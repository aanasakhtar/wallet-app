import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import relationship

from .database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class TransactionType(str, enum.Enum):
    topup = "topup"
    transfer = "transfer"
    cashback = "cashback"


class TransactionStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    phone = Column(String, unique=True, nullable=True, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    wallet = relationship("Wallet", back_populates="owner", uselist=False)


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, unique=True)
    balance = Column(Float, nullable=False, default=0.0)
    currency = Column(String, nullable=False, default="PKR")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="wallet")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String, primary_key=True, default=gen_uuid)
    from_wallet_id = Column(String, ForeignKey("wallets.id"), nullable=True)
    to_wallet_id = Column(String, ForeignKey("wallets.id"), nullable=True)
    amount = Column(Float, nullable=False)
    type = Column(SAEnum(TransactionType), nullable=False)
    status = Column(SAEnum(TransactionStatus), nullable=False, default=TransactionStatus.pending)
    created_at = Column(DateTime, default=datetime.utcnow)


class IdempotencyKey(Base):
    """Remembers which transfer an Idempotency-Key produced.

    The row is inserted in the SAME database transaction as the money movement
    (see transfer_service.execute_transfer), so a key can never exist without
    its transfer and a transfer can never be committed without its key.
    """
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    key = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    transaction_id = Column(String, ForeignKey("transactions.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
