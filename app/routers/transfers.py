from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..dependencies import get_current_user
from ..services import transfer_service, wallet_service

router = APIRouter(tags=["transfers"])


@router.post("/transfers", response_model=schemas.TransactionOut)
def create_transfer(
    payload: schemas.TransferRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        txn = transfer_service.execute_transfer(
            db, sender=current_user, recipient_identifier=payload.recipient, amount=payload.amount
        )
    except transfer_service.TransferError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return txn


@router.get("/transactions", response_model=List[schemas.TransactionOut])
def list_transactions(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = wallet_service.get_wallet_for_user(db, current_user)
    return (
        db.query(models.Transaction)
        .filter(
            or_(
                models.Transaction.from_wallet_id == wallet.id,
                models.Transaction.to_wallet_id == wallet.id,
            )
        )
        .order_by(models.Transaction.created_at.desc())
        .all()
    )
