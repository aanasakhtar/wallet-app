from sqlalchemy.orm import Session

from .. import models, metrics
from ..logging_config import app_logger


class WalletError(Exception):
    pass


def get_wallet_for_user(db: Session, user: models.User) -> models.Wallet:
    wallet = db.query(models.Wallet).filter(models.Wallet.user_id == user.id).first()
    if wallet is None:
        # Should never happen since signup always creates one, but fail loudly if it does.
        app_logger.error(
            "No wallet found for user",
            extra={"event": "wallet_missing", "user_id": user.id},
        )
        raise WalletError("Wallet not found for user")
    return wallet


def topup(db: Session, user: models.User, amount: float) -> models.Transaction:
    wallet = get_wallet_for_user(db, user)

    wallet.balance += amount
    txn = models.Transaction(
        from_wallet_id=None,
        to_wallet_id=wallet.id,
        amount=amount,
        type=models.TransactionType.topup,
        status=models.TransactionStatus.completed,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

    metrics.transactions_total.labels(type="topup", status="completed").inc()

    app_logger.info(
        "Top-up completed",
        extra={
            "event": "topup_completed",
            "wallet_id": wallet.id,
            "transaction_id": txn.id,
            "amount": amount,
        },
    )
    return txn
