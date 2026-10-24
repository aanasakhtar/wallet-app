from typing import List, Optional

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas, validation
from ..api_docs import errors
from ..database import get_db
from ..dependencies import get_current_user
from ..rate_limit import enforce_transfer_rate_limit
from ..services import transfer_service

router = APIRouter(tags=["transfers"])


@router.post(
    "/transfers",
    response_model=schemas.TransactionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Send money to another user",
    description=(
        "Moves `amount` from the caller's wallet to the recipient's.\n\n"
        "**Idempotency:** the `Idempotency-Key` header is required. Repeating a request with the same key "
        "and body returns the original transfer (201, `Idempotent-Replayed: true`) and moves no money. "
        "The same key with a different body is a 422.\n\n"
        "**Rate limit:** limited per user; excess calls get 429 with `Retry-After`."
    ),
    responses={
        201: {"headers": {
            "Location": {"description": "URL of the created transfer", "schema": {"type": "string"}},
            "Idempotent-Replayed": {"description": "`true` when this response is a replay of an earlier request",
                                    "schema": {"type": "string"}},
            "RateLimit-Limit": {"schema": {"type": "integer"}, "description": "Requests allowed per window"},
            "RateLimit-Remaining": {"schema": {"type": "integer"}, "description": "Requests left in the window"},
        }},
        **errors(401, 404, 409, 422, 429),
    },
)
def create_transfer(
    payload: schemas.TransferRequest,
    response: Response,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", pattern=validation.IDEMPOTENCY_KEY_PATTERN,
        description="Client-generated unique key (a UUID is ideal), 8-64 chars of A-Z a-z 0-9 _ -",
    ),
    current_user: models.User = Depends(enforce_transfer_rate_limit),
    db: Session = Depends(get_db),
):
    txn, replayed = transfer_service.create_transfer(
        db, current_user, payload.recipient, payload.amount, idempotency_key)
    response.headers["Location"] = f"/transfers/{txn.id}"
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return txn


@router.get("/transfers/{transfer_id}", response_model=schemas.TransactionOut,
            summary="Fetch one transfer you sent or received", responses=errors(401, 404))
def get_transfer(transfer_id: str, current_user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return transfer_service.get_transfer(db, current_user, transfer_id)


@router.get("/transactions", response_model=List[schemas.TransactionOut],
            summary="Your transaction history, newest first", responses=errors(401, 422))
def list_transactions(
    limit: int = Query(50, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
    type: Optional[models.TransactionType] = Query(None, description="Filter by type"),
    status: Optional[models.TransactionStatus] = Query(None, description="Filter by status"),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return transfer_service.list_transactions(db, current_user, limit, offset, type, status)
