"""Forte Bid Desk — an AI estimator that *executes*, built for Forte Construction Corp.

A system of action: an agent that reads an owner's bid solicitation, resolves the
scope to Forte's cost library, checks yard/sub availability, swaps superseded
assemblies, prices against the owner's rate schedule, clears the submission
guardrails, and **executes** the estimate end-to-end inside the project system —
with a real two-tier (student -> teacher) model-routing pipeline behind the scope
matching.

Layers:
  service.py     — the connector layer (bid inbox, cost library, yard/sub bench, owner CRM)
  playbook.py    — the editable estimating memory (rates, alternates, learned synonyms, thresholds)
  scope.py       — two-tier scope identification (cheap MiniLM student, escalate to Claude)
  guardrails.py  — the submission-safety gate (margin floors, owner standing, M/WBE goal)
  worker.py      — the Bid Desk run, streamed step-by-step as it executes
  distill.py     — the teacher->student distillation metrics (precision, cost, latency, tier mix)
  feedback.py    — human-in-the-loop corrections that write back to the Playbook
  ask.py         — Forte AI: grounded Q&A over the live data
"""

__version__ = "1.0.0"
