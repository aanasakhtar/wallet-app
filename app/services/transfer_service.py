import time

from sqlalchemy.orm import Session

from .. import models, metrics
from ..config import settings
from ..logging_config import app_logger


class TransferError(Exception):
    """Raised for any transfer failure the router should turn into a 4xx."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _find_recipient(db: Session, identifier: str) -> models.User:
    user = (
        db.query(models.User)
        .filter((models.User.email == identifier) | (models.User.phone == identifier))
        .first()
    )
    if user is None:
        raise TransferError("Recipient not found", reason="recipient_not_found")
    return user


def execute_transfer(
    db: Session,
    sender: models.User,
    recipient_identifier: str,
    amount: float,
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
            raise TransferError("Cannot transfer to yourself", reason="self_transfer")

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
            raise TransferError("Insufficient balance", reason="insufficient_balance")

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

        db.commit()
        db.refresh(txn)

        metrics.transactions_total.labels(type="transfer", status="completed").inc()
        metrics.transfer_amount_pkr.observe(amount)
        if cashback_amount > 0:
            metrics.transactions_total.labels(type="cashback", status="completed").inc()
        metrics.refresh_business_gauges(db)

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
