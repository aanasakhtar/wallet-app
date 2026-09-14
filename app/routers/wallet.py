from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..dependencies import get_current_user
from ..services import wallet_service

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/balance", response_model=schemas.WalletOut)
def get_balance(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = wallet_service.get_wallet_for_user(db, current_user)
    return wallet


@router.post("/topup", response_model=schemas.TransactionOut)
def topup(
    payload: schemas.TopupRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    txn = wallet_service.topup(db, current_user, payload.amount)
    return txn
