"""Provider abstraction.

Each data domain has a small interface. Concrete providers (Yahoo, FRED, Google
News RSS, SEC EDGAR, Finnhub, Anthropic...) implement them, and the registry
(`registry.py`) chains them with fallbacks and tracks provider health so the UI
can show which source served a section and whether it is degraded.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import pandas as pd


class ProviderError(RuntimeError):
    """Upstream failure (network, rate limit, bad payload)."""


class NotFoundError(ProviderError):
    """Symbol / series does not exist upstream."""


@dataclass
class QuoteData:
    symbol: str
    price: float | None
    prev_close: float | None
    change: float | None
    change_pct: float | None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    currency: str | None = None
    name: str | None = None
    exchange: str | None = None
    market_state: str | None = None   # REGULAR | PRE | POST | CLOSED ...
    quote_time: dt.datetime | None = None
    delay_minutes: int | None = None
    realtime: bool = False
    source: str = ""
    pre_market_price: float | None = None
    post_market_price: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["quote_time"] = self.quote_time.isoformat() if self.quote_time else None
        return d


@dataclass
class NewsItem:
    title: str
    url: str | None
    source: str | None
    published_at: dt.datetime | None
    provider: str
    summary: str | None = None
    tickers: list[str] = field(default_factory=list)
    category: str | None = None
    language: str | None = None
    scope: str = "market"


@dataclass
class SymbolMatch:
    symbol: str
    name: str
    exchange: str | None = None
    market: str | None = None
    type: str | None = None
    name_ko: str | None = None
    score: float = 0.0


class PriceProvider(Protocol):
    name: str

    def history(self, symbol: str, *, period: str | None = None, start: dt.date | None = None,
                interval: str = "1d", prepost: bool = False) -> pd.DataFrame: ...

    def history_many(self, symbols: list[str], *, period: str = "2y", interval: str = "1d") -> dict[str, pd.DataFrame]: ...

    def quote(self, symbol: str) -> QuoteData: ...


class FundamentalsProvider(Protocol):
    name: str

    def profile(self, symbol: str) -> dict[str, Any]: ...

    def earnings(self, symbol: str) -> list[dict[str, Any]]: ...


class MacroProvider(Protocol):
    name: str

    def series(self, series_id: str, start: dt.date | None = None) -> pd.Series: ...


class NewsProvider(Protocol):
    name: str

    def search(self, query: str, *, language: str = "en", limit: int = 30) -> list[NewsItem]: ...


# ----------------------------------------------------------------- health
class ProviderHealth:
    """Thread-safe record of last success / failure per provider (shown in UI)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = {}

    def ok(self, name: str) -> None:
        with self._lock:
            s = self._state.setdefault(name, {"failures": 0})
            s.update(status="ok", last_success=time.time(), failures=0)

    def fail(self, name: str, err: Exception | str) -> None:
        with self._lock:
            s = self._state.setdefault(name, {"failures": 0})
            s.update(status="degraded", last_error=str(err)[:300], last_failure=time.time(),
                     failures=s.get("failures", 0) + 1)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._state.items()}


health = ProviderHealth()
