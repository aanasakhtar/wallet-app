from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..logging_config import app_logger
from ..security import hash_password, verify_password, create_access_token


class AuthError(Exception):
    """Raised for any auth failure the router should turn into a 4xx."""


def signup(db: Session, payload: schemas.SignupRequest) -> models.User:
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        app_logger.warning("Signup rejected: email already registered")
        raise AuthError("Email already registered")

    user = models.User(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()  # get user.id before creating the wallet

    wallet = models.Wallet(user_id=user.id, balance=0.0, currency=settings.default_currency)
    db.add(wallet)
    db.commit()
    db.refresh(user)

    app_logger.info("New user signed up: user_id=%s", user.id)
    return user


def login(db: Session, email: str, password: str) -> str:
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        app_logger.warning("Failed login attempt for email=%s", email)
        raise AuthError("Incorrect email or password")

    app_logger.info("User logged in: user_id=%s", user.id)
    return create_access_token(subject=user.id)
