"""The Bid Desk run — a system of *action*, streamed step by step.

Not "answer a question about an estimate" but "*produce and submit* the
estimate." The agent reads an inbound solicitation and executes Forte's precon
workflow end-to-end:

    parse -> identify scope (student/teacher routing) -> check availability ->
    swap unavailable/retired assemblies -> price against the owner's rate tier ->
    clear the guardrail gate -> EXECUTE (write the estimate to the project system +
    draft the proposal letter) or ESCALATE to the chief estimator with the reason.

A workflow is a generator that yields typed events as it computes; the SSE
endpoint forwards them so the UI trace ticks live, and `run_quote` drains the
same generator to a dict for the cached/headless path — one definition, no fork.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import playbook, guardrails
from .catalog import company, Part
from .scope import resolve_line, resolution_json

PACE = float(os.getenv("RUN_PACE", "0.0"))     # >0 drips events for a live-feel demo
_EST_DIR = Path(".cache/forte_estimates")       # the "project system" the agent writes to


def _pace(mult: float = 1.0) -> None:
    if PACE:
        time.sleep(PACE * mult)


@dataclass
class QuoteLine:
    raw: str
    qty: int
    sku: str | None
    name: str
    brand: str
    category: str
    unit_cost: float
    list_price: float
    discount: float
    net_price: float
    extended: float
    margin: float
    resolve_tier: str        # student | teacher | memory
    confidence: float
    escalated: bool
    rationale: str
    on_hand: int
    warehouse: str
    lead_time_days: int
    status: str = "active"
    uom: str = "each"
    mwbe: str = ""
    substituted: bool = False
    orig_sku: str | None = None
    orig_name: str | None = None
    price_delta: float = 0.0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    flags: list = field(default_factory=list)

    def guard_dict(self) -> dict:
        return {"raw": self.raw, "sku": self.sku, "name": self.name, "qty": self.qty,
                "net_price": self.net_price, "unit_cost": self.unit_cost,
                "margin": self.margin, "status": self.status, "mwbe": self.mwbe,
                "substituted": self.substituted, "orig_sku": self.orig_sku,
                "price_delta": self.price_delta}


def _price(part: Part, discount: float) -> tuple:
    net = round(part.list_price * (1 - discount), 2)
    margin = (net - part.unit_cost) / net if net else 0.0
    return net, round(margin, 4)


def _best_stock(d, sku: str) -> tuple:
    lines = d.stock_for(sku)
    if not lines:
        return 0, "—", 0
    best = max(lines, key=lambda s: s.on_hand)
    return d.on_hand(sku), best.warehouse, best.lead_time_days


def _make_line(d, res, discount: float) -> QuoteLine:
    """Turn a scope Resolution into a priced line, applying the alternates policy
    when the resolved assembly is unavailable or discontinued."""
    part = res.part
    substituted = False
    orig_sku = orig_name = None
    price_delta = 0.0
    flags = []

    if part is not None:
        on_hand = d.on_hand(part.sku)
        needs_sub = part.status == "discontinued" or on_hand == 0
        if needs_sub:
            sub = playbook.substitution_for(part.sku)
            if sub and d.part(sub["to"]):
                new = d.part(sub["to"])
                substituted = True
                orig_sku, orig_name = part.sku, part.name
                price_delta = round((new.list_price - part.list_price) / part.list_price, 4) \
                    if part.list_price else 0.0
                flags.append("substituted:" + ("discontinued" if part.status == "discontinued"
                                               else "out_of_stock"))
                part = new
            else:
                flags.append("backorder")

    if part is None:
        return QuoteLine(res.raw, res.qty, None, "(unresolved)", "", "", 0, 0, discount,
                         0, 0, 0, res.tier, res.confidence, res.escalated,
                         res.rationale, 0, "—", 0, "unresolved",
                         latency_ms=res.latency_ms, cost_usd=res.cost_usd,
                         flags=["unresolved"])

    net, margin = _price(part, discount)
    on_hand, wh, lead = _best_stock(d, part.sku)
    return QuoteLine(
        raw=res.raw, qty=res.qty, sku=part.sku, name=part.name, brand=part.brand,
        category=part.category, unit_cost=part.unit_cost, list_price=part.list_price,
        discount=discount, net_price=net, extended=round(net * res.qty, 2), margin=margin,
        resolve_tier=res.tier, confidence=res.confidence, escalated=res.escalated,
        rationale=res.rationale, on_hand=on_hand, warehouse=wh, lead_time_days=lead,
        status=part.status, uom=part.uom, mwbe=part.mwbe, substituted=substituted,
        orig_sku=orig_sku, orig_name=orig_name, price_delta=price_delta,
        latency_ms=res.latency_ms, cost_usd=res.cost_usd, flags=flags)


def _proposal_letter(d, rfq, cust, lines, subtotal, decision) -> str:
    """Draft the owner-facing proposal cover letter (deterministic template; the
    same shape an estimator would send). Reads as a real submission, not a chat answer."""
    ref = f"EST-{rfq.id.split('-')[-1]}"
    rows = []
    for l in lines:
        if not l.sku:
            rows.append(f"  • {l.raw}: needs clarification, we'll follow up")
            continue
        note = ""
        if l.substituted:
            note = f"  (alternate for {l.orig_name}; {l.orig_sku} unavailable)"
        rows.append(f"  • {l.qty:,} {l.uom} × {l.name} [{l.sku}] at ${l.net_price:,.2f}"
                    f" = ${l.extended:,.2f}{note}")
    body = "\n".join(rows)
    lead = (f"Thank you for the solicitation. Estimate {ref} is attached. Pricing reflects "
            f"your tier-{cust.tier} rate schedule, and every assembly below is deliverable "
            f"within the bid schedule.")
    if decision == "review":
        lead = (f"Thank you for the solicitation. Estimate {ref} is ready with a couple of "
                f"items to confirm (noted below) before we release it.")
    if decision == "escalate":
        lead = (f"Thank you for the solicitation. We're preparing estimate {ref}. One item "
                f"needs a quick check on our side and your Forte project executive will "
                f"follow up shortly.")
    return (f"To: {cust.email}\nSubject: Re: {rfq.subject} · Estimate {ref}\n\n"
            f"Hi {cust.contact.split(',')[0]},\n\n{lead}\n\n{body}\n\n"
            f"Subtotal: ${subtotal:,.2f} ({cust.terms})\n\n"
            f"Reply here to release the estimate into the design-build cost model and "
            f"we'll confirm the schedule.\n\n"
            f"Forte AI · Bid Desk\nForte Construction Corp. · 1770 Motor Parkway, Islandia, NY")


def _execute(d, rfq, cust, quote: dict) -> dict:
    """The action: persist the estimate to the project system. A real state change
    + artifact, not a printed answer. Returns the write receipt."""
    _EST_DIR.mkdir(parents=True, exist_ok=True)
    ref = quote["ref"]
    (_EST_DIR / f"{ref}.json").write_text(json.dumps(quote, indent=2), encoding="utf-8")
    return {"ref": ref, "system": "Procore · Estimates", "status": "written",
            "path": str(_EST_DIR / f"{ref}.json")}


# --- the streamed run ------------------------------------------------------------

def quote_events(rfq_id: str):
    d = company()
    rfq = next((r for r in d.rfqs if r.id == rfq_id), None)
    run_id = "r_" + uuid.uuid4().hex[:6]
    t0 = time.time()
    if rfq is None:
        yield "failed", {"message": f"unknown bid {rfq_id!r}"}
        return
    cust = d.customer(rfq.customer_id)
    discount = playbook.discount_for(cust.tier)
    yield "run_started", {"run_id": run_id, "workflow": "estimate", "rfq": rfq.id,
                          "customer": cust.name, "tier": cust.tier,
                          "subject": rfq.subject}
    try:
        # 1 — read the solicitation from the bid inbox connector
        yield "step", {"id": "parse", "label": f"Read {rfq.id} from {cust.name}",
                       "status": "running"}
        _pace()
        yield "count", {"label": "Scope lines", "n": len(rfq.lines)}
        yield "step", {"id": "parse", "label": f"Parsed {len(rfq.lines)} scope lines",
                       "status": "done"}

        # 2 — identify each assembly (student -> teacher routing)
        yield "step", {"id": "identify", "label": "Identify scope (student → teacher routing)",
                       "status": "running"}
        lines: list = []
        n_student = n_teacher = n_memory = 0
        total_cost = 0.0
        for ln in rfq.lines:
            res = resolve_line(d, ln.raw, rfq.customer_id)
            total_cost += res.cost_usd
            n_student += res.tier == "student"
            n_teacher += res.tier == "teacher"
            n_memory += res.tier == "memory"
            ql = _make_line(d, res, discount)
            lines.append(ql)
            yield "resolve", {**resolution_json(res),
                              "final_sku": ql.sku, "substituted": ql.substituted,
                              "orig_sku": ql.orig_sku, "on_hand": ql.on_hand,
                              "warehouse": ql.warehouse}
            _pace(1.3 if res.escalated else 0.5)
        yield "step", {"id": "identify",
                       "label": f"Identified {len([l for l in lines if l.sku])}/{len(lines)} "
                                f"· {n_student} student · {n_teacher} teacher · {n_memory} memory",
                       "status": "done"}
        yield "metric", {"key": "cheap_tier", "label": "Resolved on cheap tier",
                         "value": round((n_student + n_memory) / max(len(lines), 1) * 100),
                         "unit": "%"}
        if total_cost:
            yield "metric", {"key": "resolve_cost", "label": "Identification cost",
                             "value": round(total_cost * 1000, 3), "unit": "¢/1k·approx"}

        # 3 — availability + alternates
        yield "step", {"id": "inventory", "label": "Check availability & propose alternates",
                       "status": "running"}
        _pace()
        n_sub = 0
        for l in lines:
            if l.substituted:
                n_sub += 1
                yield "finding", {"severity": "medium",
                                  "text": f"Alternate: {l.orig_name} → {l.name}",
                                  "detail": f"{l.orig_sku} unavailable "
                                            f"({'discontinued' if 'discontinued' in ''.join(l.flags) else 'factory backorder'}); "
                                            f"policy alternate is {l.sku} "
                                            f"({l.price_delta*100:+.0f}% rate).",
                                  "evidence": playbook.substitution_for(l.orig_sku)["reason"]
                                  if playbook.substitution_for(l.orig_sku) else ""}
            elif "backorder" in l.flags:
                yield "finding", {"severity": "high",
                                  "text": f"No availability or alternate for {l.name}",
                                  "detail": "Line flagged for an estimator. No policy alternate on file.",
                                  "evidence": l.sku or l.raw}
        yield "step", {"id": "inventory",
                       "label": f"Availability checked, {n_sub} alternate(s) proposed",
                       "status": "done"}

        # 4 — price against the owner's rate tier
        yield "step", {"id": "price", "label": f"Price at {cust.name}'s tier-{cust.tier} "
                                               f"rate schedule (−{discount*100:.0f}%)",
                       "status": "running"}
        _pace()
        priced = [l for l in lines if l.sku]
        subtotal = round(sum(l.extended for l in priced), 2)
        blended = (sum(l.net_price * l.qty for l in priced) -
                   sum(l.unit_cost * l.qty for l in priced))
        blended_margin = round(blended / sum(l.net_price * l.qty for l in priced), 4) \
            if priced else 0.0
        for l in priced:
            yield "cell", {"sku": l.sku, "name": l.name, "qty": l.qty,
                           "net_price": l.net_price, "extended": l.extended,
                           "margin": round(l.margin, 4),
                           "thin": l.margin < playbook.line_hard_floor()}
        yield "metric", {"key": "subtotal", "label": "Estimate subtotal", "value": subtotal,
                         "unit": "$"}
        yield "metric", {"key": "blended_margin", "label": "Blended margin",
                         "value": round(blended_margin * 100, 1), "unit": "%",
                         "flag": "danger" if blended_margin < playbook.margin_floor() else None}
        share = guardrails.mwbe_share([l.guard_dict() for l in lines])
        yield "metric", {"key": "mwbe", "label": "M/WBE participation",
                         "value": round(share * 100), "unit": "%"}
        yield "step", {"id": "price", "label": f"Priced {len(priced)} lines, "
                                               f"${subtotal:,.0f} subtotal", "status": "done"}

        # 5 — the guardrail gate
        yield "step", {"id": "guardrails", "label": "Run submission guardrails", "status": "running"}
        verdict = guardrails.evaluate([l.guard_dict() for l in lines], cust)
        for r in verdict.results:
            yield "guard", {"id": r.id, "label": r.label, "status": r.status,
                            "detail": r.detail, "evidence": r.evidence}
            _pace(0.7)
        yield "step", {"id": "guardrails",
                       "label": f"Guardrails: {verdict.decision.replace('_', ' ')}",
                       "status": "done"}

        # 6 — act: execute or escalate
        ref = f"EST-{rfq.id.split('-')[-1]}"
        letter = _proposal_letter(d, rfq, cust, lines, subtotal, verdict.decision)
        quote = _quote_dict(rfq, cust, lines, subtotal, blended_margin, share, verdict,
                            letter, ref, total_cost, n_student, n_teacher, n_memory)
        if verdict.can_auto_execute:
            yield "step", {"id": "execute", "label": "Execute: write estimate to Procore + draft proposal",
                           "status": "running"}
            _pace()
            receipt = _execute(d, rfq, cust, quote)
            quote["execution"] = receipt
            yield "action", {"kind": "executed", "ref": ref, "system": receipt["system"],
                             "detail": f"Estimate {ref} written to Procore and the proposal letter drafted.",
                             "email": letter}
            yield "step", {"id": "execute", "label": f"Executed: estimate {ref} submitted", "status": "done"}
        else:
            reason = verdict.summary
            quote["execution"] = {"ref": ref, "status": "held", "reason": reason}
            yield "step", {"id": "execute", "label": "Escalate to the chief estimator (guardrail hold)",
                           "status": "running"}
            _pace()
            yield "action", {"kind": "escalated", "ref": ref,
                             "system": "Chief estimator review queue",
                             "detail": reason,
                             "email": letter,
                             "blocked_by": verdict.blocked_by}
            yield "step", {"id": "execute",
                           "label": f"Escalated: estimate {ref} held for review", "status": "done"}

        yield "result", quote
        yield "done", {"run_id": run_id, "decision": verdict.decision,
                       "ref": ref, "elapsed_ms": int((time.time() - t0) * 1000)}
    except Exception as exc:
        yield "failed", {"message": str(exc)}


def _quote_dict(rfq, cust, lines, subtotal, blended_margin, mwbe, verdict, letter, ref,
                cost, n_student, n_teacher, n_memory) -> dict:
    return {
        "ref": ref, "rfq_id": rfq.id, "subject": rfq.subject,
        "customer": {"id": cust.id, "name": cust.name, "contact": cust.contact,
                     "email": cust.email, "tier": cust.tier, "terms": cust.terms,
                     "credit_status": cust.credit_status, "mwbe_goal": cust.mwbe_goal},
        "headline": rfq.headline,
        "lines": [asdict(l) for l in lines],
        "subtotal": subtotal, "blended_margin": blended_margin, "mwbe_share": round(mwbe, 4),
        "decision": verdict.decision, "verdict": guardrails.verdict_json(verdict),
        "routing": {"student": n_student, "teacher": n_teacher, "memory": n_memory,
                    "cheap_tier_pct": round((n_student + n_memory) / max(len(lines), 1) * 100),
                    "identify_cost_usd": round(cost, 6)},
        "follow_up_email": letter,
    }


def run_quote(rfq_id: str) -> dict:
    """Drain the same generator to a dict — the cached / headless path."""
    out: dict = {}
    for event, payload in quote_events(rfq_id):
        if event == "result":
            out = payload
        elif event == "failed":
            raise RuntimeError(payload["message"])
    return out
