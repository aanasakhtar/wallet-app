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


settings = Settings()
