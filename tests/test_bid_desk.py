"""End-to-end checks for the Bid Desk engine — no network, no API key.

The planted challenges must produce the planned outcomes:
  BID-3101 (MTA)       -> auto-executed: ambiguous line resolved from history, HJ-40 swapped to HJ-45
  BID-3102 (SCA)       -> escalated on the blended margin floor; FR-20 swapped to FR-22
  BID-3103 (DDC)       -> escalated on the owner prequalification hold
  BID-3104 (Islandia)  -> auto-executed, every line on the cheap tier or teacher-endorsed
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from forte import guardrails, playbook  # noqa: E402
from forte.catalog import company  # noqa: E402
from forte.scope import resolve_line, _tokens, parse_qty  # noqa: E402
from forte.worker import run_quote  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_playbook(tmp_path, monkeypatch):
    monkeypatch.setattr(playbook, "_STORE", tmp_path / "playbook.json")
    playbook.reset()
    yield
    playbook.reset()


def test_every_planted_line_resolves_to_gold():
    d = company()
    for r in d.rfqs:
        for ln in r.lines:
            res = resolve_line(d, ln.raw, r.customer_id)
            assert res.sku == ln.gold_sku, (r.id, ln.raw, res.sku)


def test_ambiguous_line_escalates_and_history_resolves_it():
    d = company()
    res = resolve_line(d, "1x the platform canopy extension we did at Kings Highway", "OWN-MTA")
    assert res.tier == "teacher" and res.escalated
    assert res.sku == "CAN-PLT-EXT-40"


def test_memory_tier_is_instant_after_teaching():
    d = company()
    phrase = "1x the canopy extension we always spec"
    before = resolve_line(d, phrase, "OWN-MTA", force_no_memory=True)
    assert before.tier == "teacher"
    playbook.teach_synonym("the canopy extension we always spec", "CAN-PLT-EXT-40", "OWN-MTA")
    after = resolve_line(d, phrase, "OWN-MTA")
    assert after.tier == "memory" and after.sku == "CAN-PLT-EXT-40"


def test_outcomes_match_the_planted_challenges():
    q = {rid: run_quote(rid) for rid in ["BID-3101", "BID-3102", "BID-3103", "BID-3104"]}
    assert q["BID-3101"]["decision"] == "auto_execute"
    assert any(l["substituted"] and l["sku"] == "ELV-HJ-45" for l in q["BID-3101"]["lines"])
    assert q["BID-3102"]["decision"] == "escalate"
    assert "margin_floor" in q["BID-3102"]["verdict"]["blocked_by"]
    assert any(l["substituted"] and l["sku"] == "DOR-FR-22" for l in q["BID-3102"]["lines"])
    assert q["BID-3103"]["decision"] == "escalate"
    assert q["BID-3103"]["verdict"]["blocked_by"] == ["credit_hold"]
    assert q["BID-3104"]["decision"] == "auto_execute"
    for r in q.values():
        assert abs(sum(l["extended"] for l in r["lines"]) - r["subtotal"]) < 0.01


def test_moving_the_margin_floor_changes_the_decision():
    playbook.set_policy("margin_floor", 0.10)
    q = run_quote("BID-3102")
    # the margin BLOCK is gone; only the M/WBE WARN remains, so it is prepared for review
    assert q["decision"] == "review"
    assert q["verdict"]["blocked_by"] == []


def test_mwbe_guard_warns_below_goal():
    lines = [{"sku": "X", "name": "x", "qty": 1, "net_price": 100.0, "unit_cost": 60.0,
              "margin": 0.4, "status": "active", "mwbe": "", "substituted": False,
              "orig_sku": None, "price_delta": 0.0}]
    cust = company().customer("OWN-MTA")
    v = guardrails.evaluate(lines, cust)
    assert v.decision == "review"
    assert any(r.id == "mwbe_goal" and r.status == "warn" for r in v.results)


def test_tokenizer_normalizes_sizes_and_models():
    t = _tokens("1200 lf 3/4in EMT conduit")
    assert "sz:3/4" in t and "emt" in t and "1200" not in t
    assert "hj40" in _tokens("3x Model HJ-40 hydraulic jack assembly")
    assert parse_qty("2,400 sf 8in CMU wall") == 2400
