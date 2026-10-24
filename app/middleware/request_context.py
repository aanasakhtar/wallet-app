"""
Assigns a request ID to every request - HTTP (this middleware) and gRPC
(grpc_service/interceptors.py uses set_request_id/reset_request_id below).

The ID is stored in a ContextVar so any log line can include it without
threading a parameter through every call. A caller may supply its own
`X-Request-ID` (HTTP) or `x-request-id` (gRPC metadata); that is how one ID
follows a request across the HTTP/gRPC boundary.
"""
import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..errors import error_body

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_log = logging.getLogger("wallet_app")


def get_request_id() -> str:
    """Request ID of whatever request is being handled; safe to call anywhere."""
    return _request_id_ctx.get()


def set_request_id(request_id: str):
    return _request_id_ctx.set(request_id)


def reset_request_id(token) -> None:
    _request_id_ctx.reset(token)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        request_id = incoming if incoming else str(uuid.uuid4())
        request.state.request_id = request_id  # for exception handlers, which run after reset
        token = _request_id_ctx.set(request_id)
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Last line of defence: an unexpected bug still gets the standard
                # error envelope (and the X-Request-ID header) instead of a bare 500.
                _log.exception("Unhandled exception",
                               extra={"event": "unhandled_exception", "path": request.url.path})
                response = JSONResponse(
                    status_code=500,
                    content=error_body("internal_error", "Internal server error", request_id),
                )
        finally:
            _request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response
