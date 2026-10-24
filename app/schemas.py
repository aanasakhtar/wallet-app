from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from . import validation
from .models import TransactionStatus, TransactionType


class StrictModel(BaseModel):
    """Request bodies reject unknown fields, so a typo like `ammount` is a 422
    instead of being silently ignored."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---- Errors (the single envelope every endpoint uses) ----

class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable error code", examples=["insufficient_balance"])
    message: str = Field(description="Human-readable explanation")
    request_id: Optional[str] = Field(default=None, description="Same value as the X-Request-ID response header; quote it when reporting a problem")
    details: Optional[Any] = Field(default=None, description="Optional extra info, e.g. the list of invalid fields")


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---- Auth ----

class SignupRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: Optional[str] = Field(default=None, pattern=validation.PHONE_PATTERN,
                                 description="Digits with optional leading +, 10-15 digits")
    password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    phone: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- Wallet ----

class WalletOut(BaseModel):
    id: str
    balance: float
    currency: str
    model_config = ConfigDict(from_attributes=True)


class AmountMixin(StrictModel):
    amount: float = Field(gt=0, le=validation.MAX_TRANSFER_AMOUNT,
                          description="PKR, at most 2 decimal places")

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v):
        return validation.validate_amount(v)


class TopupRequest(AmountMixin):
    pass


# ---- Transfers ----

class TransferRequest(AmountMixin):
    recipient: str = Field(min_length=validation.RECIPIENT_MIN, max_length=validation.RECIPIENT_MAX,
                           description="Recipient's email address or phone number")


class TransactionOut(BaseModel):
    id: str
    from_wallet_id: Optional[str]
    to_wallet_id: Optional[str]
    amount: float
    type: TransactionType
    status: TransactionStatus
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
