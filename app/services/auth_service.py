from sqlalchemy.orm import Session

from .. import models, metrics, schemas
from ..config import settings
from ..logging_config import app_logger
from ..security import hash_password, verify_password, create_access_token


class AuthError(Exception):
    """Raised for any auth failure the router should turn into a 4xx."""


def signup(db: Session, payload: schemas.SignupRequest) -> models.User:
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        metrics.auth_failures_total.labels(reason="duplicate_signup").inc()
        app_logger.warning(
            "Signup rejected: email already registered",
            extra={"event": "signup_rejected", "reason": "duplicate_signup"},
        )
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

    metrics.refresh_business_gauges(db)
    app_logger.info(
        "New user signed up",
        extra={"event": "user_signup", "user_id": user.id, "wallet_id": wallet.id},
    )
    return user


def login(db: Session, email: str, password: str) -> str:
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        metrics.auth_failures_total.labels(reason="unknown_email").inc()
        app_logger.warning(
            "Failed login: unknown email",
            extra={"event": "login_failed", "reason": "unknown_email"},
        )
        raise AuthError("Incorrect email or password")

    if not verify_password(password, user.password_hash):
        metrics.auth_failures_total.labels(reason="bad_password").inc()
        app_logger.warning(
            "Failed login: bad password",
            extra={"event": "login_failed", "reason": "bad_password", "user_id": user.id},
        )
        raise AuthError("Incorrect email or password")

    app_logger.info("User logged in", extra={"event": "login_success", "user_id": user.id})
    return create_access_token(subject=user.id)
