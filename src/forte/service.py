"""Web service layer — turns the Bid Desk into JSON for the single-page UI.

Thin: it wires the connector data (cost library / availability / owners / inbox),
the streamed run, the distillation metrics, the editable Playbook, and the
feedback loop into serializable shapes. Executed estimates are cached to
data/forte so the UI is instant on reload.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from . import playbook, distill, feedback
from .catalog import company, rfq_summary, part_json, BID_HISTORY, backlog_series
from .worker import run_quote

_CACHE = Path("data/forte")


# --- the connector layer ---------------------------------------------------------
# In the demo every source is synthetic; the production story is the same interface
# behind Forte's real systems (Procore, Sage 300 CRE, PASSPort, the estimating inbox).
_CONNECTORS = [
    {"id": "inbox", "name": "Bid Inbox", "vendor": "estimating@ · agency portals", "kind": "inbox",
     "provides": "inbound solicitations, scope requests, addenda", "status": "connected"},
    {"id": "catalog", "name": "Cost Library", "vendor": "Estimating · assemblies & unit rates", "kind": "erp",
     "provides": "assemblies · vendors/subs · specs · schedule rate · cost", "status": "connected"},
    {"id": "inventory", "name": "Yard, Warehouse & Sub Bench", "vendor": "Holbrook · Islandia · NYC", "kind": "erp",
     "provides": "deliverable units by location · lead times", "status": "connected"},
    {"id": "crm", "name": "Owners & Contracts", "vendor": "Owner CRM · PASSPort standing", "kind": "crm",
     "provides": "rate tier · prequal standing · project history · M/WBE goals", "status": "connected"},
    {"id": "merge", "name": "Procore / Sage 300 CRE", "vendor": "Production connector", "kind": "live",
     "provides": "swap in the real project system, same interface, agent unchanged", "status": "available"},
]


def _all_quotes() -> list:
    d = company()
    out = []
    for r in d.rfqs:
        p = _CACHE / f"quote_{r.id}.json"
        if p.exists():
            out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def overview() -> dict:
    """The Home / command-center snapshot — live counts, outcomes, routing headline,
    and the aggregates the charts need."""
    d = company()
    quotes = _all_quotes()
    n = len(quotes)
    auto = sum(1 for q in quotes if q.get("decision") == "auto_execute")
    esc = sum(1 for q in quotes if q.get("decision") == "escalate")
    review = sum(1 for q in quotes if q.get("decision") == "review")
    total_value = round(sum(q.get("subtotal", 0) for q in quotes))
    avg_margin = round(sum(q.get("blended_margin", 0) for q in quotes) / n, 4) if n else 0.0
    n_subs = sum(1 for q in quotes for l in q.get("lines", []) if l.get("substituted"))
    n_lines = sum(len(q.get("lines", [])) for q in quotes)
    n_teacher = sum(q.get("routing", {}).get("teacher", 0) for q in quotes)
    r = distill.cached()
    routed, save = r.get("routed", {}), r.get("savings", {})

    by_division: dict = defaultdict(float)
    by_vendor: dict = defaultdict(float)
    mwbe_value = 0.0
    for q in quotes:
        for l in q.get("lines", []):
            if l.get("sku"):
                by_division[l["category"]] += l["extended"]
                by_vendor[l["brand"]] += l["extended"]
                if l.get("mwbe"):
                    mwbe_value += l["extended"]

    insights = []
    for q in quotes:
        if q.get("decision") != "auto_execute":
            blocked = q["verdict"].get("blocked_by") or []
            sev = "high" if "credit_hold" in blocked else "critical" if "margin_floor" in blocked else "medium"
            link = "desk"
            insights.append({"severity": sev, "icon": "alert" if sev != "medium" else "layers",
                             "title": f"{q['ref']} held for the chief estimator",
                             "amount_usd": q["subtotal"], "detail": q["verdict"]["summary"],
                             "action": "Open in Bid Desk", "link": link, "rfq": q["rfq_id"]})
    if n_subs:
        insights.append({"severity": "medium", "icon": "layers",
                         "title": f"{n_subs} unavailable assembl{'y' if n_subs == 1 else 'ies'} auto-swapped",
                         "amount_usd": None,
                         "detail": "Policy alternates from the Playbook, all within the rate tolerance.",
                         "action": "Review alternates policy", "link": "brain"})
    insights.append({"severity": "low", "icon": "bolt",
                     "title": f"{routed.get('cheap_tier_pct',0):.0f}% of scope lookups on the cheap tier",
                     "amount_usd": None,
                     "detail": f"{save.get('cost_reduction_pct',0):.0f}% lower serving cost than calling "
                               f"the frontier model on every lookup.",
                     "action": "See model routing", "link": "routing"})

    return {
        "company": d.name,
        "as_of": "2026-09-11",
        "counts": {"open_rfqs": len(d.rfqs), "skus": len(d.parts),
                   "customers": len(d.customers), "warehouses": len(d.warehouses),
                   "lines": n_lines, "teacher_lines": n_teacher},
        "quotes": {"n": n, "auto": auto, "escalate": esc, "review": review,
                   "total_value": total_value, "avg_margin": avg_margin,
                   "substitutions": n_subs,
                   "mwbe_share": round(mwbe_value / total_value, 4) if total_value else 0.0},
        "routing": {"cheap_tier_pct": routed.get("cheap_tier_pct", 0),
                    "cost_reduction_pct": save.get("cost_reduction_pct", 0),
                    "student_precision": routed.get("student_precision", 0),
                    "p50_latency_ms": routed.get("p50_latency_ms", 0),
                    "latency_speedup_x": save.get("latency_speedup_x", 0),
                    "cost_per_1k_usd": routed.get("cost_per_1k_usd", 0),
                    "teacher_cost_per_1k_usd": r.get("teacher_only", {}).get("cost_per_1k_usd", 0)},
        "policies": playbook.brain()["policies"],
        "by_division": dict(sorted(by_division.items(), key=lambda kv: -kv[1])),
        "by_vendor": dict(sorted(by_vendor.items(), key=lambda kv: -kv[1])),
        "monthly_trend": BID_HISTORY,
        "backlog": backlog_series(),
        "insights": insights,
        "recent": [{"ref": q["ref"], "rfq_id": q["rfq_id"], "customer": q["customer"]["name"],
                    "subject": q["subject"], "decision": q["decision"], "subtotal": q["subtotal"],
                    "blended_margin": q["blended_margin"], "mwbe_share": q.get("mwbe_share", 0),
                    "cheap_tier_pct": q["routing"]["cheap_tier_pct"],
                    "n_lines": len(q["lines"])} for q in quotes],
    }


def connectors() -> dict:
    d = company()
    counts = {
        "inbox": f"{len(d.rfqs)} open solicitations",
        "catalog": f"{len(d.parts)} assemblies across {len({p.category for p in d.parts})} divisions",
        "inventory": f"{len(d.stock)} availability lines · {len(d.warehouses)} locations",
        "crm": f"{len(d.customers)} owners · {len(d.history)} historical scope lines",
    }
    return {"company": d.name,
            "connectors": [{**c, "detail": counts.get(c["id"])} for c in _CONNECTORS]}


# --- the inbox ------------------------------------------------------------------

def rfqs() -> list:
    d = company()
    return [rfq_summary(d, r) for r in d.rfqs]


def rfq(rfq_id: str) -> dict:
    d = company()
    r = next((x for x in d.rfqs if x.id == rfq_id), None)
    if r is None:
        raise KeyError(rfq_id)
    c = d.customer(r.customer_id)
    return {"id": r.id, "subject": r.subject, "received_at": r.received_at,
            "body": r.body, "headline": r.headline,
            "customer": asdict(c) if c else None, "n_lines": len(r.lines)}


# --- the estimate (materialize + cache) -------------------------------------------

def quote(rfq_id: str, *, force: bool = False) -> dict:
    p = _CACHE / f"quote_{rfq_id}.json"
    if p.exists() and not force:
        return json.loads(p.read_text(encoding="utf-8"))
    out = run_quote(rfq_id)
    _CACHE.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def quotes_index() -> list:
    d = company()
    out = []
    for r in d.rfqs:
        p = _CACHE / f"quote_{r.id}.json"
        row = rfq_summary(d, r)
        if p.exists():
            q = json.loads(p.read_text(encoding="utf-8"))
            row.update({"decision": q.get("decision"), "ref": q.get("ref"),
                        "subtotal": q.get("subtotal"), "blended_margin": q.get("blended_margin"),
                        "cached": True})
        else:
            row["cached"] = False
        out.append(row)
    return out


def quotes_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ref", "bid", "owner", "subject", "decision", "subtotal_usd",
                "blended_margin", "mwbe_share", "lines"])
    for q in _all_quotes():
        w.writerow([q["ref"], q["rfq_id"], q["customer"]["name"], q["subject"], q["decision"],
                    q["subtotal"], q["blended_margin"], q.get("mwbe_share", 0), len(q["lines"])])
    return buf.getvalue()


# --- the two-tier routing metrics -----------------------------------------------

def routing() -> dict:
    return distill.cached()


def routing_run() -> dict:
    return distill.run(save=True)


# --- Playbook (editable memory) + feedback ---------------------------------------

def brain() -> dict:
    return playbook.snapshot()


def _drop_quote_cache() -> None:
    for p in _CACHE.glob("quote_*.json"):
        try:
            p.unlink()
        except Exception:
            pass


def set_policy(key: str, value) -> dict:
    playbook.set_policy(key, value)
    _drop_quote_cache()          # a moved threshold can change a decision
    return playbook.snapshot()


def reset_brain() -> dict:
    playbook.reset()
    _drop_quote_cache()
    return playbook.snapshot()


def teach(customer_id: str, phrase: str, sku: str, scope: str = "customer") -> dict:
    res = feedback.apply_correction(customer_id, phrase, sku, scope)
    _drop_quote_cache()
    return res


def demo_feedback() -> dict:
    return feedback.demonstrate()


# --- cost library / owners browse -----------------------------------------------

def catalog() -> dict:
    d = company()
    rows = []
    for p in sorted(d.parts, key=lambda x: (x.category, x.sku)):
        rows.append({**part_json(p, d.on_hand(p.sku)),
                     "warehouses": [asdict(s) for s in d.stock_for(p.sku)]})
    return {"company": d.name, "warehouses": d.warehouses, "parts": rows,
            "categories": sorted({p.category for p in d.parts})}


def customers() -> list:
    d = company()
    out = []
    for c in d.customers:
        hist = [{"sku": h.sku, "last_desc": h.last_desc, "qty": h.qty,
                 "ordered_on": h.ordered_on} for h in d.history_for(c.id)]
        out.append({**asdict(c), "discount": playbook.discount_for(c.tier), "history": hist})
    return out


def search(q: str) -> list:
    """Typeahead over bids, owners, and assemblies (for the ⌘K palette + home search)."""
    ql = (q or "").strip().lower()
    if len(ql) < 2:
        return []
    d = company()
    out = []
    for r in d.rfqs:
        c = d.customer(r.customer_id)
        hay = f"{r.id} {r.subject} {c.name if c else ''}".lower()
        if ql in hay:
            out.append({"kind": "bid", "id": r.id, "label": f"{r.id} · {c.name if c else ''}",
                        "sub": r.subject})
    for c in d.customers:
        if ql in c.name.lower():
            out.append({"kind": "owner", "id": c.id, "label": c.name,
                        "sub": f"tier {c.tier} · {c.credit_status}"})
    for p in d.parts:
        if ql in f"{p.sku} {p.name} {p.brand} {p.category}".lower():
            out.append({"kind": "assembly", "id": p.sku, "label": f"{p.sku} · {p.name}",
                        "sub": f"{p.brand} · {p.category}"})
    return out[:10]
