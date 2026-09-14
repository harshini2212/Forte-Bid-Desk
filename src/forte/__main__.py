"""Launch the Forte Bid Desk:

    python -m forte            ->  http://localhost:8000
    python -m forte --port 8050
    python -m forte --precompute   # cache estimates + routing metrics, then exit
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    # Best-effort: load ANTHROPIC_API_KEY from .env so live Claude works without
    # manual env setup. Cached estimates + the offline teacher run with no key at all.
    try:
        from .llm import load_api_key
        if load_api_key():
            print("[forte] ANTHROPIC_API_KEY loaded — live Claude teacher enabled.")
        else:
            print("[forte] No API key — running fully offline (cached + deterministic teacher).")
    except Exception:
        pass

    if "--precompute" in sys.argv:
        from . import service
        for r in service.rfqs():
            print(f"estimating {r['id']} ...", flush=True)
            q = service.quote(r["id"], force=True)
            print(f"  -> {q['ref']} {q['decision']} ${q['subtotal']:,.0f} "
                  f"margin {q['blended_margin']*100:.1f}%")
        print("routing metrics ...", flush=True)
        r = service.routing_run()["routed"]
        print(f"  -> {r['cheap_tier_pct']}% cheap tier · precision {r['student_precision']}")
        print("done.")
        return

    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    else:
        port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"

    import uvicorn
    print(f"Forte Bid Desk -> http://{host}:{port}")
    uvicorn.run("forte.app:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
