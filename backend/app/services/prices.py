"""Price data access: cache -> database -> provider (incremental refresh).

Daily bars are persisted in `daily_prices`; on each refresh only the most recent
week is re-downloaded and upserted (it also overwrites today's partial bar).
Every series passes data-quality validation before it reaches the analytics.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select

from ..analysis.data_quality import validate
from ..cache import cache, cached
from ..db import SessionLocal
from ..models import DailyPrice, DataQualityIssue, Quote
from ..providers.base import NotFoundError, ProviderError, QuoteData
from ..providers.yahoo import yahoo
from . import clock
from .store import upsert, utcnow

log = logging.getLogger(__name__)
COLS = ["open", "high", "low", "close", "adj_close", "volume", "dividends", "splits"]


@dataclass
class PriceSeries:
    symbol: str
    df: pd.DataFrame
    quality: dict
    fetched_at: dt.datetime
    source: str

    def meta(self) -> dict:
        return {"symbol": self.symbol, "rows": len(self.df), "source": self.source,
                "fetched_at": self.fetched_at.isoformat(), "quality": self.quality,
                "first_date": str(self.df.index[0].date()) if len(self.df) else None,
                "last_date": str(self.df.index[-1].date()) if len(self.df) else None}


def _ttl(symbol: str) -> int:
    return 120 if clock.is_open(symbol) else 1800


def _load_db(symbol: str) -> pd.DataFrame:
    with SessionLocal() as db:
        rows = db.execute(select(DailyPrice).where(DailyPrice.symbol == symbol).order_by(DailyPrice.date)).scalars().all()
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame([{c: getattr(r, c) for c in COLS} | {"date": pd.Timestamp(r.date)} for r in rows]).set_index("date")
    return df.astype(float)


def _save_db(symbol: str, df: pd.DataFrame, source: str = "yahoo") -> None:
    rows = [{"symbol": symbol, "date": ts.date(), **{c: (None if pd.isna(r[c]) else float(r[c])) for c in COLS},
             "source": source} for ts, r in df.iterrows()]
    with SessionLocal() as db:
        upsert(db, DailyPrice, rows, ["symbol", "date"])
        db.commit()


def _record_quality(symbol: str, report: dict) -> None:
    issues = [i for i in report.get("issues", []) if i["severity"] in ("warn", "error")]
    if not issues:
        return
    try:
        with SessionLocal() as db:
            for i in issues:
                db.add(DataQualityIssue(symbol=symbol, check=i["check"], severity=i["severity"], details=i))
            db.commit()
    except Exception as e:  # never block analysis on bookkeeping
        log.debug("quality log failed: %s", e)


def _fetch_daily(symbol: str, years: int) -> PriceSeries:
    stored = _load_db(symbol)
    want_start = (pd.Timestamp.today() - pd.DateOffset(years=years)).normalize()
    source = "yahoo"
    try:
        if stored.empty or stored.index[0] > want_start + pd.Timedelta(days=10):
            period = "max" if years > 10 else f"{years}y"
            fresh = yahoo.history(symbol, period=period, interval="1d")
            df = fresh
        else:
            start = (stored.index[-1] - pd.Timedelta(days=7)).date()
            fresh = yahoo.history(symbol, start=start, interval="1d")
            # A split/dividend inside the refresh window rescales adjusted history: refetch fully.
            overlap = stored.index.intersection(fresh.index)
            if len(overlap) and (abs(stored.loc[overlap, "close"] / fresh.loc[overlap, "close"] - 1) > 0.005).any():
                fresh = yahoo.history(symbol, period=f"{years}y", interval="1d")
                df = fresh
            else:
                df = pd.concat([stored[stored.index < fresh.index[0]], fresh])
        _save_db(symbol, fresh)
    except NotFoundError:
        raise
    except ProviderError as e:
        if stored.empty:
            raise
        log.warning("using stored prices for %s (provider error: %s)", symbol, e)
        df, source = stored, "database (provider unavailable)"
    df = df[df.index >= want_start] if years < 30 else df
    clean_df, rep = validate(df, symbol, daily=True)
    _record_quality(symbol, rep.to_dict())
    return PriceSeries(symbol, clean_df, rep.to_dict(), utcnow(), source)


def get_daily(symbol: str, years: int = 10) -> PriceSeries:
    return cached(f"px:d:{symbol}:{years}", _ttl(symbol), lambda: _fetch_daily(symbol, years))


def get_intraday(symbol: str, interval: str = "5m", period: str = "5d") -> pd.DataFrame:
    def load():
        df = yahoo.history(symbol, period=period, interval=interval, prepost=not symbol.endswith((".KS", ".KQ")))
        clean_df, _ = validate(df, symbol, daily=False)
        return clean_df
    return cached(f"px:i:{symbol}:{interval}:{period}", 60 if clock.is_open(symbol) else 900, load)


def put_many_daily(frames: dict[str, pd.DataFrame], years: int = 2) -> None:
    """Seed per-symbol caches/DB from a batch download (universe scans)."""
    now = utcnow()
    for sym, df in frames.items():
        clean_df, rep = validate(df, sym, daily=True)
        cache.set(f"px:d:{sym}:{years}", PriceSeries(sym, clean_df, rep.to_dict(), now, "yahoo (batch)"), _ttl(sym))
        try:
            _save_db(sym, df)
        except Exception as e:
            log.debug("save failed %s: %s", sym, e)


# ------------------------------------------------------------------ quotes
def _quote_from_db(symbol: str) -> QuoteData | None:
    with SessionLocal() as db:
        q = db.get(Quote, symbol)
    if not q or q.price is None:
        return None
    return QuoteData(symbol=symbol, price=q.price, prev_close=q.prev_close, change=q.change, change_pct=q.change_pct,
                     open=q.open, high=q.high, low=q.low, volume=q.volume, currency=q.currency,
                     market_state=q.market_state, quote_time=q.quote_time, delay_minutes=q.delay_minutes,
                     source=f"{q.source} (stored, provider unavailable)")


def _save_quote(q: QuoteData) -> None:
    try:
        with SessionLocal() as db:
            upsert(db, Quote, [{"symbol": q.symbol, "price": q.price, "prev_close": q.prev_close, "change": q.change,
                                "change_pct": q.change_pct, "open": q.open, "high": q.high, "low": q.low,
                                "volume": q.volume, "currency": q.currency, "market_state": q.market_state,
                                "quote_time": q.quote_time, "delay_minutes": q.delay_minutes, "source": q.source,
                                "fetched_at": utcnow()}], ["symbol"])
            db.commit()
    except Exception as e:
        log.debug("quote save failed: %s", e)


def get_quote(symbol: str) -> QuoteData:
    def load():
        try:
            q = yahoo.quote(symbol)
            _save_quote(q)
            return q
        except NotFoundError:
            raise
        except ProviderError:
            q = _quote_from_db(symbol)
            if q is None:
                raise
            return q
    return cached(f"q:{symbol}", 20 if clock.is_open(symbol) else 300, load)


def get_quotes(symbols: list[str]) -> dict[str, QuoteData | None]:
    out: dict[str, QuoteData | None] = {}
    missing = []
    for s in symbols:
        hit = cache.get(f"q:{s}")
        if hit is not None:
            out[s] = hit
        else:
            missing.append(s)
    if missing:
        res = yahoo.quotes(missing)
        for s, q in res.items():
            if isinstance(q, Exception):
                out[s] = _quote_from_db(s)
            else:
                out[s] = q
                cache.set(f"q:{s}", q, 20 if clock.is_open(s) else 300)
                _save_quote(q)
    return out
