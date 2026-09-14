"""Two-tier scope identification — the engineering heart of the Bid Desk.

An estimator's first job on any solicitation is mapping messy scope lines ("the
canopy extension we did at Kings Highway", "3x HJ-40 jacks") to priced
assemblies in the cost library. A cheap, fast **student** handles the clean
majority in milliseconds for ~$0, and only the ambiguous tail **escalates to the
teacher** (a frontier model) for real reasoning. Heavy reasoning in the cloud,
lightweight matching at the edge.

  * student  — a hybrid retriever: a distilled MiniLM (BERT) sentence-embedding
               model for semantic recall, blended with lexical + spec signals for
               exact size and model-number discrimination (raw embeddings are weak
               at HJ-40 vs HJ-45). Served locally via ONNX (fastembed), so the tier
               stays fast, free, and offline. Falls back to pure lexical if the
               model isn't installed.
  * teacher  — Claude, given the messy line + the owner's project history + the
               top candidates, resolves the assembly with a short rationale. Cached;
               with neither key nor cache, a deterministic teacher-sim uses project
               history (context the student never sees), a genuine capability gap.

Routing rule: accept the student when it is confident *and* clearly ahead of the
runner-up; otherwise escalate. `distill.py` calibrates the threshold and reports
the tier mix / precision / cost / latency this routing achieves.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from . import playbook
from .catalog import Company, Part

# Confidence at/above which the student is trusted without escalation, and the
# minimum lead over the runner-up. Calibrated in distill.py; committed defaults.
STUDENT_ACCEPT = 0.58
STUDENT_MIN_GAP = 0.10

# Frontier-model pricing for the teacher-cost story ($/1M tokens).
_TEACHER_IN_PER_M = 15.00
_TEACHER_OUT_PER_M = 75.00


@dataclass
class Candidate:
    sku: str
    score: float
    part: Part


@dataclass
class Resolution:
    raw: str
    qty: int
    sku: str | None
    part: Part | None
    confidence: float
    tier: str                 # "student" | "teacher" | "memory"
    escalated: bool
    rationale: str
    candidates: list = field(default_factory=list)   # [Candidate] top-K
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    source_note: str = ""     # e.g. "matched from Playbook synonym"


# --- tokenization: normalize the messy ways people write sizes / specs -----------

_SIZE_MAP = {
    "1/2": {"1/2", "1/2in", "0.5", ".5", "0.5in", "half"},
    "3/4": {"3/4", "3/4in", "0.75", ".75", "0.75in"},
    "1": {"1in"},
    "2": {"2in"},
    "4": {"4in"},
    "6": {"6in"},
    "8": {"8in"},
    "1-1/2": {"1-1/2", "1-1/2in", "1.5", "1.5in", "1 1/2"},
}
_LEAD_QTY = re.compile(r"^\s*\d[\d,]*\s*(?:x\b|x\s|ea\b|pcs\b|units\b|\s)\s*")
_UNITS = {"sf", "lf", "ton", "tons", "each", "ea", "case", "cases", "box", "boxes",
          "drum", "drums", "month", "months"}


def _tokens(text: str) -> set:
    t = (text or "").lower()
    t = _LEAD_QTY.sub("", t)                              # "200x 3/4in ..." -> "3/4in ..."
    t = re.sub(r'(\d+(?:/\d+)?)\s*(?:inches|inch|in\b|")', r"\1in", t)   # 8 inch -> 8in
    t = re.sub(r"(\d)\s*x\s+", r"\1 ", t)                # "40x " -> "40 "
    t = re.sub(r"[^a-z0-9/\-\.# ]", " ", t)
    words = [w for w in t.split() if w]
    toks = set()
    for w in words:
        if w.isdigit():
            if 2 <= len(w) <= 4:                          # 40, 400, 4000 — spec numbers
                toks.add(w)
        elif w not in _UNITS:
            toks.add(w)
    allw = set(words)
    for canon, variants in _SIZE_MAP.items():            # canonical size tokens
        if allw & variants:
            toks.add(f"sz:{canon}")
    for m in re.findall(r"\b[a-z]{1,3}-?\d{2,3}\b", t):  # model numbers hj-40, fr-20, w12
        toks.add(m.replace("-", ""))
    return toks


# spec keys that describe *other* assemblies (supersession/fitment), not this one's
# own identity — excluded from the student surface so a query for "FR-20" doesn't
# also light up "FR-22 (replaces FR-20)". The teacher + alternates policy own that.
_XREF_SPEC_KEYS = {"supersedes", "replaces", "replaced_by", "fits"}


def _part_surface(p: Part) -> set:
    name = re.sub(r"\(.*?\)", " ", p.name)   # drop parentheticals from the name
    own_specs = [str(v) for k, v in p.specs.items() if k not in _XREF_SPEC_KEYS]
    toks = _tokens(" ".join([name, p.brand, p.category, " ".join(p.keywords)] + own_specs))
    toks.add(p.sku.lower())
    return toks


_QTY_RE = re.compile(r"(\d[\d,]*)\s*(?:x|@|ea|pcs|pieces|units|sf|lf|ton|tons)?\b", re.I)


def parse_qty(line: str) -> int:
    m = _QTY_RE.search(line.strip())
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return 1


# --- the student: hybrid MiniLM (BERT) retriever + lexical/spec signals ----------

EMB_WEIGHT = 0.9
_MATERIALS = {"steel", "concrete", "copper", "aluminum", "porcelain", "galvanized",
              "cmu", "brass", "stainless", "orange"}
_EMB_OK = None               # None = untried; True/False after first attempt
_EMBEDDER = None
_PART_VECS: dict = {}        # sku -> L2-normalized embedding


def _embedder():
    """Lazily load the distilled MiniLM (all-MiniLM-L6-v2) via fastembed/ONNX.
    Returns None if unavailable, so the ranker degrades to pure lexical."""
    global _EMB_OK, _EMBEDDER
    if _EMB_OK is None:
        try:
            from fastembed import TextEmbedding
            _EMBEDDER = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
            _EMB_OK = True
        except Exception:
            _EMB_OK = False
    return _EMBEDDER if _EMB_OK else None


def embedder_available() -> bool:
    return _embedder() is not None


def _part_text(p: Part) -> str:
    own = [str(v) for k, v in p.specs.items() if k not in _XREF_SPEC_KEYS]
    name = re.sub(r"\(.*?\)", " ", p.name)
    return f"{name}. {p.brand} {p.category}. " + " ".join(own + p.keywords)


def _embed(texts):
    import numpy as np
    em = _embedder()
    if em is None:
        return None
    out = []
    for v in em.embed(list(texts)):
        v = np.asarray(v, dtype="float32")
        out.append(v / (float(np.linalg.norm(v)) + 1e-9))
    return out


def _embed_sims(d: Company, line: str):
    """Cosine similarity of the query against every assembly (sku->sim), or None."""
    import numpy as np
    if not _PART_VECS and _embedder() is not None:
        vecs = _embed([_part_text(p) for p in d.parts])
        if vecs is not None:
            for p, v in zip(d.parts, vecs):
                _PART_VECS[p.sku] = v
    if not _PART_VECS:
        return None
    qv = _embed([line])
    if not qv:
        return None
    q = qv[0]
    return {sku: float(np.dot(q, v)) for sku, v in _PART_VECS.items()}


def warmup(d: Company | None = None):
    """Pre-load the model + assembly vectors so the first live lookup is fast."""
    from .catalog import company
    _embed_sims(d or company(), "warmup")


def student_rank(d: Company, line: str, k: int = 4) -> list:
    q = _tokens(line)
    sims = _embed_sims(d, line)                    # semantic recall (None w/o model)
    model_q = {t for t in q if re.fullmatch(r"[a-z]{1,3}\d{2,3}", t)}
    scored = []
    for p in d.parts:
        overlap = q & _part_surface(p)
        cos = sims.get(p.sku, 0.0) if sims else 0.0
        if not overlap and cos < 0.40:             # no lexical and no semantic signal
            continue
        base = (len(overlap) / (len(q) ** 0.5)) if q else 0.0
        size_hit = any(t.startswith("sz:") for t in overlap)
        mat_hit = bool(overlap & _MATERIALS)
        cat_hit = bool(_tokens(p.category) & q)
        model_hit = bool(overlap & model_q)
        score = (base + 1.0 * size_hit + 0.55 * mat_hit + 0.3 * cat_hit + 1.5 * model_hit
                 + EMB_WEIGHT * cos)
        if p.status == "discontinued":             # findable, but shouldn't win outright
            score *= 0.9
        scored.append(Candidate(p.sku, round(score, 3), p))
    scored.sort(key=lambda c: -c.score)
    return scored[:k]


def _confidence(cands: list) -> tuple:
    """Map raw scores -> a calibrated [0,1] confidence + the gap to runner-up."""
    if not cands:
        return 0.0, 0.0
    top = cands[0].score
    runner = cands[1].score if len(cands) > 1 else 0.0
    conf = top / (top + 1.4)
    gap = (top - runner) / top if top else 0.0
    return round(conf, 3), round(gap, 3)


# --- the teacher: Claude, with project-history context the student lacks --------

_PROMPT_VERSION = "forte-scope-teacher-v1"


def _teacher_prompt(line: str, cands: list, history: list, cust_name: str) -> str:
    cand_lines = "\n".join(
        f"  - {c.part.sku}: {c.part.name} [{c.part.brand}, {c.part.category}] "
        f"specs={json.dumps(c.part.specs)} status={c.part.status}"
        for c in cands) or "  (no strong library candidates)"
    hist_lines = "\n".join(
        f"  - {h.ordered_on}: delivered {h.qty}x {h.sku} — the owner called it \"{h.last_desc}\""
        for h in history) or "  (no prior projects on file)"
    return (
        "You are a senior estimator at Forte Construction, a New York general "
        "contractor (transit, design-build, buildings). An owner's project manager "
        "wrote a scope line on a bid solicitation. Resolve it to exactly one "
        "cost-library assembly, using the owner's project history when the line is "
        "vague (e.g. \"the one we did at the last station\").\n\n"
        f"Owner: {cust_name}\n"
        f"Scope line: \"{line}\"\n\n"
        f"Top library candidates:\n{cand_lines}\n\n"
        f"This owner's recent project history with Forte:\n{hist_lines}\n\n"
        "Reason about size, model number, material, division, and history, then answer. "
        "Respond with ONLY a JSON object: "
        '{"sku": "<the chosen assembly code, or null if truly unresolvable>", '
        '"confidence": <0..1>, "rationale": "<one sentence>"}.'
    )


def _est_cost(prompt: str, out: str) -> float:
    tin = len(prompt) / 4.0
    tout = len(out) / 4.0
    return round(tin / 1e6 * _TEACHER_IN_PER_M + tout / 1e6 * _TEACHER_OUT_PER_M, 6)


def teacher_resolve(d: Company, line: str, cands: list, customer_id: str) -> dict:
    """Ask the teacher (Claude, cached) to resolve the line. Degrades to a
    deterministic history-aware teacher-sim when there's no key and no cache."""
    from .llm import DecodingParams, ResponseCache, request_key, DEFAULT_MODEL

    cust = d.customer(customer_id)
    history = d.history_for(customer_id)
    prompt = _teacher_prompt(line, cands, history, cust.name if cust else customer_id)
    params = DecodingParams(max_tokens=300)
    cache = ResponseCache(".cache/llm")
    key = request_key(model_id=DEFAULT_MODEL, params=params,
                      prompt_version=_PROMPT_VERSION, adapter_version="teacher/0",
                      rendered_prompt=prompt)
    rec = cache.get(key)
    if rec is not None:
        return {**_parse_teacher(rec["response"], cands), "cost_usd": rec.get("cost", 0.0),
                "cached": True}

    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from .llm import ClaudeModel
            out = ClaudeModel(DEFAULT_MODEL).complete(prompt, params)
            cost = _est_cost(prompt, out)
            cache.put(key, {"response": out, "cost": cost, "prompt_version": _PROMPT_VERSION})
            return {**_parse_teacher(out, cands), "cost_usd": cost, "cached": False}
        except Exception:
            pass  # fall through to the offline sim
    return {**_teacher_offline(d, line, cands, history), "cost_usd": 0.0, "cached": False}


def _parse_teacher(out: str, cands: list) -> dict:
    try:
        m = re.search(r"\{.*\}", out, re.S)
        obj = json.loads(m.group(0)) if m else {}
        sku = obj.get("sku")
        valid = {c.sku for c in cands}
        if sku not in valid and cands:
            sku = cands[0].sku
        return {"sku": sku, "confidence": float(obj.get("confidence", 0.8)),
                "rationale": obj.get("rationale", "teacher-resolved")}
    except Exception:
        return {"sku": cands[0].sku if cands else None, "confidence": 0.7,
                "rationale": "teacher-resolved (unparsed)"}


def _teacher_offline(d: Company, line: str, cands: list, history: list) -> dict:
    """Deterministic stand-in for the teacher: resolves vague lines via the owner's
    project history (which the student never gets), which is exactly why
    escalation helps. Never consults gold labels."""
    q = _tokens(line)
    for h in history:
        hq = _tokens(h.last_desc)
        distinctive = hq - {"the", "we", "did", "at", "last", "time", "our", "usual", "same", "x"}
        if distinctive and len(q & distinctive) >= max(2, len(distinctive) // 2):
            part = d.part(h.sku)
            return {"sku": h.sku, "confidence": 0.9,
                    "rationale": f"Matches this owner's prior scope "
                                 f"{part.name if part else h.sku} (\"{h.last_desc}\")."}
    if cands:
        c = cands[0]
        return {"sku": c.sku, "confidence": 0.82,
                "rationale": f"Best spec match on {', '.join(list(c.part.specs.values())[:3])}."}
    return {"sku": None, "confidence": 0.0, "rationale": "No library match found."}


# --- the router: student first, escalate the ambiguous tail ----------------------

def resolve_line(d: Company, line: str, customer_id: str,
                 *, force_no_memory: bool = False) -> Resolution:
    qty = parse_qty(line)

    # tier 0 — Playbook memory: a learned synonym / owner preference is an
    # instant, free, exact hit (this is what feedback turns 'ambiguous' into).
    if not force_no_memory:
        pref = playbook.customer_pref(customer_id, _dephrase(line))
        syn = pref or playbook.synonym_for(_dephrase(line))
        if syn and d.part(syn):
            return Resolution(line, qty, syn, d.part(syn), 0.99, "memory", False,
                              "Resolved from the Playbook " +
                              ("owner preference" if pref else "synonym") +
                              ", learned from prior human feedback.",
                              source_note="playbook")

    t0 = time.perf_counter()
    cands = student_rank(d, line)
    conf, gap = _confidence(cands)
    student_ms = (time.perf_counter() - t0) * 1000.0

    student_ok = bool(cands) and conf >= STUDENT_ACCEPT and gap >= STUDENT_MIN_GAP
    # A confident match that happens to be discontinued or unavailable is still a
    # correct *identification* — availability is a separate concern the alternates
    # policy owns downstream. Only genuinely ambiguous lines escalate.
    if student_ok:
        c = cands[0]
        return Resolution(line, qty, c.sku, c.part, conf, "student", False,
                          f"High-confidence hybrid match — MiniLM embedding + spec "
                          f"signals (conf {conf:.2f}, lead {gap:.2f} over runner-up).",
                          candidates=cands, latency_ms=round(student_ms, 2), cost_usd=0.0)

    t1 = time.perf_counter()
    res = teacher_resolve(d, line, cands, customer_id)
    teacher_ms = (time.perf_counter() - t1) * 1000.0
    sku = res.get("sku")
    return Resolution(line, qty, sku, d.part(sku) if sku else None,
                      float(res.get("confidence", 0.8)), "teacher", True,
                      res.get("rationale", "teacher-resolved"),
                      candidates=cands,
                      latency_ms=round(student_ms + teacher_ms, 2),
                      cost_usd=res.get("cost_usd", 0.0),
                      source_note="cached-teacher" if res.get("cached") else "teacher")


def _dephrase(line: str) -> str:
    """Strip the leading quantity so 'orange traffic cones' matches a taught synonym."""
    return _LEAD_QTY.sub("", line, count=1).strip(" -x").strip()


def resolution_json(r: Resolution) -> dict:
    return {
        "raw": r.raw, "qty": r.qty, "sku": r.sku,
        "part": {"sku": r.part.sku, "name": r.part.name, "brand": r.part.brand,
                 "category": r.part.category, "status": r.part.status,
                 "list_price": r.part.list_price, "unit_cost": r.part.unit_cost,
                 "uom": r.part.uom, "mwbe": r.part.mwbe}
        if r.part else None,
        "confidence": round(r.confidence, 3), "tier": r.tier, "escalated": r.escalated,
        "rationale": r.rationale, "latency_ms": r.latency_ms, "cost_usd": r.cost_usd,
        "source_note": r.source_note,
        "candidates": [{"sku": c.sku, "name": c.part.name, "score": c.score}
                       for c in r.candidates],
    }
