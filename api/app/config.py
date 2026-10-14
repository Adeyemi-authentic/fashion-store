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
    # Shared with the storefront proxy (X-Proxy-Auth). Only requests carrying it may tell us
    # the client IP via X-Forwarded-For; anyone else's X-Forwarded-For is ignored.
    proxy_shared_secret: str = ""
    rate_limit_checkout_per_min: int = 10
    rate_limit_login_per_min: int = 10
    paystack_secret_key: str = ""  # sk_test_...; also the webhook signing key
    resend_api_key: str = ""  # empty: emails are written to email_outbox_dir instead
    email_from: str = "FlyFit <orders@example.com>"
    admin_email: str = "admin@example.com"
    email_outbox_dir: str = str(REPO_ROOT / "api" / ".emails")
    # The free tier runs the outbox worker inside the API process (PLAN.md section 5). Safe
    # with several processes too: SKIP LOCKED shares the jobs out.
    worker_in_api: bool = True
    worker_lease_seconds: float = 60.0
    worker_poll_seconds: float = 1.0
    # AI (Phase 6). Empty keys: a local stand-in embedder and no LLM query parsing, so the
    # app still runs; tests always use fakes and never call these APIs.
    anthropic_api_key: str = ""
    llm_model: str = "claude-haiku-5-5"
    voyage_api_key: str = ""
    embedding_model: str = "voyage-4"  # 1024 dimensions, matching products.embedding
    rate_limit_search_per_min: int = 30
    log_level: str = "INFO"
    sentry_dsn: str = ""  # empty: Sentry off
    metrics_token: str = ""  # set in production: /metrics then needs "Bearer <token>"

    @field_validator("database_url", "database_url_direct")
    @classmethod
    def use_psycopg3(cls, url: str | None) -> str | None:
        """Neon and Render hand out plain postgresql:// URLs; SQLAlchemy would read that as
        psycopg2. Point them at psycopg 3, which is the driver installed."""
        if url and url.startswith(("postgresql://", "postgres://")):
            return "postgresql+psycopg://" + url.split("://", 1)[1]
        return url

    @property
    def cookie_secure(self) -> bool:
        """Secure cookies need HTTPS. Safari drops them on http://localhost, so dev is off."""
        return self.public_site_url.startswith("https://")

    @property
    def migrations_database_url(self) -> str:
        return self.database_url_direct or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
