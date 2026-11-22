"""Yahoo Finance provider (via the `yfinance` library, no API key).

Coverage: US + KR equities (`.KS` KOSPI / `.KQ` KOSDAQ), indices, FX, futures,
crypto, fundamentals, earnings calendar, symbol search and headline news.
Delay per exchange is read from Yahoo's own `exchangeDataDelayedBy` field so the
UI can label quotes as real-time or delayed.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd
import yfinance as yf

from .base import NewsItem, NotFoundError, ProviderError, QuoteData, SymbolMatch, health

log = logging.getLogger(__name__)
NAME = "yahoo"
# yfinance.download keeps module-level state; concurrent calls from different threads can hang or mix
# results, so batch downloads are serialised process-wide.
_DOWNLOAD_LOCK = threading.Lock()

_US_SESSIONS = (("pre", dt.time(4, 0), dt.time(9, 30)), ("regular", dt.time(9, 30), dt.time(16, 0)),
                ("post", dt.time(16, 0), dt.time(20, 0)))


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _px(x: Any) -> float | None:
    """Price field: Yahoo reports 0 for day open/high/low before a session trades - treat as missing."""
    v = _f(x)
    return v if v is not None and v > 0 else None


def _normalize_frame(raw: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Yahoo OHLCV -> lower-case columns; daily index -> naive date, intraday -> UTC."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "adj_close", "volume", "dividends", "splits"])
    df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                             "Adj Close": "adj_close", "Volume": "volume", "Dividends": "dividends",
                             "Stock Splits": "splits", "Capital Gains": "capital_gains"})
    if "adj_close" not in df:
        df["adj_close"] = df["close"]
    for c in ("dividends", "splits"):
        if c not in df:
            df[c] = 0.0
    df = df[["open", "high", "low", "close", "adj_close", "volume", "dividends", "splits"]].astype(float)
    idx = df.index
    if interval in ("1d", "5d", "1wk", "1mo", "3mo"):
        # Daily bars are stamped at local-exchange midnight; keep the calendar date.
        df.index = pd.DatetimeIndex([pd.Timestamp(t.date()) for t in idx], name="date")
    else:
        df.index = (idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")).rename("ts")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["close"])


def us_session(ts_utc: pd.Timestamp) -> str:
    t = ts_utc.tz_convert("America/New_York").time()
    for name, a, b in _US_SESSIONS:
        if a <= t < b:
            return name
    return "closed"


class YahooProvider:
    name = NAME

    # ------------------------------------------------------------------ prices
    def history(self, symbol: str, *, period: str | None = None, start: dt.date | None = None,
                interval: str = "1d", prepost: bool = False) -> pd.DataFrame:
        try:
            kw: dict[str, Any] = {"interval": interval, "auto_adjust": False, "actions": True, "prepost": prepost,
                                  "raise_errors": False}
            if start is not None:
                kw["start"] = start.isoformat()
            else:
                kw["period"] = period or "1y"
            raw = yf.Ticker(symbol).history(**kw)
        except Exception as e:  # network / parsing
            health.fail(NAME, e)
            raise ProviderError(f"yahoo history failed for {symbol}: {e}") from e
        df = _normalize_frame(raw, interval)
        if df.empty:
            raise NotFoundError(f"No price data for {symbol}")
        if interval not in ("1d", "5d", "1wk", "1mo", "3mo"):
            df["session"] = [us_session(t) for t in df.index] if not symbol.endswith((".KS", ".KQ")) else "regular"
        health.ok(NAME)
        return df

    def history_many(self, symbols: list[str], *, period: str = "2y", interval: str = "1d",
                     chunk: int = 40) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), chunk):
            part = symbols[i:i + chunk]
            try:
                with _DOWNLOAD_LOCK:
                    raw = yf.download(part, period=period, interval=interval, group_by="ticker", auto_adjust=False,
                                      actions=True, progress=False, threads=True, timeout=20)
            except Exception as e:
                health.fail(NAME, e)
                log.warning("batch download failed (%s...): %s", part[:3], e)
                continue
            if raw is None or raw.empty:
                continue
            for s in part:
                try:
                    sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                except KeyError:
                    continue
                df = _normalize_frame(sub.dropna(how="all"), interval)
                if not df.empty:
                    out[s] = df
        if out:
            health.ok(NAME)
        return out

    def history_parallel(self, symbols: list[str], *, period: str = "1y", interval: str = "1d",
                         max_workers: int = 8) -> dict[str, pd.DataFrame]:
        """Per-symbol history in a thread pool. Does not take the batch-download lock, so small
        dashboard requests are never queued behind a universe scan."""
        def one(s: str):
            try:
                return s, self.history(s, period=period, interval=interval)
            except Exception:
                return s, None
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            return {s: df for s, df in ex.map(one, symbols) if df is not None}

    # ------------------------------------------------------------------ quotes
    def quote(self, symbol: str) -> QuoteData:
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception as e:
            health.fail(NAME, e)
            raise ProviderError(f"yahoo quote failed for {symbol}: {e}") from e
        price = _f(info.get("regularMarketPrice") or info.get("currentPrice"))
        if price is None:
            raise NotFoundError(f"No quote for {symbol}")
        prev = _f(info.get("regularMarketPreviousClose") or info.get("previousClose"))
        chg = _f(info.get("regularMarketChange"))
        if chg is None and prev:
            chg = price - prev
        chg_pct = _f(info.get("regularMarketChangePercent"))
        if chg_pct is None and prev:
            chg_pct = (price / prev - 1) * 100
        ts = info.get("regularMarketTime")
        qt = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc) if isinstance(ts, (int, float)) else None
        delay = info.get("exchangeDataDelayedBy")
        delay = int(delay) if isinstance(delay, (int, float)) else None
        src = info.get("quoteSourceName") or ""
        health.ok(NAME)
        return QuoteData(
            symbol=symbol, price=price, prev_close=prev, change=chg, change_pct=chg_pct,
            open=_px(info.get("regularMarketOpen") or info.get("open")),
            high=_px(info.get("regularMarketDayHigh") or info.get("dayHigh")),
            low=_px(info.get("regularMarketDayLow") or info.get("dayLow")),
            volume=_f(info.get("regularMarketVolume") or info.get("volume")),
            currency=info.get("currency"), name=info.get("longName") or info.get("shortName"),
            exchange=info.get("fullExchangeName") or info.get("exchange"),
            market_state=info.get("marketState"), quote_time=qt, delay_minutes=delay,
            realtime=(delay == 0 and "delayed" not in src.lower()), source=f"Yahoo Finance ({src or 'quote'})",
            pre_market_price=_f(info.get("preMarketPrice")), post_market_price=_f(info.get("postMarketPrice")),
            fifty_two_week_high=_f(info.get("fiftyTwoWeekHigh")), fifty_two_week_low=_f(info.get("fiftyTwoWeekLow")),
            extra={"quote_type": info.get("quoteType"), "short_name": info.get("shortName"),
                   "tz": info.get("exchangeTimezoneName")},
        )

    def quotes(self, symbols: list[str], max_workers: int = 8) -> dict[str, QuoteData | Exception]:
        def one(s: str):
            try:
                return s, self.quote(s)
            except Exception as e:  # keep per-symbol failures isolated
                return s, e

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            return dict(ex.map(one, symbols))

    # ------------------------------------------------------------ fundamentals
    def profile(self, symbol: str) -> dict[str, Any]:
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception as e:
            health.fail(NAME, e)
            raise ProviderError(str(e)) from e
        if not info or (info.get("regularMarketPrice") is None and info.get("longName") is None
                        and info.get("shortName") is None):
            raise NotFoundError(f"Unknown symbol {symbol}")
        health.ok(NAME)
        return info

    def earnings(self, symbol: str, limit: int = 24) -> list[dict[str, Any]]:
        try:
            ed = yf.Ticker(symbol).get_earnings_dates(limit=limit)
        except Exception as e:
            log.info("earnings dates unavailable for %s: %s", symbol, e)
            return []
        if ed is None or ed.empty:
            return []
        rows = []
        for ts, r in ed.iterrows():
            rows.append({
                "date": pd.Timestamp(ts).tz_convert("UTC").to_pydatetime() if pd.Timestamp(ts).tz is not None
                else pd.Timestamp(ts).tz_localize("UTC").to_pydatetime(),
                "eps_estimate": _f(r.get("EPS Estimate")), "eps_actual": _f(r.get("Reported EPS")),
                "surprise_pct": _f(r.get("Surprise(%)")),
            })
        return rows

    def calendar(self, symbol: str) -> dict[str, Any]:
        try:
            cal = yf.Ticker(symbol).calendar or {}
        except Exception:
            return {}
        return cal if isinstance(cal, dict) else {}

    def quarterly_financials(self, symbol: str) -> dict[str, pd.DataFrame]:
        t = yf.Ticker(symbol)
        out = {}
        for key, attr in (("income", "quarterly_income_stmt"), ("balance", "quarterly_balance_sheet"),
                          ("cashflow", "quarterly_cashflow"), ("income_annual", "income_stmt"),
                          ("cashflow_annual", "cashflow")):
            try:
                df = getattr(t, attr)
                out[key] = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
            except Exception:
                out[key] = pd.DataFrame()
        return out

    # ------------------------------------------------------------------ search
    def search(self, query: str, limit: int = 10) -> list[SymbolMatch]:
        try:
            s = yf.Search(query, max_results=limit, news_count=0, raise_errors=False)
            quotes = s.quotes or []
        except Exception as e:
            health.fail(NAME, e)
            return []
        out = []
        for q in quotes:
            sym = q.get("symbol")
            if not sym:
                continue
            out.append(SymbolMatch(symbol=sym, name=q.get("longname") or q.get("shortname") or sym,
                                   exchange=q.get("exchDisp") or q.get("exchange"), type=q.get("quoteType"),
                                   market="KR" if sym.endswith((".KS", ".KQ")) else "US"))
        return out

    def news(self, query: str, limit: int = 20) -> list[NewsItem]:
        try:
            s = yf.Search(query, max_results=1, news_count=limit, raise_errors=False)
            items = s.news or []
        except Exception as e:
            health.fail(NAME, e)
            return []
        out = []
        for n in items:
            ts = n.get("providerPublishTime")
            out.append(NewsItem(
                title=n.get("title", "").strip(), url=n.get("link"), source=n.get("publisher"),
                published_at=dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc) if isinstance(ts, (int, float)) else None,
                provider="yahoo", tickers=list(n.get("relatedTickers") or []), language="en"))
        return [o for o in out if o.title]

    def screen_us_nasdaq(self, size: int = 100) -> list[dict[str, Any]]:
        """Largest Nasdaq-listed equities by market cap (used as a Nasdaq-100 approximation)."""
        from yfinance import EquityQuery

        q = EquityQuery("and", [EquityQuery("eq", ["region", "us"]),
                                EquityQuery("is-in", ["exchange", "NMS", "NGM", "NCM"])])
        r = yf.screen(q, sortField="intradaymarketcap", sortAsc=False, size=size)
        return [{"symbol": x["symbol"], "name": x.get("longName") or x.get("shortName"),
                 "market_cap": x.get("marketCap")} for x in (r or {}).get("quotes", []) if x.get("quoteType") == "EQUITY"]

    def screen_kr(self, exchange: str, size: int = 100) -> list[dict[str, Any]]:
        """Top Korean listings by market cap (exchange KSC=KOSPI, KOE=KOSDAQ)."""
        from yfinance import EquityQuery

        q = EquityQuery("and", [EquityQuery("eq", ["region", "kr"]), EquityQuery("is-in", ["exchange", exchange])])
        r = yf.screen(q, sortField="intradaymarketcap", sortAsc=False, size=size)
        return [{"symbol": x["symbol"], "name": x.get("longName") or x.get("shortName"),
                 "market_cap": x.get("marketCap")} for x in (r or {}).get("quotes", [])]


yahoo = YahooProvider()
