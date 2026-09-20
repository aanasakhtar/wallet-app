"""
Part E.2 - Cardinality explosion demo.

A deliberately separate, tiny app on its own port (8001) with its own
Prometheus job. Keeping it out of the main wallet app means the experiment
cannot pollute the real app's time series, and it can be restarted freely
to reset the demo counter.

The demo exposes ONE counter, `demo_requests_total`, in two modes:

  labelled mode   (default): demo_requests_total{request_id="<uuid>"}
                  -> one new time series per unique request id
  unlabelled mode: demo_requests_total{endpoint="/demo"}
                  -> a single time series regardless of traffic volume

The brief says to stop at 100 unique IDs, so MAX_UNIQUE_IDS is a hard cap.
This is not enough to hurt Prometheus; it is enough to make the growth
curve obvious in a graph.

Run:
    python3 cardinality_demo.py                  # labelled (bad) mode
    CARDINALITY_MODE=off python3 cardinality_demo.py   # unlabelled (good) mode

Then drive traffic:
    python3 scripts/drive_cardinality.py

Observe in Prometheus:
    count(demo_requests_total)          # number of series
    sum(demo_requests_total)            # total requests (same either way)
"""
import os
import uuid

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest

MODE = os.getenv("CARDINALITY_MODE", "on").lower()  # "on" = label with request_id
MAX_UNIQUE_IDS = int(os.getenv("MAX_UNIQUE_IDS", "100"))
PORT = int(os.getenv("CARDINALITY_PORT", "8001"))

# A dedicated registry keeps the default python_* and process_* collectors out
# of this demo's output, so `count(demo_requests_total)` is unambiguous.
registry = CollectorRegistry()

if MODE == "on":
    demo_requests_total = Counter(
        "demo_requests_total",
        "Demo counter labelled with a unique request id (DO NOT DO THIS IN PRODUCTION).",
        ["request_id"],
        registry=registry,
    )
else:
    demo_requests_total = Counter(
        "demo_requests_total",
        "Demo counter with a bounded label set (the correct approach).",
        ["endpoint"],
        registry=registry,
    )

app = FastAPI(title="Cardinality Demo", version="1.0.0")

_unique_ids: set[str] = set()
_capped = False


@app.get("/demo")
def demo():
    """Each call either creates a new time series (labelled mode) or reuses
    the single existing one (unlabelled mode)."""
    global _capped
    request_id = str(uuid.uuid4())

    if MODE == "on":
        if len(_unique_ids) < MAX_UNIQUE_IDS:
            _unique_ids.add(request_id)
            demo_requests_total.labels(request_id=request_id).inc()
        else:
            if not _capped:
                print(f"Reached the {MAX_UNIQUE_IDS}-series cap; reusing an existing id.")
                _capped = True
            # Reuse an existing id so traffic keeps counting without growing series.
            demo_requests_total.labels(request_id=next(iter(_unique_ids))).inc()
    else:
        demo_requests_total.labels(endpoint="/demo").inc()

    return {
        "mode": MODE,
        "request_id": request_id,
        "unique_series": len(_unique_ids) if MODE == "on" else 1,
        "capped": _capped,
    }


@app.get("/stats")
def stats():
    return {
        "mode": MODE,
        "max_unique_ids": MAX_UNIQUE_IDS,
        "series_count": len(_unique_ids) if MODE == "on" else 1,
        "capped": _capped,
    }


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    label = "request_id (HIGH cardinality)" if MODE == "on" else "endpoint (bounded)"
    print(f"Cardinality demo on port {PORT}, labelling by {label}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
