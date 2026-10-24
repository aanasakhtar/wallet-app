"""Turns every kind of failure into the one error envelope (see errors.error_body)."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import DomainError, error_body
from .middleware.request_context import get_request_id

_STATUS_CODES = {
    400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 409: "conflict", 422: "validation_error", 429: "rate_limited",
}


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", None) or get_request_id()


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(exc.code, exc.message, _rid(request), exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [
            {
                "field": ".".join(str(p) for p in err["loc"]),
                "message": err["msg"].removeprefix("Value error, "),
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        first = details[0]
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", f"{first['field']}: {first['message']}",
                               _rid(request), details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, f"http_{exc.status_code}")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail), _rid(request)),
            headers=getattr(exc, "headers", None),
        )
