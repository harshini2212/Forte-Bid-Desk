"""The Playbook — Forte's editable estimating memory.

The company-specific knowledge the Bid Desk agent reasons and acts on: pricing
rules, scope synonyms learned from usage, the alternates policy (what supersedes
what), the owner rate schedules, and the guardrail thresholds that make
autonomous submission safe.

It is **editable**: the UI reads it, a person can change a rule or teach a
synonym, and the *next* run behaves differently. That feedback loop — human
judgment curating the memory that drives execution — is the learning layer in
miniature. State persists to a JSON file so an edit survives a reload; delete the
file to reset to the seeded defaults below.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

# Persist outside the source tree so edits survive redeploys of the same disk but
# don't dirty the repo. Falls back to the seeded defaults when absent.
_STORE = Path(os.environ.get("PLAYBOOK_PATH", str(Path(".cache") / "playbook.json")))


# --- the seeded memory (a real GC's estimating knowledge, curated) ---------------
DEFAULTS: dict = {
    "policies": {
        # the money guardrail: never submit below this blended gross margin without
        # the chief estimator's sign-off. SCA's term-contract rate is engineered to test it.
        "margin_floor": 0.15,
        # a single line priced below this is always held for review, even if the
        # bid blend clears the floor.
        "line_margin_hard_floor": 0.08,
        # alternates above this unit-rate delta need human approval.
        "substitution_price_tolerance": 0.20,
        # block auto-submit entirely for owners on a prequalification / bonding hold.
        "block_on_credit_hold": True,
        # public-owner diversity participation goal (M/WBE + SDVOB share of bid value)
        # below which the bid is prepared but flagged for review.
        "mwbe_goal": 0.30,
    },
    # owner rate tiers: fraction off Forte's schedule rate (the CRM view pricing applies).
    "contract_tiers": {"A": 0.30, "B": 0.18, "C": 0.10},
    # alternates policy: the approved replacement when an assembly is unavailable or retired.
    "substitutions": {
        "ELV-HJ-40": {"to": "ELV-HJ-45", "reason": "HJ-45 supersedes HJ-40 (OEM form-fit-function, longer stroke)"},
        "DOR-FR-20": {"to": "DOR-FR-22", "reason": "FR-20 discontinued; FR-22 is the listed 90-min replacement"},
    },
    # learned synonyms: free-text phrase -> assembly. This is what the human-feedback
    # loop writes to, and what turns an "ambiguous" line into an instant match on
    # the next run. Seeded with a couple Estimating already knows.
    "synonyms": {
        "orange traffic cones": "SAF-CONE-28-CS",
        "form release": "CON-FRM-REL-DR",
    },
    # per-owner standing preferences (e.g. MTA's "canopy extension" means the 40 LF).
    # Populated by feedback; seeded empty so the demo can *earn* the first one.
    "customer_prefs": {},
}


def _load() -> dict:
    if _STORE.exists():
        try:
            disk = json.loads(_STORE.read_text(encoding="utf-8"))
            brain = copy.deepcopy(DEFAULTS)      # merge over defaults so new keys appear
            _deep_update(brain, disk)
            return brain
        except Exception:
            pass
    return copy.deepcopy(DEFAULTS)


def _deep_update(base: dict, over: dict) -> None:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


_BRAIN: dict | None = None


def brain() -> dict:
    global _BRAIN
    if _BRAIN is None:
        _BRAIN = _load()
    return _BRAIN


def save() -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(brain(), indent=2), encoding="utf-8")


def reset() -> dict:
    """Back to seeded defaults (clears learned synonyms/prefs)."""
    global _BRAIN
    _BRAIN = copy.deepcopy(DEFAULTS)
    if _STORE.exists():
        try:
            _STORE.unlink()
        except Exception:
            pass
    return _BRAIN


# --- typed accessors the agent uses ---------------------------------------------

def margin_floor() -> float:
    return float(brain()["policies"]["margin_floor"])


def line_hard_floor() -> float:
    return float(brain()["policies"]["line_margin_hard_floor"])


def sub_tolerance() -> float:
    return float(brain()["policies"]["substitution_price_tolerance"])


def block_on_credit_hold() -> bool:
    return bool(brain()["policies"]["block_on_credit_hold"])


def mwbe_goal() -> float:
    return float(brain()["policies"].get("mwbe_goal", 0.0))


def discount_for(tier: str) -> float:
    return float(brain()["contract_tiers"].get(tier, 0.0))


def substitution_for(sku: str) -> dict | None:
    return brain()["substitutions"].get(sku)


def synonym_for(phrase: str) -> str | None:
    return brain()["synonyms"].get(_norm(phrase))


def customer_pref(customer_id: str, phrase: str) -> str | None:
    return brain()["customer_prefs"].get(f"{customer_id}::{_norm(phrase)}")


# --- editors (the feedback loop + the UI write through here) ---------------------

def set_policy(key: str, value) -> dict:
    if key not in brain()["policies"]:
        raise KeyError(f"unknown policy {key!r}")
    brain()["policies"][key] = value
    save()
    return brain()["policies"]


def teach_synonym(phrase: str, sku: str, customer_id: str | None = None) -> None:
    """Human feedback: 'this phrase means this assembly'. Scope it to an owner when
    the mapping is owner-specific (e.g. MTA's 'canopy extension')."""
    if customer_id:
        brain()["customer_prefs"][f"{customer_id}::{_norm(phrase)}"] = sku
    else:
        brain()["synonyms"][_norm(phrase)] = sku
    save()


def set_substitution(sku: str, to_sku: str, reason: str = "human-approved alternate") -> None:
    brain()["substitutions"][sku] = {"to": to_sku, "reason": reason}
    save()


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def snapshot() -> dict:
    """A JSON-safe copy for the UI (counts + the editable tables)."""
    b = brain()
    return {
        "policies": dict(b["policies"]),
        "contract_tiers": dict(b["contract_tiers"]),
        "substitutions": {k: dict(v) for k, v in b["substitutions"].items()},
        "synonyms": dict(b["synonyms"]),
        "customer_prefs": dict(b["customer_prefs"]),
        "counts": {"synonyms": len(b["synonyms"]),
                   "customer_prefs": len(b["customer_prefs"]),
                   "substitutions": len(b["substitutions"])},
    }
