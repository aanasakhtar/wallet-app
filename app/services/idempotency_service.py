"""
Idempotency-Key handling for POST /transfers and the CreateTransfer RPC.

Contract (same for HTTP and gRPC):
  * first time a (user, key) is seen      -> the transfer runs, the key is stored
  * same key + same request again         -> NOTHING runs; the original result is returned
  * same key + DIFFERENT request          -> rejected (IdempotencyKeyReuse)
  * the transfer fails (4xx)              -> the key is NOT stored, so the caller may
                                             retry with the same key after fixing the cause

Why this is safe under crashes and races: the key row is written in the same
database transaction as the money movement. Either both commit or neither does,
so there is no "half-recorded" state to clean up. Two concurrent requests with
the same key collide on the UNIQUE(user_id, key) index; the loser rolls back and
re-reads the winner's committed result.
"""
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..errors import IdempotencyKeyReuse


def fingerprint(recipient: str, amount: float) -> str:
    canonical = f"{recipient.strip().lower()}|{amount:.2f}"
    return hashlib.sha256(canonical.encode()).hexdigest()


def begin(db: Session, user_id: str, key: str, request_hash: str) -> Tuple[Optional[str], Optional[models.IdempotencyKey]]:
    """Returns (replay_transaction_id, None) when the key was already used, or
    (None, new_record) when the caller should run the transfer."""
    expiry = datetime.utcnow() - timedelta(seconds=settings.idempotency_ttl_seconds)
    for _ in range(2):
        existing = db.query(models.IdempotencyKey).filter_by(user_id=user_id, key=key).first()
        if existing is not None and existing.created_at < expiry:
            db.delete(existing)  # expired: the key may be reused
            db.flush()
            existing = None
        if existing is None:
            record = models.IdempotencyKey(user_id=user_id, key=key, request_hash=request_hash)
            db.add(record)
            try:
                db.flush()  # not committed: becomes durable together with the transfer
                return None, record
            except IntegrityError:
                db.rollback()  # lost a race with a concurrent request using the same key
                continue
        if existing.request_hash != request_hash:
            raise IdempotencyKeyReuse()
        return existing.transaction_id, None
    raise IdempotencyKeyReuse("Could not reserve the idempotency key; please retry")


def purge_expired(db: Session) -> int:
    expiry = datetime.utcnow() - timedelta(seconds=settings.idempotency_ttl_seconds)
    n = db.query(models.IdempotencyKey).filter(models.IdempotencyKey.created_at < expiry).delete()
    db.commit()
    return n
