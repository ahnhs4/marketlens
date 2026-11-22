"""Exchange sessions (holiday-aware via exchange_calendars). Internally everything is UTC."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


@lru_cache(maxsize=4)
def _cal(code: str):
    import exchange_calendars as xcals

    return xcals.get_calendar(code)


def market_of(symbol: str) -> str:
    return "KR" if symbol.endswith((".KS", ".KQ")) or symbol in ("^KS11", "^KQ11", "^KS200") else "US"


def status(market: str, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    ts = pd.Timestamp(now).tz_convert("UTC")
    code = "XKRX" if market == "KR" else "XNYS"
    tz = KST if market == "KR" else ET
    try:
        cal = _cal(code)
        today = ts.tz_convert(tz).normalize().tz_localize(None)
        is_session = cal.is_session(today)
        if is_session:
            o, c = cal.session_open(today), cal.session_close(today)
        nxt = cal.date_to_session(today, direction="next")
        if is_session and ts >= cal.session_close(today):
            nxt = cal.next_session(today)
        next_open = cal.session_open(nxt)
    except Exception:  # calendar unavailable -> naive weekday schedule
        is_session = ts.tz_convert(tz).weekday() < 5
        local = ts.tz_convert(tz)
        o = (local.normalize() + pd.Timedelta(hours=9, minutes=30 if market == "US" else 0)).tz_convert("UTC")
        c = (local.normalize() + pd.Timedelta(hours=16 if market == "US" else 15, minutes=0 if market == "US" else 30)).tz_convert("UTC")
        next_open = o if ts < o else o + pd.Timedelta(days=1)
    state = "closed"
    if is_session:
        if o <= ts < c:
            state = "open"
        elif market == "US" and o - pd.Timedelta(hours=5, minutes=30) <= ts < o:
            state = "pre"
        elif market == "US" and c <= ts < c + pd.Timedelta(hours=4):
            state = "post"
    local = ts.tz_convert(tz)
    return {"market": market, "state": state, "local_time": local.isoformat(), "timezone": str(tz),
            "next_open": next_open.isoformat() if state != "open" else None,
            "session_close": c.isoformat() if is_session else None}


def is_open(symbol: str) -> bool:
    return status(market_of(symbol))["state"] in ("open", "pre", "post")
