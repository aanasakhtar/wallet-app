"""
Assigns a unique request ID to every incoming HTTP request.

This exists now, in Phase 1, specifically so Phase 2's structured logging
(Part C of the assignment) can tag every log line with the request that
produced it, without having to thread a request_id parameter through every
function call by hand.
"""
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Read the request ID for whatever request is currently being handled.
    Safe to call from anywhere (routers, services, logging formatters)."""
    return _request_id_ctx.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        request_id = incoming if incoming else str(uuid.uuid4())
        token = _request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response
