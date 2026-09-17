# Forte Bid Desk

**An AI estimator that executes, built for [Forte Construction Corp.](https://www.fortecc.com/)**

Forte is a New York general contractor: MTA ADA design-build packages, station renewals,
elevator replacements, schools and public facilities across NYC and Long Island. Every one of
those projects starts at the precon desk, where an estimator turns an owner's solicitation into
a priced, defensible bid. This project is an AI teammate for that desk.

It reads an inbound solicitation and runs the whole job: resolves every scope line to Forte's
cost library, checks yard and subcontractor availability, swaps superseded assemblies, prices
against the owner's rate schedule, clears a submission-safety gate, and then **writes the
estimate and drafts the proposal letter**, or escalates to the chief estimator with the exact
reason. It suggests nothing; it acts, and it never ships a number that fails a guardrail.

```bash
pip install -r requirements.txt
python serve.py                 # -> http://localhost:8000  (runs offline, no API key)
RUN_PACE=0.3 python serve.py    # drip the live stream for a demo
python serve.py --precompute    # regenerate cached estimates + routing metrics
python -m pytest -q             # engine tests
```

Opening a solicitation *streams the run*: the trace ticks step by step, guardrails turn green
one at a time, prices and the decision land last. The same generator that streams also
produces the cached estimate, so there is one workflow definition and no fork.

---

## What it does, in Forte's terms

| Precon step | What the agent does |
|---|---|
| **Read the solicitation** | Pulls the scope request from the bid inbox (MTA C&D, SCA, DDC, private owners). |
| **Identify scope** | Maps messy lines ("the canopy extension we did at Kings Highway", "3x HJ-40 jacks") to priced assemblies. A distilled MiniLM student resolves the clean majority on-box for ~$0; only the ambiguous tail escalates to Claude, which also sees the owner's project history. |
| **Check availability** | Deliverable units by location (Holbrook warehouse, Islandia yard, NYC field office and sub bench) and lead times. |
| **Propose alternates** | Backordered or discontinued assemblies swap to the Playbook alternate (HJ-40 → HJ-45, FR-20 → FR-22) inside a rate tolerance, with the reason logged. |
| **Price** | Forte's schedule rate less the owner's tier discount (term contract, design-build negotiated, lump-sum). |
| **Guardrails** | Scope validity · alternates tolerance · per-line margin floor · blended margin floor · owner prequalification and bonding standing · M/WBE participation goal. |
| **Execute** | Writes the estimate to the project system (Procore in production, a local write here) and drafts the owner-facing proposal letter, or holds and escalates. |

## The four solicitations, four outcomes

| Bid | What it exercises | Outcome |
|---|---|---|
| **MTA C&D · Package 7, 167th St** | a clean line, an ambiguous *"the canopy extension we did at Kings Highway"* (resolved from project history), and a backordered HJ-40 jack (swapped to the HJ-45 alternate) | **auto-submitted** |
| **NYC SCA · PS 118** (term contract) | a discontinued FR-20 door swapped cleanly, but the deep term-contract rate pulls the *blended* margin under 15% | **escalated** (margin floor) |
| **NYC DDC · Bronx library sitework** | every line resolves cleanly | **escalated** (owner on a PASSPort prequalification hold) |
| **Islandia Medical Office** (private) | everything deliverable, margins healthy | **auto-submitted** end to end |

## Why the guardrails matter

A copilot that suggests a wrong number gets caught by an estimator. An agent that *submits*
the estimate cannot rely on that. So a deterministic gate sits in the hot path: any BLOCK
(scope validity, per-line floor, blended floor, owner standing) holds the bid; any WARN
(alternates tolerance, M/WBE goal) prepares it for review; a clean gate submits. Thresholds
live in the Playbook and are editable, so a person moves the margin floor and the next run
behaves differently.

## Routing numbers (computed, not asserted)

`distill.py` builds a labeled scope-query set from the cost library, runs the student over it,
calibrates the accept threshold to hold precision at or above 97%, and reports the routed
system against a teacher-on-everything baseline:

```
~95% of scope lookups resolve on the cheap local tier · 100% precision where the student keeps them
~20 ms median latency (MiniLM on-box) vs ~3.2 s to the frontier model
$0.57 / 1k lookups vs $11 routing everything to the frontier model → ~95% lower serving cost
```

## The learning loop

A vague line escalates to the teacher the first time. Confirm what it means once, and it is
written to the Playbook as an owner-scoped synonym. The next run resolves it instantly on the
free memory tier. The Playbook page runs the before/after and shows the escalation disappear.

## Architecture

```
Connectors ─▶  Playbook ─▶       Bid Desk agent ─────────▶  ┌─ GUARDRAIL GATE ─┐ ─▶  ACT
(inbox/library/ (editable memory:  parse · identify ·        │ margin · standing│    execute → Procore
 yard/owners)   rates, alternates, availability · alternates·│ alternates ·     │    + proposal letter
                learned synonyms)  price                     │ M/WBE · validity │    · or escalate
                                        ▲                    └────────┬─────────┘
                                   student→teacher routing       ✓ submit · ✕ hold for a human
                                        │
                                   estimator feedback ──────────────┘
```

| Module | Role |
|---|---|
| `forte/catalog.py` | The company workspace: cost library, availability, owners, project history, bid inbox. |
| `forte/scope.py` | Two-tier scope identification (hybrid MiniLM/BERT retriever → Claude escalation). |
| `forte/guardrails.py` | The deterministic submission-safety gate. |
| `forte/worker.py` | The run as a streamed event generator (parse → identify → availability → alternates → price → guard → execute). |
| `forte/playbook.py` | Editable estimating memory: rate tiers, alternates policy, thresholds, learned synonyms. |
| `forte/feedback.py` | Estimator corrections that write back to the Playbook. |
| `forte/distill.py` | Teacher → student distillation metrics (calibration, precision, cost, latency). |
| `forte/ask.py` | Forte AI: grounded Q&A over the live desk with citation chips. |
| `forte/service.py`, `forte/app.py` | JSON shapes and the FastAPI routes; two SSE streams. |
| `forte/static/index.html` | The single-file UI: Home command center, Bid Desk, Forte AI, Model Routing, Playbook, Connectors, Cost Library, Runs. |

See [ARCHITECTURE.md](ARCHITECTURE.md) for the event protocol and decision logic.

## Honest scope

- **Calibrated demo data, never real owner data.** The cost library, availability, owners, and solicitations are a curated, deterministic slice with *planted, findable* challenges so the agent visibly earns each result. Owner contacts are fictional. Bid-volume and backlog charts are illustrative and labeled as such.
- **Runs fully offline.** Committed cache plus a deterministic teacher fallback mean the whole demo runs with no API key. Set `ANTHROPIC_API_KEY` (see `.env.example`) to route the ambiguous tail and Forte AI questions through live Claude.
- **Reproduces the pattern, not Forte's stack.** In production the connector layer points at Forte's real systems (Procore, Sage 300 CRE, PASSPort); the agent never changes.

## Deploy

`render.yaml` (Render), the `Dockerfile` (Railway / Cloud Run), or `python serve.py` behind any
ASGI host. A single FastAPI origin serves the UI and the streaming API.

## Author

Harshini Vijaya Kumar · [harshinivijay.com](https://harshinivijay.com) · [LinkedIn](https://www.linkedin.com/in/harshini-vijayakumar/) · hv2201@nyu.edu
