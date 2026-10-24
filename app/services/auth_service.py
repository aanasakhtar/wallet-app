from typing import Optional

from sqlalchemy.orm import Session

from .. import models, metrics, schemas
from ..config import settings
from ..errors import (EmailAlreadyRegistered, InvalidCredentials, PhoneAlreadyRegistered,
                      Unauthenticated)
from ..logging_config import app_logger
from ..security import create_access_token, decode_access_token, hash_password, verify_password


def authenticate_token(db: Session, token: Optional[str]) -> models.User:
    """Resolve a bearer token to a user. Used by BOTH the HTTP dependency and the
    gRPC handler, so the two protocols authenticate identically."""
    user_id = decode_access_token(token) if token else None
    user = db.query(models.User).filter(models.User.id == user_id).first() if user_id else None
    if user is None:
        raise Unauthenticated()
    return user


def signup(db: Session, payload: schemas.SignupRequest) -> models.User:
    if db.query(models.User).filter(models.User.email == payload.email).first():
        metrics.auth_failures_total.labels(reason="duplicate_signup").inc()
        app_logger.warning("Signup rejected: email already registered",
                           extra={"event": "signup_rejected", "reason": "duplicate_signup", "field": "email"})
        raise EmailAlreadyRegistered()

    # The phone column is UNIQUE: without this check a duplicate phone surfaced as an
    # unhandled IntegrityError, i.e. a 500 (found while reviewing the endpoints for A2).
    if payload.phone and db.query(models.User).filter(models.User.phone == payload.phone).first():
        metrics.auth_failures_total.labels(reason="duplicate_signup").inc()
        app_logger.warning("Signup rejected: phone already registered",
                           extra={"event": "signup_rejected", "reason": "duplicate_signup", "field": "phone"})
        raise PhoneAlreadyRegistered()

    user = models.User(name=payload.name, email=payload.email, phone=payload.phone,
                       password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()  # get user.id before creating the wallet

    wallet = models.Wallet(user_id=user.id, balance=0.0, currency=settings.default_currency)
    db.add(wallet)
    db.commit()
    db.refresh(user)

    app_logger.info("New user signed up",
                    extra={"event": "user_signup", "user_id": user.id, "wallet_id": wallet.id})
    return user


def login(db: Session, email: str, password: str) -> str:
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        metrics.auth_failures_total.labels(reason="unknown_email").inc()
        app_logger.warning("Failed login: unknown email",
                           extra={"event": "login_failed", "reason": "unknown_email"})
        raise InvalidCredentials()

    if not verify_password(password, user.password_hash):
        metrics.auth_failures_total.labels(reason="bad_password").inc()
        app_logger.warning("Failed login: bad password",
                           extra={"event": "login_failed", "reason": "bad_password", "user_id": user.id})
        raise InvalidCredentials()

    app_logger.info("User logged in", extra={"event": "login_success", "user_id": user.id})
    return create_access_token(subject=user.id)
