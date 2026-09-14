"""FastAPI app: JSON API + live SSE stream for the Bid Desk, serving the SPA."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import service, runlog
from .worker import quote_events


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
                "X-Accel-Buffering": "no"}
_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Forte Bid Desk", docs_url="/api/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


@app.on_event("startup")
def _warm_embedder():
    """Load the MiniLM student model + assembly vectors in the background so the
    first live scope lookup is fast (best-effort; the ranker falls back to lexical)."""
    import threading

    def _go():
        try:
            from .scope import warmup
            warmup()
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


@app.get("/api/health")
def health():
    import os
    return {"ok": True, "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "company": "Forte Construction Corp."}


@app.get("/api/overview")
def overview():
    return service.overview()


@app.get("/api/connectors")
def connectors():
    return service.connectors()


@app.get("/api/rfqs")
def rfqs():
    return service.rfqs()


@app.get("/api/rfq/{rfq_id}")
def rfq(rfq_id: str):
    try:
        return service.rfq(rfq_id)
    except KeyError:
        raise HTTPException(404, f"unknown bid {rfq_id}")


@app.get("/api/quotes")
def quotes():
    return service.quotes_index()


@app.get("/api/quotes.csv")
def quotes_csv():
    return PlainTextResponse(service.quotes_csv(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=estimates.csv"})


@app.get("/api/quote/{rfq_id}")
def quote(rfq_id: str):
    try:
        return service.quote(rfq_id)
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/stream/quote")
def stream_quote(rfq: str):
    """Stream the Bid Desk run as it executes (Server-Sent Events). The same
    generator `run_quote` drains for the cached path — one definition."""
    def gen():
        ref = decision = None
        cust = ""
        cost = 0.0
        for event, payload in quote_events(rfq):
            if event == "run_started":
                cust = (payload or {}).get("customer", "")
            elif event == "result":
                cost = (payload or {}).get("routing", {}).get("identify_cost_usd", 0.0)
            elif event == "done":
                decision, ref = payload.get("decision"), payload.get("ref")
                runlog.record("estimate", cust, f"{ref} · {rfq}",
                              status="done", decision=decision, ref=ref, cost_usd=cost)
            elif event == "failed":
                runlog.record("estimate", cust, rfq, status="failed",
                              note=(payload or {}).get("message", ""))
            yield sse(event, payload)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/ask/suggestions")
def ask_suggestions():
    from .ask import SUGGESTIONS
    return SUGGESTIONS


@app.get("/api/stream/ask")
def stream_ask(q: str, model: str | None = None):
    """Stream a grounded answer over the live Bid Desk data (SSE)."""
    from .ask import ask_events

    def gen():
        for event, payload in ask_events(q, model):
            if event == "done":
                runlog.record("ask", "Forte AI", q[:70], status="done")
            yield sse(event, payload)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/routing")
def routing():
    return service.routing()


@app.post("/api/routing/run")
def routing_run():
    return service.routing_run()


@app.get("/api/brain")
def brain():
    return service.brain()


@app.post("/api/brain/policy")
def set_policy(payload: dict = Body(...)):
    key = (payload or {}).get("key")
    value = (payload or {}).get("value")
    try:
        return service.set_policy(key, value)
    except KeyError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/brain/reset")
def reset_brain():
    return service.reset_brain()


@app.post("/api/brain/teach")
def teach(payload: dict = Body(...)):
    p = payload or {}
    try:
        return service.teach(p.get("customer_id"), p.get("phrase"), p.get("sku"),
                             p.get("scope", "customer"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/brain/demo")
def demo_feedback():
    return service.demo_feedback()


@app.get("/api/catalog")
def catalog():
    return service.catalog()


@app.get("/api/customers")
def customers():
    return service.customers()


@app.get("/api/search")
def search(q: str):
    return {"results": service.search(q)}


@app.get("/api/runs")
def runs():
    return runlog.list_runs()


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html",
                        headers={"Cache-Control": "no-store, must-revalidate"})


app.mount("/", StaticFiles(directory=_STATIC), name="static")
