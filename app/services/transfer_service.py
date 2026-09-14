from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..logging_config import app_logger


class TransferError(Exception):
    """Raised for any transfer failure the router should turn into a 4xx."""


def _find_recipient(db: Session, identifier: str) -> models.User:
    user = (
        db.query(models.User)
        .filter((models.User.email == identifier) | (models.User.phone == identifier))
        .first()
    )
    if user is None:
        raise TransferError("Recipient not found")
    return user


def execute_transfer(
    db: Session,
    sender: models.User,
    recipient_identifier: str,
    amount: float,
) -> models.Transaction:
    recipient = _find_recipient(db, recipient_identifier)
    if recipient.id == sender.id:
        raise TransferError("Cannot transfer to yourself")

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
        app_logger.warning(
            "Transfer failed (insufficient balance): wallet_id=%s amount=%.2f",
            sender_wallet.id,
            amount,
        )
        raise TransferError("Insufficient balance")

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

    app_logger.info(
        "Transfer completed: from_wallet_id=%s to_wallet_id=%s amount=%.2f",
        sender_wallet.id,
        recipient_wallet.id,
        amount,
    )
    return txn
