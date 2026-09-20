"""
Applies configured faults before the real handler runs.

Ordering matters and is subtle, so it is spelled out here. Starlette runs the
LAST-added middleware OUTERMOST, so main.py adds these in reverse order of
execution to produce:

    RequestContextMiddleware   (outermost - assigns request_id FIRST)
      -> MetricsMiddleware     (starts the latency timer)
        -> FaultMiddleware     (innermost - sleeps/raises)
          -> route handler

Why this exact nesting:
  * request_id must be assigned before anything else so that fault logs carry
    the same correlation id as the request they degraded. With the fault
    middleware outermost, every fault log showed request_id="-" and could not
    be joined to its request in Kibana.
  * the metrics timer must start OUTSIDE the fault, otherwise the injected
    delay would not appear in wallet_http_request_duration_seconds and the
    Part E experiment would show nothing.
"""
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..faults import InjectedFault, fault_injector
from ..logging_config import app_logger


class FaultMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # /admin and /metrics stay clean so the experiment can always be
        # observed and switched off, even while a fault is active.
        if not path.startswith("/admin") and path != "/metrics":
            try:
                fault_injector.maybe_inject()
            except InjectedFault as e:
                app_logger.error(
                    "Request failed due to injected fault",
                    extra={"event": "request_failed", "reason": "injected_fault", "path": path},
                )
                return JSONResponse(status_code=503, content={"detail": str(e)})
        return await call_next(request)
