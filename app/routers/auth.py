from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import schemas
from ..api_docs import errors
from ..database import get_db
from ..services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED,
             summary="Register a user and create their wallet",
             responses=errors(409, 422))
def signup(payload: schemas.SignupRequest, db: Session = Depends(get_db)):
    return auth_service.signup(db, payload)


@router.post("/login", response_model=schemas.TokenResponse,
             summary="Exchange email + password for a bearer token",
             description="Form fields per the OAuth2 password flow: `username` is the account email.",
             responses=errors(401, 422))
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return schemas.TokenResponse(access_token=auth_service.login(db, form_data.username, form_data.password))
