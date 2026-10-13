from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All config comes from environment variables (see .env.example at the repo root)."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_env: str = "development"
    brand_name: str = "FlyFit"
    public_site_url: str = "http://localhost:3000"

    database_url: str
    database_url_direct: str | None = None  # migrations; falls back to database_url

    hold_minutes: int = 15
    log_level: str = "INFO"

    @field_validator("database_url", "database_url_direct")
    @classmethod
    def use_psycopg3(cls, url: str | None) -> str | None:
        """Neon and Render hand out plain postgresql:// URLs; SQLAlchemy would read that as
        psycopg2. Point them at psycopg 3, which is the driver installed."""
        if url and url.startswith(("postgresql://", "postgres://")):
            return "postgresql+psycopg://" + url.split("://", 1)[1]
        return url

    @property
    def migrations_database_url(self) -> str:
        return self.database_url_direct or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
