from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..api_docs import errors
from ..database import get_db
from ..dependencies import get_current_user
from ..services import wallet_service

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/balance", response_model=schemas.WalletOut, summary="Current wallet balance",
            responses=errors(401))
def get_balance(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return wallet_service.get_wallet_for_user(db, current_user)


@router.post("/topups", response_model=schemas.TransactionOut, status_code=status.HTTP_201_CREATED,
             summary="Add money to your own wallet (simulated)",
             description="Creates a top-up transaction. Renamed from the verb-style `/wallet/topup`: "
                         "the URL names the collection, POST creates one member.",
             responses=errors(401, 422))
def create_topup(payload: schemas.TopupRequest, current_user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return wallet_service.topup(db, current_user, payload.amount)
