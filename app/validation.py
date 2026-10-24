"""
Input rules shared by the HTTP schemas (Pydantic) and the gRPC handler.
Keeping them here means "what is a valid amount" is defined exactly once.
"""
import math
import re
from decimal import Decimal
from typing import Optional

from .errors import ValidationFailed

MAX_TRANSFER_AMOUNT = 1_000_000.0  # PKR per operation
RECIPIENT_MIN, RECIPIENT_MAX = 3, 254
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
PHONE_PATTERN = r"^\+?[0-9]{10,15}$"

_idem_re = re.compile(IDEMPOTENCY_KEY_PATTERN)


def validate_amount(value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("amount must be a finite number")
    if value <= 0:
        raise ValueError("amount must be greater than 0")
    if value > MAX_TRANSFER_AMOUNT:
        raise ValueError(f"amount must not exceed {MAX_TRANSFER_AMOUNT:,.0f}")
    if Decimal(str(value)).as_tuple().exponent < -2:
        raise ValueError("amount must have at most 2 decimal places")
    return float(value)


def validate_recipient(value: str) -> str:
    value = (value or "").strip()
    if not (RECIPIENT_MIN <= len(value) <= RECIPIENT_MAX):
        raise ValueError(f"recipient must be {RECIPIENT_MIN}-{RECIPIENT_MAX} characters (email or phone)")
    return value


def validate_idempotency_key(value: Optional[str]) -> str:
    if not value or not _idem_re.match(value):
        raise ValueError("idempotency key must be 8-64 characters of A-Z a-z 0-9 _ -")
    return value


def validate_transfer_inputs(recipient: str, amount: float, idempotency_key: str):
    """gRPC entry point: same rules as the HTTP schema, reported as ValidationFailed."""
    problems = []
    for field, fn, value in (
        ("recipient", validate_recipient, recipient),
        ("amount", validate_amount, amount),
        ("idempotency_key", validate_idempotency_key, idempotency_key),
    ):
        try:
            fn(value)
        except ValueError as e:
            problems.append({"field": field, "message": str(e)})
    if problems:
        raise ValidationFailed(problems[0]["message"], details=problems)
