"""Price-series validation, run before any analysis.

Checks: duplicate timestamps, non-positive prices, negative volume, OHLC
inconsistency, impossible jumps, probable un-adjusted splits, missing sessions
(against the exchange calendar, holidays included), stale prints and timezone
normalisation. Hard errors are removed; soft issues are reported to the UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 20, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10)


@dataclass
class QualityReport:
    rows_in: int
    rows_out: int
    issues: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i["severity"] == "error" for i in self.issues)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "rows_in": self.rows_in, "rows_out": self.rows_out, "issues": self.issues}


@lru_cache(maxsize=8)
def _calendar(code: str):
    import exchange_calendars as xcals

    return xcals.get_calendar(code)


def calendar_code(symbol: str) -> str | None:
    if symbol.endswith((".KS", ".KQ")) or symbol in ("^KS11", "^KQ11"):
        return "XKRX"
    if symbol.startswith("^") or "=" in symbol or symbol.endswith("-USD") or "." in symbol:
        return None  # indices/FX/futures/crypto/foreign: skip session check
    return "XNYS"


def expected_sessions(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex | None:
    code = calendar_code(symbol)
    if not code:
        return None
    try:
        cal = _calendar(code)
        start = max(start, cal.first_session)
        end = min(end, cal.last_session)
        return pd.DatetimeIndex(cal.sessions_in_range(start, end)).tz_localize(None)
    except Exception as e:  # calendar data missing for range
        log.debug("calendar check skipped for %s: %s", symbol, e)
        return None


def validate(df: pd.DataFrame, symbol: str = "", *, daily: bool = True) -> tuple[pd.DataFrame, QualityReport]:
    rep = QualityReport(rows_in=len(df), rows_out=len(df))
    if df.empty:
        rep.issues.append({"check": "empty", "severity": "error", "detail": "no rows"})
        return df, rep
    out = df.copy()

    # timezone / index normalisation
    if not daily and getattr(out.index, "tz", None) is None:
        out.index = out.index.tz_localize("UTC")
        rep.issues.append({"check": "timezone", "severity": "warn", "detail": "naive intraday timestamps assumed UTC"})

    dup = out.index.duplicated(keep="last")
    if dup.any():
        rep.issues.append({"check": "duplicate_timestamp", "severity": "warn", "count": int(dup.sum()),
                           "sample": [str(x) for x in out.index[dup][:5]]})
        out = out[~dup]
    out = out.sort_index()

    bad_px = (out[["open", "high", "low", "close"]] <= 0).any(axis=1)
    if bad_px.any():
        rep.issues.append({"check": "non_positive_price", "severity": "error", "count": int(bad_px.sum()),
                           "sample": [str(x) for x in out.index[bad_px][:5]], "action": "rows removed"})
        out = out[~bad_px]

    neg_vol = out["volume"] < 0
    if neg_vol.any():
        rep.issues.append({"check": "negative_volume", "severity": "error", "count": int(neg_vol.sum()),
                           "action": "set to NaN"})
        out.loc[neg_vol, "volume"] = np.nan

    hi_bad = out["high"] < out[["open", "close"]].max(axis=1) * (1 - 1e-6)
    lo_bad = out["low"] > out[["open", "close"]].min(axis=1) * (1 + 1e-6)
    if (hi_bad | lo_bad).any():
        rep.issues.append({"check": "ohlc_inconsistent", "severity": "warn", "count": int((hi_bad | lo_bad).sum()),
                           "sample": [str(x) for x in out.index[hi_bad | lo_bad][:5]], "action": "high/low widened"})
        out["high"] = out[["high", "open", "close"]].max(axis=1)
        out["low"] = out[["low", "open", "close"]].min(axis=1)

    ret = out["close"].pct_change()
    if daily and len(out) > 30:
        ratio = out["close"] / out["close"].shift(1)
        split_like = pd.Series(False, index=out.index)
        for r in SPLIT_RATIOS:
            split_like |= ((ratio - 1 / r).abs() < 0.02 / r)
        recorded = out.get("splits", pd.Series(0.0, index=out.index)).fillna(0) != 0
        suspicious = split_like & ~recorded & (ret.abs() > 0.4)
        if suspicious.any():
            rep.issues.append({"check": "possible_unadjusted_split", "severity": "warn", "count": int(suspicious.sum()),
                               "sample": [str(x.date()) for x in out.index[suspicious][:5]],
                               "detail": "price ratio matches a split ratio with no split recorded"})
        robust = (ret - ret.median()).abs() / (1.4826 * (ret - ret.median()).abs().median() + 1e-12)
        jumps = (ret.abs() > 0.5) | (robust > 25)
        jumps &= ~suspicious & ~recorded
        if jumps.any():
            rep.issues.append({"check": "extreme_move", "severity": "warn", "count": int(jumps.sum()),
                               "sample": [f"{x.date()} ({ret[x]:+.1%})" for x in out.index[jumps][:5]],
                               "detail": "verify corporate action or bad print"})

        exp = expected_sessions(symbol, out.index[0], out.index[-1])
        if exp is not None and len(exp):
            missing = exp.difference(out.index)
            # The current session may legitimately be absent before the open.
            missing = missing[missing < out.index[-1]]
            if len(missing):
                rep.issues.append({"check": "missing_sessions", "severity": "warn" if len(missing) < 10 else "error",
                                   "count": int(len(missing)), "sample": [str(x.date()) for x in missing[:5]]})
            extra = out.index.difference(exp)
            if len(extra) and len(extra) < len(out) * 0.05:
                rep.issues.append({"check": "non_session_bars", "severity": "info", "count": int(len(extra)),
                                   "sample": [str(x.date()) for x in extra[:5]]})

        stale = (out["close"].diff() == 0) & (out["volume"].fillna(0) == 0)
        run = stale.groupby((~stale).cumsum()).cumsum()
        if (run >= 5).any():
            rep.issues.append({"check": "stale_prices", "severity": "warn", "count": int((run >= 5).sum()),
                               "detail": "5+ consecutive unchanged closes with zero volume (halt or bad data)"})

    rep.rows_out = len(out)
    return out, rep
