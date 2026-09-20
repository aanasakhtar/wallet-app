"""
Records HTTP-level metrics for every request.

Cardinality note (relevant to Part E): the `endpoint` label is taken from the
matched ROUTE TEMPLATE, not the raw URL path. If a route were ever changed to
/wallet/{wallet_id}, this middleware would still record endpoint="/wallet/{wallet_id}"
rather than one series per wallet id. Unmatched paths (404s) collapse to
"unmatched" for the same reason.
"""
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..metrics import (
    http_request_duration_seconds,
    http_requests_in_progress,
    http_requests_total,
)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    return "unmatched"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        http_requests_in_progress.inc()
        start = time.perf_counter()
        status_code = 500  # assume failure until proven otherwise
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            endpoint = _route_template(request)
            # /metrics itself is excluded so Prometheus scraping doesn't
            # inflate the app's own request counts.
            if endpoint != "/metrics":
                http_requests_total.labels(
                    method=request.method,
                    endpoint=endpoint,
                    status_code=str(status_code),
                ).inc()
                http_request_duration_seconds.labels(
                    method=request.method,
                    endpoint=endpoint,
                ).observe(duration)
            http_requests_in_progress.dec()
