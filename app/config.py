"""
Central configuration. Everything is read from environment variables so the
same code runs unmodified locally (SQLite), in Docker Compose (Postgres),
and later when Phase 2 adds Prometheus/ELK env vars.
"""
import os


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./wallet.db")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
    default_currency: str = os.getenv("DEFAULT_CURRENCY", "PKR")
    cashback_rate: float = float(os.getenv("CASHBACK_RATE", "0.0"))  # e.g. 0.01 = 1%
    admin_token: str = os.getenv("ADMIN_TOKEN", "local-admin-token")
    # Rate limiting (single-instance, in memory). "LIMIT/WINDOW_SECONDS".
    rate_limit_enabled: bool = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes")
    rate_limit_transfers: str = os.getenv("RATE_LIMIT_TRANSFERS", "20/60")
    # How long an Idempotency-Key is remembered.
    idempotency_ttl_seconds: int = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", str(24 * 3600)))


settings = Settings()
