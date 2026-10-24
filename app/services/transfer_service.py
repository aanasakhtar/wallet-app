import time
from typing import Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models, metrics
from ..config import settings
from ..errors import (DomainError, InsufficientBalance, RecipientNotFound, SelfTransfer,
                      TransferError, TransferNotFound)
from ..logging_config import app_logger
from . import idempotency_service, wallet_service


def _find_recipient(db: Session, identifier: str) -> models.User:
    user = (
        db.query(models.User)
        .filter((models.User.email == identifier) | (models.User.phone == identifier))
        .first()
    )
    if user is None:
        raise RecipientNotFound()
    return user


def execute_transfer(
    db: Session,
    sender: models.User,
    recipient_identifier: str,
    amount: float,
    idempotency_record: Optional[models.IdempotencyKey] = None,
) -> models.Transaction:
    """Move money between two wallets.

    Instrumentation lives here rather than in the router so that the metric
    measures the business operation itself, not HTTP overhead. pending_transfers
    is incremented on entry and decremented in `finally`, so it returns to zero
    even when a transfer is rejected.
    """
    metrics.pending_transfers.inc()
    start = time.perf_counter()
    try:
        try:
            recipient = _find_recipient(db, recipient_identifier)
        except TransferError:
            metrics.transfer_rejections_total.labels(reason="recipient_not_found").inc()
            app_logger.warning(
                "Transfer rejected: recipient not found",
                extra={"event": "transfer_rejected", "reason": "recipient_not_found",
                       "sender_user_id": sender.id},
            )
            raise

        if recipient.id == sender.id:
            metrics.transfer_rejections_total.labels(reason="self_transfer").inc()
            app_logger.warning(
                "Transfer rejected: self transfer",
                extra={"event": "transfer_rejected", "reason": "self_transfer",
                       "sender_user_id": sender.id},
            )
            raise SelfTransfer()

        # Lock both wallet rows in a stable order (by id) to avoid deadlocks if two
        # transfers between the same two wallets happen concurrently in opposite directions.
        wallet_ids = sorted(
            [
                db.query(models.Wallet.id).filter(models.Wallet.user_id == sender.id).scalar(),
                db.query(models.Wallet.id).filter(models.Wallet.user_id == recipient.id).scalar(),
            ]
        )
        locked_wallets = {
            w.id: w
            for w in db.query(models.Wallet)
            .filter(models.Wallet.id.in_(wallet_ids))
            .with_for_update()
            .all()
        }
        sender_wallet = next(w for w in locked_wallets.values() if w.user_id == sender.id)
        recipient_wallet = next(w for w in locked_wallets.values() if w.user_id == recipient.id)

        if sender_wallet.balance < amount:
            txn = models.Transaction(
                from_wallet_id=sender_wallet.id,
                to_wallet_id=recipient_wallet.id,
                amount=amount,
                type=models.TransactionType.transfer,
                status=models.TransactionStatus.failed,
            )
            db.add(txn)
            if idempotency_record is not None:
                db.delete(idempotency_record)  # a failed attempt must not consume the key
            db.commit()

            metrics.transactions_total.labels(type="transfer", status="failed").inc()
            metrics.transfer_rejections_total.labels(reason="insufficient_balance").inc()
            app_logger.warning(
                "Transfer failed: insufficient balance",
                extra={
                    "event": "transfer_failed",
                    "reason": "insufficient_balance",
                    "wallet_id": sender_wallet.id,
                    "transaction_id": txn.id,
                    "amount": amount,
                },
            )
            raise InsufficientBalance()

        sender_wallet.balance -= amount
        recipient_wallet.balance += amount

        txn = models.Transaction(
            from_wallet_id=sender_wallet.id,
            to_wallet_id=recipient_wallet.id,
            amount=amount,
            type=models.TransactionType.transfer,
            status=models.TransactionStatus.completed,
        )
        db.add(txn)

        cashback_amount = 0.0
        if settings.cashback_rate > 0:
            cashback_amount = round(amount * settings.cashback_rate, 2)
            recipient_wallet.balance += cashback_amount
            db.add(
                models.Transaction(
                    from_wallet_id=None,
                    to_wallet_id=recipient_wallet.id,
                    amount=cashback_amount,
                    type=models.TransactionType.cashback,
                    status=models.TransactionStatus.completed,
                )
            )

        if idempotency_record is not None:
            db.flush()  # assigns txn.id
            idempotency_record.transaction_id = txn.id  # same DB transaction as the money movement

        db.commit()
        db.refresh(txn)

        metrics.transactions_total.labels(type="transfer", status="completed").inc()
        metrics.transfer_amount_pkr.observe(amount)
        if cashback_amount > 0:
            metrics.transactions_total.labels(type="cashback", status="completed").inc()

        app_logger.info(
            "Transfer completed",
            extra={
                "event": "transfer_completed",
                "transaction_id": txn.id,
                "from_wallet_id": sender_wallet.id,
                "to_wallet_id": recipient_wallet.id,
                "amount": amount,
                "cashback_amount": cashback_amount,
            },
        )
        return txn
    finally:
        metrics.transfer_processing_seconds.observe(time.perf_counter() - start)
        metrics.pending_transfers.dec()


def create_transfer(
    db: Session,
    sender: models.User,
    recipient_identifier: str,
    amount: float,
    idempotency_key: str,
) -> Tuple[models.Transaction, bool]:
    """THE entry point for sending money, used by both the HTTP endpoint and the
    gRPC handler. Returns (transaction, replayed)."""
    request_hash = idempotency_service.fingerprint(recipient_identifier, amount)
    replay_id, record = idempotency_service.begin(db, sender.id, idempotency_key, request_hash)

    if replay_id is not None:
        txn = db.get(models.Transaction, replay_id)
        metrics.idempotent_replays_total.inc()
        app_logger.info("Transfer replayed from idempotency key",
                        extra={"event": "transfer_replayed", "transaction_id": replay_id,
                               "idempotency_key": idempotency_key})
        return txn, True

    try:
        txn = execute_transfer(db, sender, recipient_identifier, amount, idempotency_record=record)
    except DomainError:
        db.rollback()  # discards the uncommitted key row (a failed attempt keeps no key)
        raise
    return txn, False


def get_transfer(db: Session, user: models.User, transfer_id: str) -> models.Transaction:
    """A user may read only transfers they sent or received; anything else is a 404
    (not a 403) so the existence of other users' transfers is not revealed."""
    wallet = wallet_service.get_wallet_for_user(db, user)
    txn = (
        db.query(models.Transaction)
        .filter(models.Transaction.id == transfer_id,
                models.Transaction.type == models.TransactionType.transfer,
                or_(models.Transaction.from_wallet_id == wallet.id,
                    models.Transaction.to_wallet_id == wallet.id))
        .first()
    )
    if txn is None:
        raise TransferNotFound()
    return txn


def list_transactions(db: Session, user: models.User, limit: int, offset: int,
                      type: Optional[models.TransactionType] = None,
                      status: Optional[models.TransactionStatus] = None):
    wallet = wallet_service.get_wallet_for_user(db, user)
    q = db.query(models.Transaction).filter(
        or_(models.Transaction.from_wallet_id == wallet.id, models.Transaction.to_wallet_id == wallet.id))
    if type is not None:
        q = q.filter(models.Transaction.type == type)
    if status is not None:
        q = q.filter(models.Transaction.status == status)
    return q.order_by(models.Transaction.created_at.desc()).offset(offset).limit(limit).all()
