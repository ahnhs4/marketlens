"""FOMC rate probabilities computed the CME FedWatch way from 30-day Fed Funds futures.

Inputs (all real, fetched): Fed Funds futures closes per contract month (Yahoo, ZQ<month><yy>.CBT), the FOMC
meeting calendar (federalreserve.gov), the current target range and effective fed funds rate (FRED).
Method (CME FedWatch):
  * A contract settles at 100 − the month's average effective rate, so implied average R = 100 − price.
  * For a meeting month with N days whose meeting ends on day d (the new rate applies from day d+1):
    rate after = next month's implied rate when the next month has no meeting; otherwise
    (R × N − rate before × d) / (N − d). The rate before the first meeting is today's effective rate;
    each later meeting starts from the previous meeting's rate after.
  * Expected change / 25bp = x steps: the move is floor(x) or ceil(x) steps with the fractional part as the
    probability of the larger one. Meetings are chained (independent) into a probability for every target range.
Numbers can differ slightly from CME's own page (settlement vs last price, rounding); the method is the same.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
import re

import httpx
import numpy as np
import pandas as pd

from ..cache import cached
from ..config import APP_USER_AGENT
from ..providers.fred import fred
from ..providers.yahoo import yahoo
from .store import clean, utcnow

log = logging.getLogger(__name__)
CODES = "FGHJKMNQUVXZ"
MONTHS = {m[:3]: i for i, m in enumerate(calendar.month_name) if m}   # "Apr/May", "October" -> by the first 3 letters
STEP = 0.25


def meetings() -> list[dt.date]:
    """FOMC meeting end dates (the decision day) from the Fed's calendar page, this year and the next."""
    def load():
        r = httpx.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", headers={"User-Agent": APP_USER_AGENT},
                      timeout=20, follow_redirects=True)
        r.raise_for_status()
        html = r.text
        out = []
        for yr in sorted({int(y) for y in re.findall(r"(\d{4}) FOMC Meetings", html)}):
            i = html.find(f"{yr} FOMC Meetings")
            j = html.find("FOMC Meetings", i + 20)
            seg = html[i:j if j > 0 else i + 40000]
            for month, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', seg, re.S):
                mnames = [m.strip() for m in re.split(r"[/-]", month)]
                dnums = [int(x) for x in re.findall(r"\d+", days)]
                if not dnums or mnames[-1][:3] not in MONTHS:
                    continue
                out.append(dt.date(yr, MONTHS[mnames[-1][:3]], dnums[-1]).isoformat())
        return sorted(set(out))
    return [dt.date.fromisoformat(d) for d in cached("fedwatch:meetings", 86400, load)]


def _ticker(y: int, m: int) -> str:
    return f"ZQ{CODES[m - 1]}{str(y)[2:]}.CBT"


def _months(start: dt.date, n: int) -> list[tuple[int, int]]:
    y, m = start.year, start.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _futures(months: list[tuple[int, int]]) -> pd.DataFrame:
    """Daily closes per contract month (columns = (y, m))."""
    def load():
        frames = yahoo.history_many([_ticker(y, m) for y, m in months], period="3mo")
        cols = {}
        for y, m in months:
            df = frames.get(_ticker(y, m))
            if df is not None and len(df):
                cols[f"{y}-{m:02d}"] = df["close"]
        return pd.DataFrame(cols)
    return cached("fedwatch:futures:" + ",".join(f"{y}{m}" for y, m in months), 900, load)


def _rates() -> tuple[pd.Series, pd.Series, pd.Series]:
    def load():
        start = dt.date.today() - dt.timedelta(days=120)
        return {k: fred.series(k, start) for k in ("DFEDTARL", "DFEDTARU", "EFFR")}
    d = cached("fedwatch:fred", 6 * 3600, load)
    return d["DFEDTARL"].dropna(), d["DFEDTARU"].dropna(), d["EFFR"].dropna()


def compute(prices: dict[str, float], meets: list[dt.date], effr: float, lower: float, as_of: dt.date) -> list[dict]:
    """Per upcoming meeting: implied rate after it, expected change and the probability of every target range."""
    upcoming = [d for d in meets if d >= as_of]
    meet_months = {(d.year, d.month) for d in upcoming}
    implied = {k: 100 - v for k, v in prices.items() if v is not None and np.isfinite(v)}
    out = []
    start = effr
    dist = {0: 1.0}            # steps away from today's target range -> probability
    # Meetings whose month contract has expired (Yahoo drops them) are folded into the next priced meeting:
    # the rate before that meeting is backed out of its month's contract and the next month's.
    skipped = False
    while upcoming and f"{upcoming[0].year}-{upcoming[0].month:02d}" not in implied:
        upcoming, skipped = upcoming[1:], True
    if skipped and upcoming:
        d = upcoming[0]
        n_days = calendar.monthrange(d.year, d.month)[1]
        ny, nm = (d.year + (d.month == 12), d.month % 12 + 1)
        nkey, key = f"{ny}-{nm:02d}", f"{d.year}-{d.month:02d}"
        if (ny, nm) in meet_months or nkey not in implied:
            return []
        start = (implied[key] * n_days - implied[nkey] * (n_days - d.day)) / d.day
        x0 = (start - effr) / STEP
        lo0 = int(np.floor(x0))
        dist = {k: v for k, v in {lo0: 1 - (x0 - lo0), lo0 + 1: x0 - lo0}.items() if v > 1e-9}
    for d in upcoming:
        key = f"{d.year}-{d.month:02d}"
        ny, nm = (d.year + (d.month == 12), d.month % 12 + 1)
        nkey = f"{ny}-{nm:02d}"
        n_days = calendar.monthrange(d.year, d.month)[1]
        if (ny, nm) not in meet_months and nkey in implied:
            after = implied[nkey]
        elif key in implied and n_days - d.day > 0:
            after = (implied[key] * n_days - start * d.day) / (n_days - d.day)
        else:
            break   # no contract to price this meeting
        x = (after - start) / STEP
        lo_step = int(np.floor(x))
        p_hi = float(x - lo_step)
        move = {lo_step: 1 - p_hi, lo_step + 1: p_hi}
        new: dict[int, float] = {}
        for s, p in dist.items():
            for mv, q in move.items():
                if q > 1e-9:
                    new[s + mv] = new.get(s + mv, 0) + p * q
        dist = new
        ranges = [{"lower": round(lower + s * STEP, 2), "upper": round(lower + (s + 1) * STEP, 2), "steps": s, "prob": round(p * 100, 1)}
                  for s, p in sorted(dist.items()) if p >= 0.0005]
        this = {"hike": sum(q for mv, q in move.items() if mv > 0), "hold": move.get(0, 0.0), "cut": sum(q for mv, q in move.items() if mv < 0)}
        out.append({"date": d.isoformat(), "rate_before": round(start, 4), "rate_after": round(after, 4),
                    "change_bp": round((after - start) * 100, 1), "cum_change_bp": round((after - effr) * 100, 1),
                    "meeting": {k: round(v * 100, 1) for k, v in this.items()},
                    "vs_today": {"hike": round(sum(p for s, p in dist.items() if s > 0) * 100, 1),
                                 "hold": round(dist.get(0, 0) * 100, 1),
                                 "cut": round(sum(p for s, p in dist.items() if s < 0) * 100, 1)},
                    "ranges": ranges,
                    "most_likely": max(ranges, key=lambda r: r["prob"]) if ranges else None})
        start = after
    return out


def fedwatch() -> dict:
    return cached("fedwatch:result", 900, _build)


def _build() -> dict:
    today = dt.date.today()
    meets = meetings()
    upcoming = [d for d in meets if d >= today]
    if not upcoming:
        return {"available": False, "message": "다가오는 FOMC 일정을 불러올 수 없습니다 (Data unavailable)"}
    first = (today - dt.timedelta(days=45)).replace(day=1)   # earlier months too, so "1 month ago" can be priced
    months = _months(first, (upcoming[-1].year - first.year) * 12 + upcoming[-1].month - first.month + 2)
    fut = _futures(months)
    lower_s, upper_s, effr_s = _rates()
    if fut.empty or lower_s.empty or effr_s.empty:
        return {"available": False, "message": "연방기금 선물 또는 기준금리 데이터를 불러올 수 없습니다 (Data unavailable)"}
    lower, upper, effr = float(lower_s.iloc[-1]), float(upper_s.iloc[-1]), float(effr_s.iloc[-1])

    def at(back_days: int):
        rows = fut[fut.index <= fut.index[-1] - pd.Timedelta(days=back_days)]
        if rows.empty:
            return None
        ts = rows.index[-1]
        d0 = ts.date()
        e = float(effr_s[effr_s.index <= ts].iloc[-1]) if (effr_s.index <= ts).any() else effr
        lo = float(lower_s[lower_s.index <= ts].iloc[-1]) if (lower_s.index <= ts).any() else lower
        px = {c: float(v) for c, v in rows.iloc[-1].items() if pd.notna(v)}
        return d0, compute(px, meets, e, lo, d0), lo
    now = at(0)
    week, month = at(7), at(30)
    if not now or not now[1]:
        return {"available": False, "message": "선물 가격으로 계산할 수 있는 회의가 없습니다 (Data unavailable)"}
    as_of, rows, _ = now

    def same_meeting(prev, date):
        if not prev:
            return None
        r = next((x for x in prev[1] if x["date"] == date), None)
        return {"as_of": prev[0].isoformat(), "meeting": r["meeting"], "vs_today": r["vs_today"]} if r else None
    for r in rows:
        r["week_ago"] = same_meeting(week, r["date"])
        r["month_ago"] = same_meeting(month, r["date"])
        r["days_to"] = (dt.date.fromisoformat(r["date"]) - today).days
    return clean({"available": True, "as_of": as_of.isoformat(), "target": {"lower": lower, "upper": upper}, "effr": effr,
                  "meetings": rows, "next": rows[0], "contracts": {k: float(v) for k, v in fut.iloc[-1].items() if pd.notna(v)},
                  "source": "연방기금 선물 (CBOT ZQ, Yahoo Finance) · FOMC 일정 (federalreserve.gov) · 기준금리 (FRED)",
                  "method": __doc__, "updated_at": utcnow().isoformat()})
