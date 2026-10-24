from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import models
from .database import Base, SessionLocal, engine
from .error_handlers import register_error_handlers
from .logging_config import app_logger, configure_logging
from .middleware.fault_middleware import FaultMiddleware
from .middleware.metrics_middleware import MetricsMiddleware
from .middleware.request_context import RequestContextMiddleware
from .openapi import install_openapi, unique_id
from .routers import admin, auth, transfers, wallet
from .services import idempotency_service

configure_logging()

# create_all also creates tables added later (idempotency_keys) in an existing DB;
# a real migration tool (Alembic) is still the production answer.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Wallet API",
    version="0.3.0",
    description="Peer-to-peer wallet: sign up, top up, send money, view history. "
                "All errors use one envelope: `{\"error\": {code, message, request_id, details?}}`.",
    generate_unique_id_function=unique_id,
)

# Middleware order matters. Starlette runs the LAST-added middleware OUTERMOST:
#   RequestContextMiddleware  (assigns request_id; also the last-resort 500 handler)
#     -> MetricsMiddleware    (timer starts here, so injected delay IS measured)
#       -> FaultMiddleware    (innermost - performs the sleep or raise)
#         -> route handler
# See app/middleware/fault_middleware.py for why this exact nesting is needed.
app.add_middleware(FaultMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestContextMiddleware)

register_error_handlers(app)
install_openapi(app)

app.include_router(auth.router)
app.include_router(wallet.router)
app.include_router(transfers.router)
app.include_router(admin.router)


@app.on_event("startup")
def startup() -> None:
    """Housekeeping at startup: drop expired idempotency keys."""
    db = SessionLocal()
    try:
        idempotency_service.purge_expired(db)
    finally:
        db.close()
    app_logger.info("Application started", extra={"event": "app_startup"})


@app.get("/health", tags=["health"], summary="Liveness check")
def health():
    return {"status": "ok"}


@app.get("/metrics", tags=["observability"], summary="Prometheus scrape target",
         response_class=PlainTextResponse)
def metrics_endpoint():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
