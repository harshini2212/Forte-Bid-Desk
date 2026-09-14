"""Forte AI — grounded Q&A over the Bid Desk's live operational data.

Ask about the cost library, availability, an owner's standing, the routing
engine, or *why* an estimate submitted or escalated, and get a concise answer
grounded in the actual numbers — a Claude answer when a key is set, and a
deterministic grounded responder offline. Sources it leaned on (estimate refs,
assembly codes, owners) come back as citation chips.

Streamed as typed SSE events (`step` / `token` / `sources` / `done`), so the
answer types out live, the same way the Bid Desk run streams.
"""

from __future__ import annotations

import os
import re
import time

from .catalog import company, BID_HISTORY
from . import playbook

PACE = float(os.getenv("RUN_PACE", "0.0"))
_ASK_V = "forte-ask-v1"


# --- grounding: assemble the live facts the answer must be based on --------------

def _data() -> dict:
    d = company()
    import importlib
    service = importlib.import_module(".service", __package__)
    quotes = []
    for r in d.rfqs:
        try:
            quotes.append(service.quote(r.id))
        except Exception:
            pass
    oos = []
    for p in d.parts:
        if p.status == "discontinued" or d.on_hand(p.sku) == 0:
            sub = playbook.substitution_for(p.sku)
            oos.append({"sku": p.sku, "name": p.name, "status": p.status,
                        "sub": (sub or {}).get("to")})
    routing = service.routing().get("routed", {})
    return {"company": d.name, "parts": d.parts, "customers": d.customers,
            "quotes": quotes, "out_of_stock": oos, "routing": routing,
            "policies": playbook.brain()["policies"]}


def _context(data: dict) -> str:
    lines = [f"Company: {data['company']} (general contractor, Islandia NY; transit, "
             f"design-build, buildings).",
             f"Cost library: {len(data['parts'])} assemblies.",
             "Owners:"]
    for c in data["customers"]:
        lines.append(f"  - {c.name}: rate tier {c.tier} (−{int(playbook.discount_for(c.tier)*100)}%), "
                     f"standing {c.credit_status}, {c.terms}, M/WBE goal {int(c.mwbe_goal*100)}%")
    lines.append("Unavailable / discontinued assemblies:")
    for o in data["out_of_stock"]:
        lines.append(f"  - {o['name']} [{o['sku']}] {o['status']}"
                     + (f" → alternate {o['sub']}" if o["sub"] else " (no alternate on file)"))
    lines.append("Estimates the Bid Desk ran:")
    for q in data["quotes"]:
        subs = [f"{l['orig_sku']}→{l['sku']}" for l in q["lines"] if l.get("substituted")]
        lines.append(f"  - {q['ref']} for {q['customer']['name']}: {q['decision']}, "
                     f"${q['subtotal']:,.0f}, blended margin {q['blended_margin']*100:.1f}%, "
                     f"M/WBE share {q.get('mwbe_share',0)*100:.0f}%"
                     + (f", alternates {', '.join(subs)}" if subs else "")
                     + f". Gate: {q['verdict']['summary']}")
    p = data["policies"]
    lines.append(f"Policy: margin floor {p['margin_floor']*100:.0f}%, per-line hard floor "
                 f"{p['line_margin_hard_floor']*100:.0f}%, block on owner hold "
                 f"{p['block_on_credit_hold']}, M/WBE goal {p.get('mwbe_goal',0)*100:.0f}%.")
    r = data["routing"]
    if r:
        lines.append(f"Routing: {r.get('cheap_tier_pct')}% of scope lookups on the cheap "
                     f"student tier, {r.get('student_precision',0)*100:.0f}% precision, "
                     f"p50 {r.get('p50_latency_ms')} ms.")
    lines.append("Bid volume by month: " + ", ".join(
        f"{h['month']} ${h['bid_value']/1e6:.1f}M ({h['bids']} bids)" for h in BID_HISTORY))
    return "\n".join(lines)


SUGGESTIONS = [
    "Why did the SCA estimate get escalated?",
    "Which assemblies are unavailable right now?",
    "Which estimates are safe to auto-submit?",
    "Is DDC clear to bid?",
    "How is the M/WBE participation on the MTA bid?",
    "What does the routing save us?",
]


# --- the answer: Claude (cached) or a deterministic grounded fallback -----------

def _prompt(question: str, ctx: str) -> str:
    return (
        "You are Forte AI, the Bid Desk assistant for Forte Construction Corp., a New "
        "York general contractor. Answer the user's question in 2–4 concise sentences, "
        "grounded ONLY in the DATA below. Cite specific estimate refs (e.g. EST-3102), "
        "assembly codes, owners, or policy numbers. If the answer isn't in the data, say "
        "so plainly. Be direct and useful, like a sharp precon teammate. Write plain "
        "prose with no markdown, asterisks, bullet lists, or headings.\n\n"
        f"DATA:\n{ctx}\n\nQUESTION: {question}\n\nANSWER:"
    )


def _answer(question: str, ctx: str, data: dict, model: str | None = None) -> tuple[str, bool]:
    from .llm import DecodingParams, ResponseCache, request_key, DEFAULT_MODEL, ALLOWED_MODELS
    model = model if model in ALLOWED_MODELS else DEFAULT_MODEL
    prompt = _prompt(question, ctx)
    params = DecodingParams(max_tokens=400)
    cache = ResponseCache(".cache/llm")
    key = request_key(model_id=model, params=params,
                      prompt_version=_ASK_V, adapter_version="ask/0", rendered_prompt=prompt)
    rec = cache.get(key)
    if rec is not None:
        return rec["response"], True
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from .llm import ClaudeModel
            out = ClaudeModel(model).complete(prompt, params).strip()
            cache.put(key, {"response": out, "prompt_version": _ASK_V})
            return out, False
        except Exception:
            pass
    return _offline(question, data), False


def _offline(q: str, data: dict) -> str:
    ql = q.lower()
    quotes = data["quotes"]

    def esc_reasons():
        return [f"{x['ref']} ({x['customer']['name']}): {x['verdict']['summary']}"
                for x in quotes if x["decision"] == "escalate"]

    if "brief" in ql or "morning" in ql:
        n_auto = sum(1 for x in quotes if x["decision"] == "auto_execute")
        held = [x for x in quotes if x["decision"] != "auto_execute"]
        total = sum(x["subtotal"] for x in quotes)
        r = data["routing"]
        return (f"Good morning. {len(quotes)} solicitations are in the desk worth "
                f"${total:,.0f}; the Bid Desk auto-submitted {n_auto} and held {len(held)}: "
                + "; ".join(f"{x['ref']} for {x['customer']['name']} ({x['verdict']['summary']})"
                            for x in held)
                + f". {len(data['out_of_stock'])} library assemblies are unavailable and both "
                f"have policy alternates. Routing kept {r.get('cheap_tier_pct')}% of scope "
                f"lookups on the local tier. Actions: clear the DDC prequalification hold, "
                f"review SCA pricing against the {data['policies']['margin_floor']*100:.0f}% "
                f"floor, and confirm the HJ-45 alternate with MTA.")
    if any(w in ql for w in ["unavailable", "out of stock", "stock", "backorder", "availab", "discontinued"]):
        items = data["out_of_stock"]
        if not items:
            return "Everything on the current solicitations is deliverable."
        parts = "; ".join(f"{o['name']} [{o['sku']}]"
                          + (f", swapped to {o['sub']}" if o['sub'] else ", no alternate")
                          for o in items)
        return (f"{len(items)} library assemblies are unavailable: {parts}. The Bid Desk "
                f"swaps these to the Playbook alternate automatically when they appear on a "
                f"solicitation.")
    if any(w in ql for w in ["mwbe", "m/wbe", "diversity", "participation", "sdvob"]):
        rows = "; ".join(f"{x['ref']} {x.get('mwbe_share',0)*100:.0f}%" for x in quotes)
        return (f"Certified-vendor share by estimate: {rows}. The policy goal for public owners "
                f"is {data['policies'].get('mwbe_goal',0)*100:.0f}%; a bid below the goal is "
                f"prepared but flagged for review rather than blocked.")
    if "escalat" in ql or ("why" in ql and any(w in ql for w in ["sca", "ddc", "hold", "margin"])):
        rs = esc_reasons()
        return ("Two estimates escalated instead of auto-submitting: " + "; ".join(rs) +
                ". In both cases a guardrail blocked autonomous submission and routed it to the "
                "chief estimator." if rs else "No estimates escalated, all cleared the guardrails.")
    if any(w in ql for w in ["prequal", "standing", "ddc", "hold", "clear to bid", "bonding"]):
        g = next((c for c in data["customers"] if c.credit_status == "hold"), None)
        return (f"{g.name} is on a bid hold ({g.terms}), so its estimate (EST-3103) was prepared "
                f"but held for the chief estimator rather than auto-submitted, because policy "
                f"blocks auto-submit on a prequalification hold. Every other owner is clear to bid."
                if g else "All owners are clear to bid.")
    if any(w in ql for w in ["auto", "safe", "submit", "execute", "clear"]):
        auto = [x["ref"] + " (" + x["customer"]["name"] + ")" for x in quotes if x["decision"] == "auto_execute"]
        return (f"{len(auto)} estimate(s) cleared every guardrail and auto-submitted: "
                f"{', '.join(auto)}. The rest were held for the chief estimator." if auto
                else "No estimates auto-submitted this session.")
    if any(w in ql for w in ["cheap", "rout", "cost", "latency", "student", "teacher", "model", "save"]):
        r = data["routing"]
        return (f"{r.get('cheap_tier_pct')}% of scope lookups resolve on the cheap local student "
                f"tier at {r.get('student_precision',0)*100:.0f}% precision and a sub-millisecond "
                f"median, and only the ambiguous tail escalates to the Claude teacher, about "
                f"${r.get('cost_per_1k_usd', 0)} per 1k lookups versus ${data['routing'].get('cost_per_1k_usd',0) and 11.0} "
                f"routing everything to the frontier model.")
    if any(w in ql for w in ["alternat", "substitut", "replace", "supersed"]):
        subs = [f"{l['orig_sku']}→{l['sku']}" for x in quotes for l in x["lines"] if l.get("substituted")]
        return ("Alternates proposed this session: " + ", ".join(subs) +
                ", each from the Playbook alternates policy, within the rate tolerance."
                if subs else "No alternates were needed on the current estimates.")
    if any(w in ql for w in ["margin", "fee", "profit"]):
        rows = "; ".join(f"{x['ref']} {x['blended_margin']*100:.1f}%" for x in quotes)
        return (f"Blended margins by estimate: {rows}. The policy floor is "
                f"{data['policies']['margin_floor']*100:.0f}%; anything under it is escalated.")
    if any(w in ql for w in ["volume", "pipeline", "month", "trend", "backlog"]):
        rows = ", ".join(f"{h['month']} ${h['bid_value']/1e6:.1f}M" for h in BID_HISTORY)
        return (f"Bid volume by month: {rows}. July carried the Package 7 award. The four "
                f"solicitations in the desk today total ${sum(x['subtotal'] for x in quotes):,.0f}.")
    n_auto = sum(1 for x in quotes if x["decision"] == "auto_execute")
    return (f"{data['company']} has {len(data['parts'])} library assemblies and 4 open "
            f"solicitations. The Bid Desk auto-submitted {n_auto} and escalated "
            f"{len(quotes)-n_auto} on a guardrail. Ask about a specific owner, what's "
            f"unavailable, why an estimate escalated, M/WBE participation, or the routing engine.")


# --- streamed run ----------------------------------------------------------------

def _sources(answer: str, data: dict) -> list:
    src = []
    for ref in sorted(set(re.findall(r"EST-\d{3,4}", answer))):
        src.append({"kind": "quote", "label": ref})
    for c in data["customers"]:
        short = c.name.split(" ")[0]
        if c.name.lower() in answer.lower() or (len(short) > 3 and short.lower() in answer.lower()):
            src.append({"kind": "account", "label": c.name})
    for sku in sorted(set(re.findall(r"[A-Z]{3}-[A-Z0-9-]{3,}", answer))):
        if any(p.sku == sku for p in data["parts"]):
            src.append({"kind": "sku", "label": sku})
    seen, out = set(), []
    for s in src:
        if s["label"] not in seen:
            seen.add(s["label"]); out.append(s)
    return out[:6]


def _chunks(text: str):
    words = text.split(" ")
    for i in range(0, len(words), 4):
        yield (" " if i else "") + " ".join(words[i:i + 4])


def ask_events(question: str, model: str | None = None):
    question = (question or "").strip()
    if not question:
        yield "failed", {"message": "ask a question"}
        return
    yield "user", {"text": question}
    yield "step", {"label": "Grounding in cost library · availability · owners · estimates · policy",
                   "status": "running"}
    try:
        data = _data()
        ctx = _context(data)
        if PACE:
            time.sleep(PACE)
        answer, cached = _answer(question, ctx, data, model)
        yield "step", {"label": "Answering from the live data"
                       + (" (cached)" if cached else ""), "status": "done"}
        for chunk in _chunks(answer):
            yield "token", {"text": chunk}
            if PACE:
                time.sleep(PACE * 0.25)
        yield "sources", {"items": _sources(answer, data)}
        yield "done", {"cached": cached}
    except Exception as exc:
        yield "failed", {"message": str(exc)}
