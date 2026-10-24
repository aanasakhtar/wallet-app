"""
Domain errors: ONE place that says how every business failure maps to an HTTP
status and to a gRPC status.

Both front ends (the FastAPI app and the gRPC service) raise and catch these
same classes. The HTTP error handlers read `http_status`; the gRPC servicer
reads `grpc_status` (a grpc.StatusCode *name*, kept as a string so the HTTP
app never has to import grpc). Having a single table is what keeps the two
protocols consistent: the same rule violation can not be a 409 on one side
and an INVALID_ARGUMENT on the other by accident.
"""
from typing import Any, Dict, Optional


class DomainError(Exception):
    code = "internal_error"
    http_status = 500
    grpc_status = "INTERNAL"
    default_message = "Internal server error"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        details: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.message = message or self.default_message
        self.details = details
        self.headers = headers or {}
        super().__init__(self.message)

    @property
    def reason(self) -> str:
        """Stable machine-readable reason; also the label value used by the
        wallet_transfer_rejections_total metric."""
        return self.code


# ---- request / auth -------------------------------------------------------

class ValidationFailed(DomainError):
    code, http_status, grpc_status = "validation_error", 422, "INVALID_ARGUMENT"
    default_message = "Request validation failed"


class Unauthenticated(DomainError):
    code, http_status, grpc_status = "unauthenticated", 401, "UNAUTHENTICATED"
    default_message = "Missing, invalid or expired credentials"

    def __init__(self, message=None, **kw):
        kw.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kw)


class InvalidCredentials(DomainError):
    code, http_status, grpc_status = "invalid_credentials", 401, "UNAUTHENTICATED"
    default_message = "Incorrect email or password"


class EmailAlreadyRegistered(DomainError):
    code, http_status, grpc_status = "email_already_registered", 409, "ALREADY_EXISTS"
    default_message = "Email already registered"


class PhoneAlreadyRegistered(DomainError):
    code, http_status, grpc_status = "phone_already_registered", 409, "ALREADY_EXISTS"
    default_message = "Phone number already registered"


# ---- transfers ------------------------------------------------------------

class TransferError(DomainError):
    """Base class for every business failure of a transfer."""
    code, http_status, grpc_status = "transfer_error", 400, "FAILED_PRECONDITION"


class RecipientNotFound(TransferError):
    code, http_status, grpc_status = "recipient_not_found", 404, "NOT_FOUND"
    default_message = "Recipient not found"


class SelfTransfer(TransferError):
    # Invalid whatever the system state is -> 422 / INVALID_ARGUMENT
    code, http_status, grpc_status = "self_transfer", 422, "INVALID_ARGUMENT"
    default_message = "Cannot transfer to yourself"


class InsufficientBalance(TransferError):
    # Depends on the CURRENT balance and may succeed later -> 409 / FAILED_PRECONDITION
    code, http_status, grpc_status = "insufficient_balance", 409, "FAILED_PRECONDITION"
    default_message = "Insufficient balance"


class TransferNotFound(DomainError):
    code, http_status, grpc_status = "transfer_not_found", 404, "NOT_FOUND"
    default_message = "Transfer not found"


# ---- idempotency / rate limiting -----------------------------------------

class IdempotencyKeyReuse(DomainError):
    code, http_status, grpc_status = "idempotency_key_reuse", 422, "INVALID_ARGUMENT"
    default_message = "Idempotency-Key was already used with a different request"


class RateLimited(DomainError):
    code, http_status, grpc_status = "rate_limited", 429, "RESOURCE_EXHAUSTED"
    default_message = "Too many requests"


def error_body(code: str, message: str, request_id: Optional[str], details: Any = None) -> dict:
    """The ONE error envelope returned by every endpoint."""
    body = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        body["details"] = details
    return {"error": body}
