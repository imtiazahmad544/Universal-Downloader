"""Application configuration via environment variables (pydantic-settings)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "sqlite:///./universal_downloader.db"

    # JWT auth
    JWT_SECRET: str = "changeme"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 30

    # Login rate limiting (sliding window per client IP)
    LOGIN_RATE_LIMIT_ATTEMPTS: int = 10
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Optional Redis (future worker queue); blank = not configured
    REDIS_URL: str = ""

    # Initial admin bootstrap (used by app/seed.py)
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "changeme"

    # Batch/discovery tuning
    BATCH_DEFAULT_SIZE: int = 20
    DISCOVERY_MAX_ITEMS: int = 500

    # Worker loops
    SCHEDULER_INTERVAL_SECONDS: int = 300
    DISCOVERY_POLL_SECONDS: int = 30
    DEFAULT_TIMEZONE: str = "UTC"

    # Logging
    LOG_LEVEL: str = "INFO"

    # Provider API credentials (future; placeholders)
    TIKTOK_API_KEY: str = ""
    TIKTOK_API_SECRET: str = ""
    INSTAGRAM_API_TOKEN: str = ""


settings = Settings()
