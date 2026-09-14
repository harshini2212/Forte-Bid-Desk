"""The submission-safety gate — what makes the Bid Desk safe to let *submit*.

A copilot can suggest a bad number and an estimator catches it. An agent that
*submits* the estimate can't rely on that — so autonomous action needs a
deterministic gate in the hot path, not a dashboard after the fact:

  * scope validity        — every priced line is a real, active library assembly
  * alternates policy     — a swapped assembly is within the approved rate tolerance
  * per-line margin floor — no single line priced below the hard margin floor
  * blended margin floor  — the bid's blended gross margin clears policy
  * owner standing        — the owner is clear to bid (no prequal / bonding hold)
  * diversity goal        — public-owner M/WBE participation meets the goal (warn)

A clean gate -> the agent auto-submits. Any BLOCK -> it holds the estimate and
escalates to the chief estimator with the exact reason. Thresholds live in the
Playbook, so a person can move the margin floor and the next run behaves differently.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

from . import playbook

PASS, WARN, BLOCK = "pass", "warn", "block"


@dataclass
class GuardResult:
    id: str
    label: str
    status: str        # pass | warn | block
    detail: str
    evidence: str = ""


@dataclass
class GateVerdict:
    decision: str          # "auto_execute" | "review" | "escalate"
    results: list          # [GuardResult]
    blocked_by: list       # [guard id]
    summary: str

    @property
    def can_auto_execute(self) -> bool:
        return self.decision == "auto_execute"


def _blended_margin(lines: list) -> float:
    rev = sum(l["net_price"] * l["qty"] for l in lines)
    cost = sum(l["unit_cost"] * l["qty"] for l in lines)
    return (rev - cost) / rev if rev else 0.0


def mwbe_share(lines: list) -> float:
    rev = sum(l["net_price"] * l["qty"] for l in lines if l.get("sku"))
    cert = sum(l["net_price"] * l["qty"] for l in lines if l.get("sku") and l.get("mwbe"))
    return cert / rev if rev else 0.0


def evaluate(lines: list, customer) -> GateVerdict:
    """`lines`: dicts with sku, name, qty, net_price, unit_cost, margin,
    substituted(bool), orig_sku, price_delta(frac), status, mwbe. `customer`: a
    catalog.Customer."""
    b = playbook.brain()["policies"]
    results: list = []

    # 1 — scope validity: nothing priced that isn't a real, active, resolved assembly
    bad = [l for l in lines if not l.get("sku") or l.get("status") == "discontinued"]
    if bad:
        results.append(GuardResult(
            "catalog_validity", "Scope validity", BLOCK,
            f"{len(bad)} line(s) are unresolved or point at a discontinued assembly.",
            "; ".join((l.get("raw") or l.get("sku") or "?") for l in bad)))
    else:
        results.append(GuardResult(
            "catalog_validity", "Scope validity", PASS,
            "Every line resolves to a real, active cost-library assembly."))

    # 2 — alternates policy: a swap must be within the approved rate tolerance
    tol = float(b["substitution_price_tolerance"])
    risky = [l for l in lines if l.get("substituted") and abs(l.get("price_delta", 0)) > tol]
    subs = [l for l in lines if l.get("substituted")]
    if risky:
        results.append(GuardResult(
            "substitution_policy", "Alternates policy", WARN,
            f"{len(risky)} alternate(s) exceed the {tol*100:.0f}% rate tolerance; "
            "confirm with the owner.",
            "; ".join(f"{l['orig_sku']}->{l['sku']} ({l['price_delta']*100:+.0f}%)" for l in risky)))
    elif subs:
        results.append(GuardResult(
            "substitution_policy", "Alternates policy", PASS,
            f"{len(subs)} alternate(s), all within the {tol*100:.0f}% rate tolerance.",
            "; ".join(f"{l['orig_sku']}->{l['sku']}" for l in subs)))
    else:
        results.append(GuardResult("substitution_policy", "Alternates policy", PASS,
                                   "No alternates on this estimate."))

    # 3 — per-line hard floor
    hard = float(b["line_margin_hard_floor"])
    thin = [l for l in lines if l.get("sku") and l["margin"] < hard]
    if thin:
        worst = min(thin, key=lambda l: l["margin"])
        results.append(GuardResult(
            "line_floor", "Per-line margin floor", BLOCK,
            f"{len(thin)} line(s) below the {hard*100:.0f}% hard floor.",
            f"worst: {worst['name']} at {worst['margin']*100:.1f}%"))
    else:
        results.append(GuardResult("line_floor", "Per-line margin floor", PASS,
                                   f"All lines clear the {hard*100:.0f}% hard floor."))

    # 4 — blended margin floor
    floor = float(b["margin_floor"])
    blended = _blended_margin([l for l in lines if l.get("sku")])
    if blended < floor:
        results.append(GuardResult(
            "margin_floor", "Blended margin floor", BLOCK,
            f"Bid blended margin {blended*100:.1f}% is below the {floor*100:.0f}% floor; "
            "needs the chief estimator's sign-off.",
            f"blended {blended*100:.1f}% vs floor {floor*100:.0f}%"))
    else:
        results.append(GuardResult(
            "margin_floor", "Blended margin floor", PASS,
            f"Blended margin {blended*100:.1f}% clears the {floor*100:.0f}% floor.",
            f"blended {blended*100:.1f}%"))

    # 5 — owner standing: prequalification / bonding must be clear
    if playbook.block_on_credit_hold() and getattr(customer, "credit_status", "ok") == "hold":
        results.append(GuardResult(
            "credit_hold", "Owner standing", BLOCK,
            f"{customer.name} is on a bid hold. Estimate may be prepared but not auto-submitted.",
            f"terms: {customer.terms}"))
    else:
        results.append(GuardResult("credit_hold", "Owner standing", PASS,
                                   "Owner is clear to bid — prequalification and bonding in good standing."))

    # 6 — diversity participation (public owners): warn, never block
    goal = float(getattr(customer, "mwbe_goal", 0.0) or 0.0)
    policy_goal = float(b.get("mwbe_goal", 0.0))
    goal = min(goal, policy_goal) if goal and policy_goal else (goal or 0.0)
    share = mwbe_share(lines)
    if goal > 0 and share < goal:
        results.append(GuardResult(
            "mwbe_goal", "M/WBE participation", WARN,
            f"Certified-vendor share {share*100:.0f}% is below the {goal*100:.0f}% goal; "
            "re-balance subs before submission.",
            f"certified {share*100:.0f}% vs goal {goal*100:.0f}%"))
    elif goal > 0:
        results.append(GuardResult(
            "mwbe_goal", "M/WBE participation", PASS,
            f"Certified-vendor share {share*100:.0f}% meets the {goal*100:.0f}% goal.",
            f"certified {share*100:.0f}%"))
    else:
        results.append(GuardResult("mwbe_goal", "M/WBE participation", PASS,
                                   "Private owner, no participation goal on this bid."))

    blocked = [r.id for r in results if r.status == BLOCK]
    warned = [r.id for r in results if r.status == WARN]
    if blocked:
        decision = "escalate"
        summary = "Held for the chief estimator: " + "; ".join(
            r.detail for r in results if r.status == BLOCK)
    elif warned:
        decision = "review"
        summary = "Prepared with items to confirm: " + "; ".join(
            r.detail for r in results if r.status == WARN)
    else:
        decision = "auto_execute"
        summary = "All guardrails clear, safe to submit automatically."
    return GateVerdict(decision, results, blocked, summary)


def verdict_json(v: GateVerdict) -> dict:
    return {"decision": v.decision, "blocked_by": v.blocked_by, "summary": v.summary,
            "results": [asdict(r) for r in v.results]}
