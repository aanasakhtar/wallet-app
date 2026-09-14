from fastapi import FastAPI

from . import models
from .database import Base, engine
from .logging_config import configure_logging
from .middleware.request_context import RequestContextMiddleware
from .routers import auth, transfers, wallet

configure_logging()

# Phase 1 uses create_all for simplicity. A real migration tool (Alembic)
# is a reasonable follow-up but out of scope for this assignment.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Wallet API", version="0.1.0")

app.add_middleware(RequestContextMiddleware)

app.include_router(auth.router)
app.include_router(wallet.router)
app.include_router(transfers.router)


@app.get("/health", tags=["health"])
def health():
    """Liveness check. Phase 2 will scrape a separate /metrics endpoint for
    Prometheus; this one stays a plain health check."""
    return {"status": "ok"}
