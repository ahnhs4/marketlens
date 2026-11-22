"""Application settings, loaded from environment variables / .env.

Credentials are never hard-coded: every key is optional and the feature that
needs it degrades gracefully (and says so in the UI) when it is missing.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Honest client identification for public endpoints (no browser impersonation).
APP_USER_AGENT = "MarketLens/0.1 python-httpx/0.28"
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- core -----------------------------------------------------------------
    env: str = Field(default="development", validation_alias=AliasChoices("APP_ENV", "ENV"))
    database_url: str = Field(default=f"sqlite:///{BACKEND_DIR / 'data' / 'marketlens.db'}")
    redis_url: str | None = None
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- auth / security --------------------------------------------------------
    secret_key: str = Field(default="dev-only-change-me", validation_alias=AliasChoices("SECRET_KEY", "APP_SECRET_KEY"))
    auth_required: bool = False  # local single-user mode when False
    session_ttl_hours: int = 24 * 14
    rate_limit_per_minute: int = 240

    # --- data providers -----------------------------------------------------------
    # Prices/fundamentals/search come from Yahoo Finance (no key). Optional keys below
    # unlock official APIs or extra coverage.
    fred_api_key: str | None = Field(default=None, validation_alias=AliasChoices("FRED_API_KEY", "MACRO_API_KEY"))
    finnhub_api_key: str | None = Field(default=None, validation_alias=AliasChoices("FINNHUB_API_KEY", "NEWS_API_KEY"))
    sec_user_agent: str = "MarketLens/0.1 research-app contact@example.com"
    openfigi_api_key: str | None = None  # optional: CUSIP -> ticker lookups for 13F (works without, just slower)
    # OpenDART (금융감독원 전자공시) key: Korean financial statements, dividends. Empty = DART features show "Data unavailable".
    dart_api_key: str | None = Field(default=None, validation_alias=AliasChoices("DART_API_KEY", "OPENDART_API_KEY"))

    # --- AI analysis --------------------------------------------------------------
    anthropic_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "AI_API_KEY"))
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "low"  # classification is a high-volume, low-latency route
    ai_refusal_fallback: bool = True

    # --- notifications (optional) ----------------------------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    discord_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # --- jobs -----------------------------------------------------------------
    scheduler_enabled: bool = True
    universe_scan_minutes: int = 30
    alert_check_minutes: int = 5

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.env.lower() in ("prod", "production")

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
