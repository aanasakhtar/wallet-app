"""Reusable OpenAPI response descriptions so every route documents its errors."""
from .schemas import ErrorResponse

_DESCRIPTIONS = {
    401: "Missing, invalid or expired bearer token",
    404: "Resource not found",
    409: "Conflict with current state (e.g. insufficient balance, duplicate registration)",
    422: "Request failed validation (bad field, missing header, idempotency key reused with a different body, self-transfer)",
    429: "Rate limit exceeded; see the Retry-After header",
}


def errors(*statuses: int) -> dict:
    return {s: {"model": ErrorResponse, "description": _DESCRIPTIONS[s]} for s in statuses}
