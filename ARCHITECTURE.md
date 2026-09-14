# Architecture

One FastAPI origin serves a zero-build single-page UI and a JSON + SSE API. The engine is
pure Python; Claude is optional and lazy.

```
src/forte/
  catalog.py     the company workspace: cost library, availability, owners, project history, inbox
  playbook.py    editable estimating memory (JSON-persisted; seeded defaults)
  scope.py       two-tier scope identification: MiniLM student (fastembed/ONNX) -> Claude teacher
  guardrails.py  deterministic submission gate (6 checks -> auto_execute | review | escalate)
  worker.py      the streamed run: a generator of typed events; run_quote() drains it
  distill.py     student calibration + routed-vs-teacher metrics -> data/forte/distill.json
  feedback.py    corrections -> Playbook synonyms / alternates (before/after demo)
  ask.py         Forte AI grounded Q&A (SSE), Claude or deterministic offline responder
  service.py     JSON shapes for the UI; caches estimates to data/forte/quote_*.json
  app.py         FastAPI routes; /api/stream/quote and /api/stream/ask are Server-Sent Events
  llm.py         content-addressed response cache + Claude client + API-key loader
  runlog.py      in-memory session log for the Runs page
  static/index.html   the UI (Home command center, Bid Desk, Ask AI, Routing, Playbook, ...)
```

## Event protocol (SSE)

`GET /api/stream/quote?rfq=BID-3101` emits, in order:

| event | payload | UI effect |
|---|---|---|
| `run_started` | run_id, customer, tier, subject | header |
| `step` | id, label, status (running/done) | execution trace ticks |
| `count` / `metric` | label, value, unit, flag | KPI tiles |
| `resolve` | the scope Resolution + final assembly, alternate, availability | line lands in the table with its tier badge |
| `finding` | severity, text, detail, evidence | "what the agent noticed" |
| `cell` | sku, net_price, extended, margin, thin | prices fill in |
| `guard` | id, label, status (pass/warn/block), detail, evidence | guardrails turn green/amber/red |
| `action` | kind (executed/escalated), ref, system, email | proposal letter + decision banner |
| `result` | the full estimate dict (cached to data/forte) | decision card |
| `done` | decision, ref, elapsed_ms | trace closes |

`GET /api/stream/ask?q=...&model=...` emits `user`, `step`, `token`*, `sources`, `done`.

## Decision logic

`guardrails.evaluate()` returns `escalate` on any BLOCK (scope validity, per-line floor,
blended floor, owner standing), `review` on any WARN (alternates tolerance, M/WBE goal), else
`auto_execute`. Only `auto_execute` writes the estimate; everything else is held with the reason.

## Determinism

Everything is hash-seeded (`catalog.stable_hash`) and the LLM cache is content-addressed
(`llm.request_key`), so a fresh clone yields the same library, estimates, and routing numbers.
