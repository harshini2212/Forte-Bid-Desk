# Forte Bid Desk

**An AI estimator that *executes*.** Built for [Forte Construction Corp.](https://www.fortecc.com/),
a New York general contractor (transit design-build, ADA station upgrades, schools and public
facilities). The Bid Desk reads an owner's inbound solicitation and runs the whole precon
job — resolves every scope line to Forte's cost library, checks yard and subcontractor
availability, swaps superseded assemblies, prices against the owner's rate schedule, clears a
submission-safety gate, and then **writes the estimate and drafts the proposal letter** — or
escalates to the chief estimator when a guardrail trips.

```bash
pip install -r requirements.txt
python serve.py                 # -> http://localhost:8000  (runs offline, no API key)
RUN_PACE=0.3 python serve.py    # drip the live stream for a demo
python serve.py --precompute    # regenerate cached estimates + routing metrics
python -m pytest -q             # engine tests
```

Opening a solicitation *streams the run*: the trace ticks step-by-step, guardrails turn green
one at a time, prices and the decision land last — the same generator that streams also
produces the cached estimate (one workflow definition, no fork).

---

## What it does, in Forte's terms

| Precon step | What the agent does |
|---|---|
| **Read the solicitation** | Pulls the scope request from the bid inbox (MTA C&D, SCA, DDC, private owners). |
| **Identify scope** | Maps messy lines ("the canopy extension we did at Kings Highway", "3x HJ-40 jacks") to priced assemblies. **Two-tier routing:** a distilled MiniLM student resolves the clean majority on-box for ~$0; only the ambiguous tail escalates to Claude, which also sees the owner's project history. |
| **Check availability** | Deliverable units by location (Holbrook warehouse, Islandia yard, NYC field office / sub bench) and lead times. |
| **Propose alternates** | Backordered or discontinued assemblies swap to the Playbook alternate (HJ-40 → HJ-45, FR-20 → FR-22) inside a rate tolerance. |
| **Price** | Forte's schedule rate less the owner's tier discount (term contract, design-build negotiated, lump-sum). |
| **Guardrails** | Scope validity · alternates tolerance · per-line margin floor · blended margin floor · owner prequalification / bonding standing · M/WBE participation goal. |
| **Execute** | Writes the estimate to the project system (Procore in production; a local write here) and drafts the owner-facing proposal letter — or holds and escalates with the exact reason. |

## The four solicitations (four outcomes)

| Bid | What it exercises | Outcome |
|---|---|---|
| **MTA C&D · Package 7, 167th St** | a clean line, an ambiguous *"the canopy extension we did at Kings Highway"* (→ teacher resolves it from project history), and a backordered HJ-40 jack (→ HJ-45 alternate) | **auto-submitted** |
| **NYC SCA · PS 118** (term contract) | a discontinued FR-20 door swapped cleanly — but the deep term-contract rate pulls the *blended* margin under 15% | **escalated** (margin floor) |
| **NYC DDC · Bronx library sitework** | every line resolves cleanly | **escalated** (owner on a PASSPort prequalification hold) |
| **Islandia Medical Office** (private) | everything deliverable, margins healthy | **auto-submitted** end-to-end |

## Routing numbers (computed, not asserted)

`distill.py` builds a labeled scope-query set from the cost library, runs the student over it,
calibrates the accept threshold to hold precision ≥ 97%, and reports the routed system against
a teacher-on-everything baseline. On this library:

```
~95% of scope lookups resolve on the cheap local tier · 100% precision where the student keeps them
~20 ms median latency (MiniLM on-box) vs ~3.2 s to the frontier model
$0.57 / 1k lookups vs $11 routing everything to the frontier model → ~95% lower serving cost
```

## Architecture

```
Connectors ─▶  Playbook ─▶       Bid Desk agent ─────────▶  ┌─ GUARDRAIL GATE ─┐ ─▶  ACT
(inbox/library/ (editable memory:  parse · identify ·        │ margin · standing│    execute → Procore
 yard/owners)   rates, alternates, availability · alternates·│ alternates ·     │    + proposal letter
                learned synonyms)  price                     │ M/WBE · validity │    · or escalate
                                        ▲                    └────────┬─────────┘
                                   student→teacher routing       ✓ submit · ✕ hold for a human
                                        │
                                   estimator feedback ──────────────┘  (a correction teaches the
                                                                        Playbook → next run
                                                                        resolves it instantly)
```

- **`forte/service.py`** — the connector layer: bid inbox, cost library, availability, owner CRM. Synthetic in the demo; same interface behind Procore / Sage 300 CRE / PASSPort in production.
- **`forte/playbook.py`** — the editable estimating memory: rate tiers, alternates policy, guardrail thresholds, synonyms learned from feedback.
- **`forte/scope.py`** — two-tier scope identification (hybrid MiniLM/BERT retriever → Claude escalation), the routing heart.
- **`forte/guardrails.py`** — the deterministic submission-safety gate.
- **`forte/worker.py`** — the run as a streamed event generator (parse → identify → availability → alternates → price → guard → execute).
- **`forte/distill.py`** — the teacher→student distillation metrics (calibration, precision, cost, latency).
- **`forte/feedback.py`** — estimator corrections that write back to the Playbook.
- **`forte/ask.py`** — *Forte AI*: grounded Q&A over the live data with citation chips; Claude when a key is set, a deterministic grounded responder offline.
- **`forte/static/index.html`** — the single-file UI: command-center Home, Bid Desk, Ask AI, Model Routing, Playbook, Connectors, Cost Library, Runs.

## Honest scope

- **Calibrated demo data, never real owner data.** The cost library, availability, owners, and solicitations are a curated, deterministic slice with *planted, findable* challenges so the agent visibly earns each result. Owner contacts and emails are fictional. Bid-volume and backlog trend charts are illustrative.
- **Runs fully offline.** Committed cache + a deterministic teacher fallback mean the whole demo runs with no API key; set `ANTHROPIC_API_KEY` (see `.env.example`) to route the ambiguous tail and Forte AI questions through live Claude.
- **Reproduces the pattern, not Forte's stack.** In production the connector layer points at Forte's real systems; the worker never changes.

## Deploy

`render.yaml` (Render), the `Dockerfile` (Railway / Cloud Run), or `python serve.py` behind any
ASGI host — a single FastAPI origin serves the UI + the streaming API.
