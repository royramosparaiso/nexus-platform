"""Platform settings — read from env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for a Platform instance.

    Env prefix: `PLATFORM_`. So DATABASE_URL is PLATFORM_DATABASE_URL.
    """

    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Postgres — required in production, defaults to a local sqlite fallback
    # for tests so that CI without a DB still runs.
    database_url: str = "postgresql+psycopg://nexus:nexus@localhost:5432/nexus"

    # One-time bootstrap token. Console reads this out-of-band from the
    # deployment env and delivers it back on /_bootstrap.
    bootstrap_token: str | None = None

    # After bootstrap, the Platform keypair is persisted in the DB. This env
    # var can override for local dev.
    platform_private_key_path: str | None = None


def get_settings() -> Settings:
    return Settings()
