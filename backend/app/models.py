"""Relational schema. Every timestamp column stores UTC.

Raw provider data (prices, news, macro observations) and derived/AI data
(news_analysis, ai_summaries, signals) live in separate tables so derived
results can be regenerated or audited against their inputs.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import false as sa_false
from sqlalchemy.types import TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite stores naive UTC, Postgres timestamptz)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        value = value.astimezone(dt.timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value


# --------------------------------------------------------------------------- users
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    settings: Mapped[UserSettings | None] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    profile: Mapped[dict] = mapped_column(JSONType, default=dict)       # risk tolerance, horizon, markets, sectors, max DD
    weights: Mapped[dict] = mapped_column(JSONType, default=dict)       # technical component weights, macro/tech blend
    preferences: Mapped[dict] = mapped_column(JSONType, default=dict)   # UI prefs (color convention, rsi period, ...)
    last_visit_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())
    previous_visit_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="settings")


# ------------------------------------------------------------------ watchlists
class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    items: Mapped[list[WatchlistItem]] = relationship(back_populates="watchlist", cascade="all, delete-orphan",
                                                      order_by="WatchlistItem.added_at")
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(String(500))
    added_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")
    __table_args__ = (UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_item"),)


# ---------------------------------------------------------------- market data
class Stock(Base):
    """Symbol master (US from SEC/Yahoo, KR from KRX KIND)."""
    __tablename__ = "stocks"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)  # Yahoo-style: NVDA, 005930.KS
    name: Mapped[str] = mapped_column(String(255))
    name_ko: Mapped[str | None] = mapped_column(String(255))
    market: Mapped[str] = mapped_column(String(8), index=True)  # US | KR | INDEX | FX ...
    exchange: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(8))
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(150))
    country: Mapped[str | None] = mapped_column(String(64))
    kr_code: Mapped[str | None] = mapped_column(String(12), index=True)
    cik: Mapped[str | None] = mapped_column(String(12))
    aliases: Mapped[list] = mapped_column(JSONType, default=list)
    source: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Quote(Base):
    __tablename__ = "quotes"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    price: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    change: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    market_state: Mapped[str | None] = mapped_column(String(16))
    quote_time: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())
    delay_minutes: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class DailyPrice(Base):
    """Split-adjusted OHLC (as displayed by exchanges/brokers) plus the dividend-adjusted
    close used for total-return backtests."""
    __tablename__ = "daily_prices"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    dividends: Mapped[float | None] = mapped_column(Float, default=0.0)
    splits: Mapped[float | None] = mapped_column(Float, default=0.0)
    source: Mapped[str | None] = mapped_column(String(32))


class IntradayPrice(Base):
    __tablename__ = "intraday_prices"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(UTCDateTime(), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    session: Mapped[str | None] = mapped_column(String(8))  # pre | regular | post


class Fundamental(Base):
    __tablename__ = "fundamentals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    as_of: Mapped[dt.date] = mapped_column(Date)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    source: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("symbol", "as_of", name="uq_fundamentals_symbol_date"),)


class Earnings(Base):
    __tablename__ = "earnings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    earnings_date: Mapped[dt.datetime] = mapped_column(UTCDateTime())
    eps_estimate: Mapped[float | None] = mapped_column(Float)
    eps_actual: Mapped[float | None] = mapped_column(Float)
    surprise_pct: Mapped[float | None] = mapped_column(Float)
    revenue_estimate: Mapped[float | None] = mapped_column(Float)
    revenue_actual: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("symbol", "earnings_date", name="uq_earnings_symbol_date"),)


class MacroData(Base):
    __tablename__ = "macro_data"
    series_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(32))
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ----------------------------------------------------------------------- news
class News(Base):
    __tablename__ = "news"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    cluster_key: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(16), default="market")  # market | stock | filing
    category: Mapped[str | None] = mapped_column(String(40))
    tickers: Mapped[list] = mapped_column(JSONType, default=list)
    language: Mapped[str | None] = mapped_column(String(8))
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), index=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    analyses: Mapped[list[NewsAnalysis]] = relationship(back_populates="news", cascade="all, delete-orphan")


class NewsAnalysis(Base):
    """Derived classification for one news item - kept apart from the raw article."""
    __tablename__ = "news_analysis"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(16))  # ai | rules
    model: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(40))
    sentiment: Mapped[str | None] = mapped_column(String(16))
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    impact: Mapped[str | None] = mapped_column(String(16))
    importance: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    event_type: Mapped[str | None] = mapped_column(String(40))
    sectors: Mapped[list] = mapped_column(JSONType, default=list)
    tickers: Mapped[list] = mapped_column(JSONType, default=list)
    chain: Mapped[list] = mapped_column(JSONType, default=list)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    news: Mapped[News] = relationship(back_populates="analyses")


class AISummary(Base):
    """AI (or rule-based) narrative output with provenance: when and from which inputs."""
    __tablename__ = "ai_summaries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), index=True)  # market | stock:NVDA | briefing
    method: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSONType, default=dict)
    input_ids: Mapped[list] = mapped_column(JSONType, default=list)
    data_from: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())
    data_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ------------------------------------------------------------ derived analytics
class TechnicalIndicator(Base):
    __tablename__ = "technical_indicators"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)


class Signal(Base):
    """Score snapshot per symbol per trading day - powers change detection."""
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    as_of: Mapped[dt.date] = mapped_column(Date)
    technical: Mapped[float | None] = mapped_column(Float)
    macro: Mapped[float | None] = mapped_column(Float)
    fundamental: Mapped[float | None] = mapped_column(Float)
    overall: Mapped[float | None] = mapped_column(Float)
    signal: Mapped[str | None] = mapped_column(String(16))
    horizons: Mapped[dict] = mapped_column(JSONType, default=dict)
    components: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("symbol", "as_of", name="uq_signal_symbol_date"),)


class Pattern(Base):
    __tablename__ = "patterns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    pattern: Mapped[str] = mapped_column(String(40))
    detected_at: Mapped[dt.date] = mapped_column(Date)
    direction: Mapped[str | None] = mapped_column(String(8))
    confidence: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("symbol", "pattern", "detected_at", name="uq_pattern"),)


class Backtest(Base):
    __tablename__ = "backtests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    symbol: Mapped[str] = mapped_column(String(24))
    strategy: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    start_date: Mapped[dt.date | None] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    check: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(8))
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    detected_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ------------------------------------------------------------------ portfolio
class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    base_currency: Mapped[str] = mapped_column(String(8), default="USD")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    positions: Mapped[list[PortfolioPosition]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(24))
    quantity: Mapped[float] = mapped_column(Float)
    avg_cost: Mapped[float] = mapped_column(Float)
    # True when avg_cost is a stand-in (e.g. imported from a balance certificate that has no purchase
    # price): P/L is then not reported as a return and P/L-based rules are skipped.
    avg_cost_estimated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    # For foreign-currency holdings: average cost per share in KRW as charged at purchase (the FX rate of
    # each buy baked in). With avg_cost (local currency) it splits KRW P/L into price P/L and FX P/L.
    avg_cost_krw: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    account: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")


# --------------------------------------------------------------------- alerts
class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(24))
    condition_type: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    channels: Mapped[list] = mapped_column(JSONType, default=lambda: ["web"])
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=24 * 60)
    last_triggered_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    events: Mapped[list[AlertEvent]] = relationship(back_populates="alert", cascade="all, delete-orphan")


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    triggered_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    delivered: Mapped[dict] = mapped_column(JSONType, default=dict)
    read: Mapped[bool] = mapped_column(Boolean, default=False)

    alert: Mapped[Alert] = relationship(back_populates="events")


# --------------------------------------------------------------------------- 13F
class Filing13F(Base):
    """One manager's 13F-HR for a quarter, with its amendments applied. `accession` = the latest document used."""
    __tablename__ = "filings_13f"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cik: Mapped[int] = mapped_column(Integer, index=True)
    manager: Mapped[str] = mapped_column(String(200))
    period: Mapped[dt.date] = mapped_column(Date)
    filed: Mapped[dt.date] = mapped_column(Date)
    accession: Mapped[str] = mapped_column(String(30), unique=True)
    equity_value: Mapped[float] = mapped_column(Float)  # USD, long share positions only (no options, no bonds)
    positions: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("cik", "period", name="uq_13f_cik_period"),)


class Holding13F(Base):
    """Share positions of one filing, summed per CUSIP across sub-managers."""
    __tablename__ = "holdings_13f"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings_13f.id", ondelete="CASCADE"), index=True)
    cusip: Mapped[str] = mapped_column(String(9), index=True)
    issuer: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(100))
    value: Mapped[float] = mapped_column(Float)   # USD
    shares: Mapped[float] = mapped_column(Float)


class CusipMap(Base):
    """CUSIP -> ticker (OpenFIGI)."""
    __tablename__ = "cusip_map"
    cusip: Mapped[str] = mapped_column(String(9), primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(200))
    security_type: Mapped[str | None] = mapped_column(String(60))
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


# --------------------------------------------------------------------------- paper trading
class PaperAccount(Base):
    """Simulated USD account: no broker, no real orders. One per user."""
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    start_cash: Mapped[float] = mapped_column(Float, default=10_000.0)
    cash: Mapped[float] = mapped_column(Float, default=10_000.0)
    fee_bps: Mapped[float] = mapped_column(Float, default=25.0)        # per side
    slippage_bps: Mapped[float] = mapped_column(Float, default=5.0)
    daily_loss_limit_pct: Mapped[float] = mapped_column(Float, default=3.0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)       # emergency stop: no automated orders at all
    halted_day: Mapped[dt.date | None] = mapped_column(Date)            # daily loss limit hit: exits only, that day
    day: Mapped[dt.date | None] = mapped_column(Date)
    day_start_equity: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PaperStrategy(Base):
    __tablename__ = "paper_strategies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(40))
    interval: Mapped[str] = mapped_column(String(8), default="15m")
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    symbols: Mapped[list] = mapped_column(JSONType, default=list)
    max_position_usd: Mapped[float] = mapped_column(Float, default=1000.0)
    flatten_at_close: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[dict] = mapped_column(JSONType, default=dict)         # last evaluated bar per symbol
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer)          # None = manual
    symbol: Mapped[str] = mapped_column(String(24))
    qty: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)                    # includes the buy fee
    stop_price: Mapped[float | None] = mapped_column(Float)
    opened_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(24))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)                        # fill price incl. slippage
    fee: Mapped[float] = mapped_column(Float)
    realized_pl: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(300))
    ts: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class PaperEvent(Base):
    """Why the engine did or did not act (signals, skips, risk stops)."""
    __tablename__ = "paper_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer)
    symbol: Mapped[str | None] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(String(400))
    ts: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class PaperEquity(Base):
    __tablename__ = "paper_equity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)


# --------------------------------------------------------------------------- OpenDART (Korean filings)
class DartCorp(Base):
    """DART corp_code <-> listed stock code."""
    __tablename__ = "dart_corps"
    corp_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    corp_name: Mapped[str] = mapped_column(String(200))
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    modify_date: Mapped[str | None] = mapped_column(String(8))
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class DartFinancial(Base):
    """One company's filing for one period, normalised.

    kind = main (BS/IS main accounts, fetched for the whole market at once) | full (complete statements incl. cash flow)
    | div (dividends from the annual report). `data` = {} records "DART has nothing for this period yet".
    """
    __tablename__ = "dart_financials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corp_code: Mapped[str] = mapped_column(String(8), index=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    year: Mapped[int] = mapped_column(Integer)
    reprt: Mapped[str] = mapped_column(String(5))
    kind: Mapped[str] = mapped_column(String(8))
    fs_div: Mapped[str | None] = mapped_column(String(3))
    rcept_no: Mapped[str | None] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("corp_code", "year", "reprt", "kind", name="uq_dart_fin_period"),)


Index("ix_signals_symbol_asof", Signal.symbol, Signal.as_of.desc())
Index("ix_news_scope_published", News.scope, News.published_at.desc())
