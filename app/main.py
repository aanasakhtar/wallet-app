from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import models
from .database import Base, SessionLocal, engine
from .logging_config import app_logger, configure_logging
from .metrics import refresh_business_gauges
from .middleware.fault_middleware import FaultMiddleware
from .middleware.metrics_middleware import MetricsMiddleware
from .middleware.request_context import RequestContextMiddleware
from .routers import admin, auth, transfers, wallet

configure_logging()

# Phase 1 uses create_all for simplicity. A real migration tool (Alembic)
# is a reasonable follow-up but out of scope for this assignment.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Wallet API", version="0.2.0")

# Middleware order matters and is easy to get wrong. Starlette runs the
# LAST-added middleware OUTERMOST, so these are added inside-out. The result:
#
#   RequestContextMiddleware  (outermost - assigns request_id first, so every
#                              log line below it, faults included, is correlated)
#     -> MetricsMiddleware    (timer starts here, so injected delay IS measured)
#       -> FaultMiddleware    (innermost - performs the sleep or raise)
#         -> route handler
#
# See app/middleware/fault_middleware.py for why this exact nesting is needed.
app.add_middleware(FaultMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestContextMiddleware)

app.include_router(auth.router)
app.include_router(wallet.router)
app.include_router(transfers.router)
app.include_router(admin.router)


@app.on_event("startup")
def prime_gauges() -> None:
    """Gauges describe current state, so they must be correct immediately after
    a restart - not only after the first write. Without this, money_in_system
    would read 0 until someone made a transfer."""
    db = SessionLocal()
    try:
        refresh_business_gauges(db)
    finally:
        db.close()
    app_logger.info("Application started", extra={"event": "app_startup"})


@app.get("/health", tags=["health"])
def health():
    """Liveness check, separate from /metrics."""
    return {"status": "ok"}


@app.get("/metrics", tags=["observability"])
def metrics_endpoint():
    """Prometheus scrape target. Returns the text exposition format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
