"""Risk signals: the CCC-minus-B high-yield spread and market-wide correlation, each checked against later S&P 500 declines.

* Credit: ICE BofA option-adjusted spreads for single-B (BAMLH0A2HYB) and CCC & lower (BAMLH0A3HYC), from FRED.
  FRED only serves the last three years of ICE data, so every observation seen is kept in data/credit_spreads.json and
  the history grows from here. Older history the user exports from a terminal can be dropped in as
  data/credit_spreads_import.csv (columns date, b, ccc in percent) and is merged in.
* Correlation: Cboe 1- and 3-month implied correlation (COR1M, COR3M, since 2006) and the realised average pairwise
  correlation of the nine original sector SPDRs over 63 trading days (since 1999).
* Declines: S&P 500 swings of 10% or more, found with a 10% reversal rule on daily closes.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from ..cache import cached
from ..config import APP_USER_AGENT, BACKEND_DIR
from ..providers.base import ProviderError
from ..providers.fred import fred
from .prices import get_daily
from .store import clean, utcnow

log = logging.getLogger(__name__)
STORE = Path(BACKEND_DIR) / "data" / "credit_spreads.json"
IMPORT = Path(BACKEND_DIR) / "data" / "credit_spreads_import.csv"
B_ID, CCC_ID = "BAMLH0A2HYB", "BAMLH0A3HYC"
SECTORS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{}_History.csv"
SWING = 0.10            # a decline counts once the S&P 500 closes 10% below its swing high; a 10% rally ends it
REARM = 20              # a new crossing needs 20 trading days back on the near side first
HORIZONS = (63, 126, 252)
CORR_WINDOW = 63
LEVELS = {"realized": (30, 40, 50, 60, 70, 80), "cor3m": (20, 30, 40, 50, 60, 70), "cor1m": (20, 30, 40, 50, 60, 70)}
_http = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), headers={"User-Agent": APP_USER_AGENT}, follow_redirects=True)


# ------------------------------------------------------------------ data
def _imported() -> pd.DataFrame:
    if not IMPORT.exists():
        return pd.DataFrame(columns=["b", "ccc"], dtype="float64")
    df = pd.read_csv(IMPORT)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"date", "b", "ccc"} <= set(df.columns):
        log.warning("%s needs the columns date, b, ccc - ignored", IMPORT.name)
        return pd.DataFrame(columns=["b", "ccc"], dtype="float64")
    df.index = pd.to_datetime(df.pop("date"))
    return df[["b", "ccc"]].apply(pd.to_numeric, errors="coerce").dropna()


def _stored() -> pd.DataFrame:
    if not STORE.exists():
        return pd.DataFrame(columns=["b", "ccc"], dtype="float64")
    rows = json.loads(STORE.read_text())["rows"]
    df = pd.DataFrame(rows, columns=["date", "b", "ccc"])
    df.index = pd.to_datetime(df.pop("date"))
    return df


def credit_history() -> tuple[pd.DataFrame, bool]:
    """Columns b and ccc in percent, plus whether FRED answered. Fresh FRED values win over stored or imported ones."""
    old = _stored().combine_first(_imported())
    try:
        start = dt.date(1996, 12, 31)
        fresh = pd.concat([fred.series(B_ID, start=start).rename("b"), fred.series(CCC_ID, start=start).rename("ccc")], axis=1).dropna()
    except ProviderError as e:
        if old.empty:
            raise
        log.warning("credit spreads: FRED unavailable, using the stored history (%s)", e)
        return old.sort_index(), False
    df = fresh.combine_first(old).sort_index().dropna()
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"series": {"b": B_ID, "ccc": CCC_ID}, "unit": "percent",
                               "rows": [[d.strftime("%Y-%m-%d"), round(float(r.b), 3), round(float(r.ccc), 3)] for d, r in df.iterrows()]}))
    tmp.replace(STORE)
    return df, True


def _cboe(name: str) -> pd.Series:
    r = _http.get(CBOE.format(name))
    if r.status_code != 200 or not r.text.startswith("DATE"):
        raise ProviderError(f"Cboe {name}: HTTP {r.status_code}")
    df = pd.read_csv(io.StringIO(r.text))
    s = pd.to_numeric(df["CLOSE"], errors="coerce")
    s.index = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
    return s[s > 0].dropna().rename(name)


def _close(symbol: str, years: int) -> pd.Series:
    d = get_daily(symbol, years=years).df
    s = d["adj_close"] if "adj_close" in d else d["close"]
    s = s.dropna()
    idx = pd.to_datetime(s.index)
    s.index = (idx.tz_convert(None) if idx.tz is not None else idx).normalize()
    return s[~s.index.duplicated(keep="last")]


def realized_correlation() -> pd.Series:
    """Average pairwise correlation of the nine sector SPDRs' daily log returns over the last 63 trading days, x100."""
    px = pd.DataFrame({s: _close(s, 30) for s in SECTORS}).dropna()
    rets = np.log(px).diff().dropna()
    roll = rets.rolling(CORR_WINDOW).corr().dropna()
    n = len(SECTORS)
    sums = roll.groupby(level=0).sum().sum(axis=1)          # every cell of each 9x9 matrix, diagonal included
    return ((sums - n) / (n * (n - 1)) * 100).rename("realized")


# ------------------------------------------------------------------ analysis
def declines(px: pd.Series, swing: float = SWING) -> list[dict]:
    """Peak-to-trough declines of `swing` or more. A peak is confirmed once the close falls `swing` below it, a trough once
    the close rises `swing` above it. `onset` is the first close `swing` below the peak; an unfinished decline is open."""
    v, out = px.to_numpy(), []
    up, hi, lo, onset = True, 0, 0, 0
    for i in range(1, len(v)):
        if up:
            if v[i] > v[hi]:
                hi = i
            elif v[i] <= v[hi] * (1 - swing):
                up, lo, onset = False, i, i
        else:
            if v[i] < v[lo]:
                lo = i
            elif v[i] >= v[lo] * (1 + swing):
                out.append((hi, onset, lo, False))
                up, hi = True, i
    if not up:
        out.append((hi, onset, lo, True))
    d = px.index
    return [{"peak": d[a], "onset": d[o], "trough": d[t], "open": op, "depth": float(v[t] / v[a] - 1)} for a, o, t, op in out]


def crossings(s: pd.Series, level: float, above: bool = True) -> list[pd.Timestamp]:
    """Days the series moves to the far side of `level` after at least REARM days on the near side."""
    out, run = [], 0
    for d, far in ((s > level) if above else (s < level)).items():
        if far:
            if run >= REARM:
                out.append(d)
            run = 0
        else:
            run += 1
    return out


def forward_drawdowns(px: pd.Series) -> dict[int, pd.Series]:
    """For each day: the lowest close over the next h trading days relative to that day's close (NaN when the window runs past today)."""
    v, out = px.to_numpy(), {}
    for h in HORIZONS:
        mins = pd.Series(v[::-1]).rolling(h, min_periods=h).min().to_numpy()[::-1]   # min of v[i..i+h-1]
        nxt = np.full(len(v), np.nan)
        nxt[:-1] = mins[1:]                                                         # min of v[i+1..i+h]
        out[h] = pd.Series(nxt / v - 1, index=px.index)
    return out


def _date(x) -> str | None:
    return None if x is None or pd.isna(x) else pd.Timestamp(x).strftime("%Y-%m-%d")


def follow_up(px: pd.Series, when: pd.Timestamp, falls: list[dict], fwd: dict[int, pd.Series]) -> dict:
    """What the S&P 500 did after a signal day: the worst close over the next 3, 6 and 12 months, and the decline it fell into."""
    i = int(px.index.searchsorted(when))
    if i >= len(px):
        return {}
    day, base = px.index[i], float(px.iloc[i])
    after = {}
    for h in HORIZONS:
        v = fwd[h].iloc[i]
        seen = px.iloc[i + 1:i + 1 + h]
        after[str(h)] = {"dd": None if seen.empty else round(float(seen.min() / base - 1), 4), "days": int(len(seen)), "complete": bool(not pd.isna(v))}
    hit = next((f for f in falls if f["trough"] >= day and f["onset"] <= day + pd.Timedelta(days=365)), None)
    decline = None
    if hit:
        timing = "before_peak" if day < hit["peak"] else "before_onset" if day < hit["onset"] else "after_onset"
        decline = {"peak": _date(hit["peak"]), "onset": _date(hit["onset"]), "trough": _date(hit["trough"]), "depth": round(hit["depth"], 4),
                   "open": hit["open"], "timing": timing, "lead_days": int((hit["onset"] - day).days)}
    return {"date": _date(day), "spx": round(base, 2), "after": after, "decline": decline}


def base_rates(fwd: dict[int, pd.Series], start: pd.Timestamp) -> dict:
    """Share of all days since `start` whose next h trading days held a close 10% or more below that day's close."""
    out = {}
    for h, s in fwd.items():
        s = s[s.index >= start].dropna()
        out[str(h)] = {"share": round(float((s <= -SWING).mean()), 4) if len(s) else None, "days": int(len(s))}
    return out


def threshold_table(series: pd.Series, levels: tuple[int, ...], px: pd.Series, falls: list[dict], fwd: dict[int, pd.Series]) -> list[dict]:
    rows = []
    for above in (True, False):
        for lv in levels:
            hits = [follow_up(px, d, falls, fwd) for d in crossings(series, lv, above)]
            hits = [h for h in hits if h]
            done = [h for h in hits if h["after"]["126"]["complete"]]
            with_fall = [h for h in done if h["after"]["126"]["dd"] is not None and h["after"]["126"]["dd"] <= -SWING]
            late = [h for h in hits if h["decline"] and h["decline"]["timing"] == "after_onset"]
            rows.append({"level": lv, "direction": "above" if above else "below", "signals": len(hits), "complete": len(done),
                         "fell_10_in_6m": len(with_fall), "share": round(len(with_fall) / len(done), 3) if done else None,
                         "already_falling": len(late), "dates": [h["date"] for h in hits]})
    return rows


def _at(s: pd.Series, when: pd.Timestamp, back: int = 0):
    i = int(s.index.searchsorted(when, side="right")) - 1 - back
    return None if i < 0 or s.empty else round(float(s.iloc[i]), 1)


def _weekly(s: pd.Series, digits: int = 2) -> list[dict]:
    w = s.resample("W-FRI").last().dropna()
    return [{"date": d.strftime("%Y-%m-%d"), "value": round(float(v), digits)} for d, v in w.items()]


def _daily(s: pd.Series, digits: int = 2) -> list[dict]:
    return [{"date": d.strftime("%Y-%m-%d"), "value": round(float(v), digits)} for d, v in s.dropna().items()]


def _pct_rank(s: pd.Series) -> float | None:
    s = s.dropna()
    return None if s.empty else round(float((s <= s.iloc[-1]).mean() * 100), 0)


# ------------------------------------------------------------------ payloads
def credit(threshold: float = 7.8) -> dict:
    def build():
        df, live = credit_history()
        gap = (df.ccc - df.b).rename("gap")
        px = _close("^GSPC", 40)
        falls, fwd = declines(px), forward_drawdowns(px)
        start = gap.index[0]
        events = []
        for d in crossings(gap, threshold, True):
            f = follow_up(px, d, falls, fwd)
            if f:
                events.append({**f, "signal_date": _date(d), "gap": round(float(gap.loc[d]), 2), "b": round(float(df.b.loc[d]), 2), "ccc": round(float(df.ccc.loc[d]), 2)})
        above = gap > threshold
        run = int(above[::-1].cumprod().sum()) if bool(above.iloc[-1]) else 0
        last_cross = events[-1]["signal_date"] if events else None
        window = px[px.index >= start]
        return {
            "threshold": threshold, "rearm_days": REARM, "swing": SWING, "fred_live": live,
            "range": [_date(start), _date(gap.index[-1])], "observations": int(len(gap)),
            "latest": {"date": _date(gap.index[-1]), "b": round(float(df.b.iloc[-1]), 2), "ccc": round(float(df.ccc.iloc[-1]), 2),
                       "gap": round(float(gap.iloc[-1]), 2), "above": bool(above.iloc[-1]), "days_above": run,
                       "percentile": _pct_rank(gap), "max": round(float(gap.max()), 2), "max_date": _date(gap.idxmax()),
                       "min": round(float(gap.min()), 2), "min_date": _date(gap.idxmin())},
            "first_day_above": bool(above.iloc[0]), "last_crossing": last_cross,
            "events": events,
            "base_rate": base_rates(fwd, start),
            "declines": [{"peak": _date(f["peak"]), "onset": _date(f["onset"]), "trough": _date(f["trough"]), "depth": round(f["depth"], 4), "open": f["open"]}
                         for f in falls if f["trough"] >= start],
            "series": {"gap": _daily(gap), "b": _daily(df.b), "ccc": _daily(df.ccc), "spx": _daily(window, 2)},
            "imported_rows": int(len(_imported())),
            "updated_at": utcnow().isoformat(),
        }
    return clean(cached(f"risk:credit:{threshold:.2f}", 3 * 3600, build))


def correlation() -> dict:
    def build():
        px = _close("^GSPC", 40)
        falls, fwd = declines(px), forward_drawdowns(px)
        measures: dict[str, pd.Series] = {}
        missing = []
        try:
            measures["realized"] = realized_correlation()
        except Exception as e:     # one source failing must not hide the others
            log.warning("realized correlation unavailable: %s", e)
            missing.append("realized")
        for name in ("COR3M", "COR1M"):
            try:
                measures[name.lower()] = _cboe(name)
            except Exception as e:
                log.warning("%s unavailable: %s", name, e)
                missing.append(name.lower())
        if not measures:
            return {"status": "Data unavailable"}
        start = min(s.index[0] for s in measures.values())
        rows = []
        for f in falls:
            if f["peak"] < start:
                continue
            row = {"peak": _date(f["peak"]), "onset": _date(f["onset"]), "trough": _date(f["trough"]), "depth": round(f["depth"], 4), "open": f["open"]}
            for k, s in measures.items():
                during = s[(s.index >= f["peak"]) & (s.index <= f["trough"])]
                pre, at_peak = _at(s, f["peak"], 21), _at(s, f["peak"])
                row[k] = {"before": pre, "peak": at_peak, "onset": _at(s, f["onset"]),
                          "max": None if during.empty else round(float(during.max()), 1),
                          "max_date": None if during.empty else _date(during.idxmax())}
            rows.append(row)
        latest = {k: {"value": round(float(s.iloc[-1]), 1), "date": _date(s.index[-1]), "percentile": _pct_rank(s),
                      "change_1m": round(float(s.iloc[-1] - s.iloc[-22]), 1) if len(s) > 22 else None, "since": _date(s.index[0])}
                  for k, s in measures.items()}
        return {
            "window": CORR_WINDOW, "swing": SWING, "rearm_days": REARM, "missing": missing, "latest": latest,
            "declines": rows,
            "thresholds": {k: threshold_table(s, LEVELS[k], px, falls, fwd) for k, s in measures.items()},
            "base_rate": base_rates(fwd, start),
            "series": {**{k: _weekly(s, 1) for k, s in measures.items()}, "spx": _weekly(px[px.index >= start], 2)},
            "updated_at": utcnow().isoformat(),
        }
    return clean(cached("risk:correlation", 6 * 3600, build))
