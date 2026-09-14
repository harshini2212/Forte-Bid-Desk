"""Human-in-the-loop — corrections that write back to the Playbook.

The learning layer: an estimator's feedback flows back into the memory that
drives execution, so the agent gets better at *this* owner's language over time:

    a person confirms "for MTA, 'canopy extension' means CAN-PLT-EXT-40"
    → it's written to the Playbook as an owner-scoped synonym
    → the *next* run resolves that line instantly on the free memory tier,
      instead of escalating to the teacher.

`demonstrate()` returns the before/after so the UI can show the escalation
collapse into an instant memory hit — the loop paying for itself.
"""

from __future__ import annotations

from . import playbook
from .catalog import company
from .scope import resolve_line, resolution_json, _dephrase


def apply_correction(customer_id: str, phrase: str, sku: str,
                     scope: str = "customer") -> dict:
    """Teach the mapping phrase→assembly. scope='customer' makes it owner-specific
    (their word for it); scope='global' applies to everyone."""
    d = company()
    if d.part(sku) is None:
        raise ValueError(f"unknown assembly {sku!r}")
    before = resolve_line(d, phrase, customer_id, force_no_memory=True)
    playbook.teach_synonym(_dephrase(phrase), sku,
                           customer_id if scope == "customer" else None)
    after = resolve_line(d, phrase, customer_id)
    return {
        "taught": {"phrase": phrase, "sku": sku, "scope": scope, "customer_id": customer_id},
        "before": resolution_json(before),
        "after": resolution_json(after),
        "improvement": {
            "tier_before": before.tier, "tier_after": after.tier,
            "escalation_removed": before.escalated and not after.escalated,
            "now_instant": after.tier == "memory",
        },
        "brain": playbook.snapshot(),
    }


def approve_substitution(orig_sku: str, to_sku: str, reason: str = "") -> dict:
    """Human approves (or overrides) an alternate — persists to policy."""
    d = company()
    if d.part(to_sku) is None:
        raise ValueError(f"unknown assembly {to_sku!r}")
    playbook.set_substitution(orig_sku, to_sku, reason or "human-approved alternate")
    return {"substitution": playbook.substitution_for(orig_sku),
            "brain": playbook.snapshot()}


def demonstrate(customer_id: str = "OWN-MTA",
                phrase: str = "1x the canopy extension we always spec",
                sku: str = "CAN-PLT-EXT-40") -> dict:
    """A self-contained before/after for the UI's 'teach it once' demo."""
    return apply_correction(customer_id, phrase, sku)
