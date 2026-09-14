from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from .models import TransactionStatus, TransactionType


# ---- Auth ----

class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    phone: Optional[str] = None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- Wallet ----

class WalletOut(BaseModel):
    id: str
    balance: float
    currency: str

    class Config:
        from_attributes = True


class TopupRequest(BaseModel):
    amount: float = Field(gt=0)


# ---- Transfers ----

class TransferRequest(BaseModel):
    recipient: str  # recipient's email or phone
    amount: float = Field(gt=0)
    note: Optional[str] = None


class TransactionOut(BaseModel):
    id: str
    from_wallet_id: Optional[str]
    to_wallet_id: Optional[str]
    amount: float
    type: TransactionType
    status: TransactionStatus
    created_at: datetime

    class Config:
        from_attributes = True
