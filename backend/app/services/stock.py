"""Stock Analyzer: assembles quote, chart, technicals, patterns, analogs, fundamentals and scores.

Each piece is a separate function (and API endpoint) so the UI can load them
progressively: quote -> chart -> technicals -> news/analogs/fundamentals.
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..analysis import fundamentals as fa
from ..analysis import indicators, levels, macro_scoring, patterns, scoring, similarity, technical, trendlines
from ..analysis.timing_evidence import market_of
from ..cache import cached
from ..db import SessionLocal
from ..models import Earnings, Fundamental, Signal
from ..providers.base import NotFoundError
from ..providers.yahoo import yahoo
from . import dart_fin, macro, prices
from .store import clean, upsert, utcnow
from .symbols import index

log = logging.getLogger(__name__)

RANGES = {"1D": ("5m", "1d"), "1W": ("30m", "5d"), "1M": ("1d", 21), "3M": ("1d", 63), "6M": ("1d", 126),
          "1Y": ("1d", 252), "3Y": ("1d", 756), "5Y": ("1wk", 1260), "MAX": ("1wk", None)}
INTRADAY = {"1m": "5d", "5m": "30d", "15m": "30d", "1h": "180d"}
# Daily ranges also carry older bars (≥ ~5 years) so zooming out keeps showing candles; the chart opens on
# the selected range via `visible_from`.
HISTORY_BARS = 1260


class InvalidTicker(ValueError):
    pass


def resolve(ticker: str) -> str:
    sym = index.resolve(ticker)
    if not sym:
        raise InvalidTicker(f"'{ticker}'은(는) 찾을 수 없는 종목입니다")
    return sym


# ------------------------------------------------------------------ profile
def profile(symbol: str) -> dict:
    def load():
        try:
            info = yahoo.profile(symbol)
        except NotFoundError:
            return None
        except Exception as e:
            log.info("profile failed %s: %s", symbol, e)
            return {"_error": str(e)}
        f = fa.extract(info)
        try:
            with SessionLocal() as db:
                upsert(db, Fundamental, [{"symbol": symbol, "as_of": dt.date.today(), "data": clean(f), "source": "yahoo",
                                          "fetched_at": utcnow()}], ["symbol", "as_of"])
                db.commit()
        except Exception as e:
            log.debug("fundamental persist failed: %s", e)
        row = index.get(symbol) or {}
        return {"symbol": symbol, "name": info.get("longName") or info.get("shortName") or row.get("name") or symbol,
                "name_ko": row.get("name_ko"), "exchange": info.get("fullExchangeName") or info.get("exchange"),
                "currency": info.get("currency"), "sector": info.get("sector"), "industry": info.get("industry"),
                "country": info.get("country"), "quote_type": info.get("quoteType"),
                "website": info.get("website"), "employees": info.get("fullTimeEmployees"),
                "summary": (info.get("longBusinessSummary") or "")[:1200] or None,
                "earnings_timestamp": info.get("earningsTimestamp"),
                "fundamentals": f, "fetched_at": utcnow().isoformat()}
    res = cached(f"profile:{symbol}", 6 * 3600, load, cache_none=True)
    if res is None:
        raise NotFoundError(symbol)
    return res


def overview(symbol: str) -> dict:
    """Header block: quote + identity. Fast path for the first paint."""
    q = prices.get_quote(symbol)
    p = profile(symbol)
    if p.get("_error"):
        p = {"symbol": symbol, "name": q.name or symbol, "fundamentals": {}, "profile_error": p["_error"]}
    return clean({"symbol": symbol, "quote": q.to_dict(), "profile": p,
                  "market_status": macro_safe_status(symbol), "updated_at": utcnow().isoformat()})


def macro_safe_status(symbol: str) -> dict:
    from . import clock
    return clock.status(clock.market_of(symbol))


# ------------------------------------------------------------------ chart
def chart(symbol: str, range_: str = "1Y", interval: str | None = None, rsi_period: int = 14,
          rsi_short: int = 7, rsi_long: int = 28, stoch_sets: dict | None = None) -> dict:
    range_ = range_.upper()
    stoch_sets = stoch_sets or indicators.STOCH_SETS
    rk = {"rsi_period": rsi_period, "rsi_short": rsi_short, "rsi_long": rsi_long, "stoch_sets": stoch_sets}
    n_vis = None
    if interval in INTRADAY:
        df = prices.get_intraday(symbol, interval, INTRADAY[interval])
        ind = indicators.compute_all(df, **rk, intraday=True)
        view = ind
        meta = {"interval": interval, "intraday": True, "source": "Yahoo Finance",
                "delayed_note": "Intraday bars may be delayed per exchange (see quote delay)."}
    elif range_ in ("1D", "1W"):
        iv, per = RANGES[range_]
        df = prices.get_intraday(symbol, iv, per if range_ == "1D" else "5d")
        if range_ == "1D" and len(df):
            last_day = df.index[-1].tz_convert("America/New_York" if not symbol.endswith((".KS", ".KQ")) else "Asia/Seoul").date()
            ind = indicators.compute_all(df, **rk, intraday=True)
            view = ind[[t.tz_convert("America/New_York" if not symbol.endswith((".KS", ".KQ")) else "Asia/Seoul").date() == last_day
                        for t in ind.index]]
        else:
            ind = indicators.compute_all(df, **rk, intraday=True)
            view = ind
        meta = {"interval": iv, "intraday": True, "source": "Yahoo Finance"}
    else:
        ps = prices.get_daily(symbol, years=10 if range_ not in ("MAX",) else 40)
        ind = indicators.compute_all(ps.df, **rk)
        iv, n = RANGES.get(range_, ("1d", 252))
        if iv == "1wk":
            wk = ps.df.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                               "adj_close": "last", "volume": "sum", "dividends": "sum",
                                               "splits": "sum"}).dropna(subset=["close"])
            ind = indicators.compute_all(wk, **rk)
            view, n_vis = ind, None if n is None else n // 5
        else:
            view, n_vis = ind.iloc[-max(HISTORY_BARS, 2 * n):], n
        start = view.index[-n_vis] if n_vis and n_vis < len(view) else None
        meta = {"interval": iv, "intraday": False, "source": ps.source, "quality": ps.quality,
                "fetched_at": ps.fetched_at.isoformat(), "visible_from": start.strftime("%Y-%m-%d") if start is not None else None}
    # Trendlines over the range on screen (at least ~6 months of daily bars for context).
    n_tl = len(view) if meta.get("intraday") else min(len(view), max((n_vis or len(view)) if not meta.get("intraday") else len(view), 120), 756)
    tl_bars = view.iloc[-n_tl:]
    try:
        tls = trendlines.find(tl_bars, k=max(3, min(10, round(len(tl_bars) / 50))))
    except Exception as e:
        log.info("trendlines %s: %s", symbol, e)
        tls = {"lines": [], "pattern": None}
    try:
        dd = drawdowns(symbol, view, n_vis, bool(meta.get("intraday")), None if meta.get("intraday") else ps.df)
    except Exception as e:
        log.info("drawdown %s: %s", symbol, e)
        dd = None
    return clean({"symbol": symbol, "range": range_, **meta, "bars": _bars(view), "trendlines": tls["lines"],
                  "trend_pattern": tls["pattern"], "drawdown": dd,
                  "rsi_periods": {"short": rsi_short, "medium": rsi_period, "long": rsi_long},
                  "stoch_sets": {k: list(v) for k, v in stoch_sets.items()}})


RANGE_KO = {"1D": "오늘", "1W": "1주", "1M": "1개월", "3M": "3개월", "6M": "6개월", "1Y": "1년", "3Y": "3년", "5Y": "5년", "MAX": "전체"}


def _peak(df: pd.DataFrame, last: float, label: str, key: str) -> dict | None:
    """Highest high in `df`, how far `last` is below it, and the low / rebound since that peak."""
    if df.empty or df["high"].isna().all():
        return None
    i = df["high"].idxmax()
    peak = float(df.loc[i, "high"])
    after = df.loc[i:]
    trough_i = after["low"].idxmin()
    trough = float(after.loc[trough_i, "low"])
    t = pd.Timestamp(i)
    when = t.tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M") if t.tzinfo is not None else t.strftime("%Y-%m-%d")
    return {"key": key, "label": label, "peak": peak, "date": when, "time": _t_key(t),
            "pct": (last / peak - 1) * 100 if peak else None,
            "days": (pd.Timestamp.now(tz=t.tzinfo).normalize() - t.normalize()).days,
            "trough": trough, "trough_pct": (trough / peak - 1) * 100 if peak else None,
            "rebound_pct": (last / trough - 1) * 100 if trough else None}


def _t_key(t: pd.Timestamp) -> str | int:
    return int(t.timestamp()) if t.tzinfo is not None else t.strftime("%Y-%m-%d")


def drawdowns(symbol: str, view: pd.DataFrame, n_vis: int | None, intraday: bool, daily: pd.DataFrame | None) -> dict | None:
    """How far the last price is below the high of the selected range, of the last 52 weeks and of all loaded history."""
    if view.empty:
        return None
    last = float(view["close"].iloc[-1])
    vis = view.iloc[-n_vis:] if n_vis and not intraday else view
    if daily is None or daily.empty:
        daily = prices.get_daily(symbol, years=10).df
    else:
        long = prices.get_daily(symbol, years=10).df       # MAX range already brought more; otherwise the 10-year cache
        daily = daily if len(daily) >= len(long) else long
    if not intraday:
        last = float(daily["close"].iloc[-1]) if daily.index[-1] >= view.index[-1] else last
    first = daily.index[0]
    listed_late = first > pd.Timestamp.today() - pd.DateOffset(years=10) + pd.Timedelta(days=60)
    all_label = "상장 이후 최고" if listed_late else f"{first.year}년 이후 최고"
    yr = daily[daily.index >= pd.Timestamp.today() - pd.DateOffset(weeks=52)]
    out: list[dict] = []
    for p in (_peak(vis, last, "선택 구간 최고", "range"), _peak(yr, last, "52주 최고", "52w"), _peak(daily, last, all_label, "all")):
        if not p:
            continue
        same = next((q for q in out if abs(q["peak"] / p["peak"] - 1) < 0.001), None)   # the same high seen from a wider window
        if same:
            same["label"] = f"{same['label'].removesuffix(' 최고')} · {p['label']}"
            same["keys"].append(p["key"])
        else:
            out.append({**p, "keys": [p["key"]]})
    return {"last": last, "peaks": out, "basis": "고가(장중 최고가) 기준, 액면분할 반영 · 배당은 반영하지 않은 가격"}


def _bars(ind: pd.DataFrame) -> list[dict]:
    cols = ["open", "high", "low", "close", "volume", "sma5", "sma20", "sma50", "sma60", "sma120", "sma200", "ema20", "ema50",
            "bb_upper", "bb_mid", "bb_lower", "rsi", "rsi_short", "rsi_long", "rsi_sig", "rsi_short_sig", "rsi_long_sig",
            "stoch_short_k", "stoch_short_d", "stoch_medium_k", "stoch_medium_d", "stoch_long_k", "stoch_long_d", "macd", "macd_signal", "macd_hist", "atr", "adx", "plus_di",
            "minus_di", "obv", "stoch_k", "stoch_d", "vwap"]
    intraday = getattr(ind.index, "tz", None) is not None
    out = []
    arr = {c: ind[c].to_numpy(dtype=float) if c in ind else None for c in cols}
    sess = ind["session"].tolist() if "session" in ind else None
    for i, ts in enumerate(ind.index):
        b = {"time": int(ts.timestamp()) if intraday else ts.strftime("%Y-%m-%d")}
        for c in cols:
            v = arr[c][i] if arr[c] is not None else np.nan
            b[c] = None if np.isnan(v) else round(float(v), 6)
        if sess:
            b["session"] = sess[i]
        out.append(b)
    return out


# --------------------------------------------------------------- technicals
def _indicator_frame(symbol: str, rsi_period: int = 14, rsi_short: int = 7, rsi_long: int = 28,
                     stoch_sets: dict | None = None) -> tuple[pd.DataFrame, prices.PriceSeries]:
    ps = prices.get_daily(symbol, years=10)
    if len(ps.df) < 60:
        raise NotFoundError(f"Not enough price history for {symbol}")
    return indicators.compute_all(ps.df, rsi_period=rsi_period, rsi_short=rsi_short, rsi_long=rsi_long,
                                  stoch_sets=stoch_sets), ps


def _benchmark(symbol: str) -> str:
    return "^KS11" if symbol.endswith((".KS", ".KQ")) else "^GSPC"


def current_regime() -> dict | None:
    try:
        from .universe import latest_breadth
        b = latest_breadth()
        return macro.regime(b.get("pct_above_sma200") if b else None)
    except Exception as e:
        log.warning("regime unavailable: %s", e)
        return None


def signal_for(ind: pd.DataFrame, regime_label: str | None, weights: dict | None = None,
               fscore: float | None = None, market: str = "US", periods: dict | None = None) -> tuple[dict, dict, dict, dict, dict]:
    """Full technical evaluation of the last bar of `ind` (used for today and for change detection)."""
    ma = technical.ma_analysis(ind)
    ri = technical.rsi_analysis(ind)
    sr = levels.support_resistance(ind, vp=levels.volume_profile(ind.iloc[-126:]))
    pt = patterns.current_patterns(ind)
    sig = scoring.technical_signal(ind, ma=ma, rsi_info=ri, sr=sr, patterns=pt["active"], regime=regime_label,
                                   weights=weights, fundamental=fscore, market=market, periods=periods)
    return sig, ma, ri, sr, pt


def technicals(symbol: str, rsi_period: int = 14, weights: dict | None = None, vp_period: str = "6M",
               rsi_short: int = 7, rsi_long: int = 28, stoch_sets: dict | None = None) -> dict:
    stoch_sets = stoch_sets or indicators.STOCH_SETS
    ind, ps = _indicator_frame(symbol, rsi_period, rsi_short, rsi_long, stoch_sets)
    reg = current_regime()
    regime_label = reg["label"] if reg else None
    fscore = None
    try:
        fscore = fundamental(symbol)["score"]
    except Exception:
        pass
    mkt = market_of(symbol)
    per = {"rsi_short": rsi_short, "rsi": rsi_period, "rsi_long": rsi_long}
    sig, ma, ri, sr, pt = signal_for(ind, regime_label, weights, fscore, mkt, per)
    vp_bars = {"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "3Y": 756}.get(vp_period, 126)
    vp = levels.volume_profile(ind.iloc[-vp_bars:])
    # Same evaluation one session earlier -> "what changed" without waiting for a stored history.
    prev_sig = signal_for(ind.iloc[:-1], regime_label, weights, fscore, mkt, per)[0] if len(ind) > 260 else None
    if prev_sig:
        sig["previous"] = {"as_of": str(ind.index[-2].date()), "score": prev_sig["score"], "signal": prev_sig["signal"],
                           "components": {c["key"]: c["score"] for c in prev_sig["components"]}}
    last = ind.iloc[-1]
    snap = {k: (None if pd.isna(last.get(k)) else float(last.get(k))) for k in
            ("close", "rsi", "rsi_short", "rsi_long", "rsi_sig", "rsi_short_sig", "rsi_long_sig",
             "stoch_short_k", "stoch_short_d", "stoch_medium_k", "stoch_medium_d", "stoch_long_k", "stoch_long_d", "macd", "macd_signal", "macd_hist", "atr", "atr_pct", "adx", "plus_di", "minus_di",
             "bb_upper", "bb_lower", "bb_pctb", "bb_width", "stoch_k", "stoch_d", "vol_ratio", "mom5", "mom20", "mom60",
             "mom120", "mom250",
             "vol20", "hi52", "lo52", "vwap", "obv", "sma5", "sma20", "sma50", "sma60", "sma120", "sma200")}
    return clean({"symbol": symbol, "as_of": str(ind.index[-1].date()), "signal": sig, "ma": ma, "rsi": ri,
                  "support_resistance": sr, "volume_profile": vp, "volume_profile_period": vp_period,
                  "patterns": pt["active"], "indicators": snap, "regime": regime_label,
                  "rsi_periods": {"short": rsi_short, "medium": rsi_period, "long": rsi_long},
                  "stoch_sets": {k: list(v) for k, v in stoch_sets.items()},
                  "oscillators": technical.oscillator_states(ind),
                  "data": ps.meta(), "updated_at": utcnow().isoformat()})


def pattern_report(symbol: str) -> dict:
    ind, ps = _indicator_frame(symbol)
    pt = patterns.current_patterns(ind)
    return clean({"symbol": symbol, **pt, "data": ps.meta(),
                  "method": "일봉 OHLC의 스윙 포인트(좌우 5일 확인) 기반 탐지; 성공 = 다음날 시가 진입 후 20거래일 뒤 "
                            "예상 방향으로 움직인 경우."})


def analogs(symbol: str, lookback_years: float = 3.0) -> dict:
    ind, ps = _indicator_frame(symbol)
    bench = None
    try:
        bench = prices.get_daily(_benchmark(symbol), years=10).df["close"]
    except Exception as e:
        log.info("benchmark unavailable: %s", e)
    res = similarity.find_analogs(ind, benchmark_close=bench, lookback_years=lookback_years)
    return clean({"symbol": symbol, **res, "benchmark": _benchmark(symbol), "data": ps.meta()})


# ------------------------------------------------------------- fundamentals
def fundamental(symbol: str, p: dict | None = None) -> dict:
    """Fundamental score for the blend: Korean companies use their OpenDART statements (ranked against every listed
    company) once stored; everything else, or a Korean company DART has nothing for, uses the Yahoo-based score."""
    if symbol.endswith((".KS", ".KQ")):
        d = dart_fin.score_for(symbol)
        if d:
            return {**d, "source": "OpenDART"}
    p = p if p is not None else profile(symbol)
    f = p.get("fundamentals") or {}
    return {**fa.fundamental_score(f), "source": "Yahoo Finance"} if f else {"score": None}


def fundamentals_detail(symbol: str) -> dict:
    p = profile(symbol)
    f = p.get("fundamentals") or {}
    score = fa.fundamental_score(f) if f else {"score": None}
    earnings = cached(f"earnings:{symbol}", 12 * 3600, lambda: yahoo.earnings(symbol, limit=40))
    _persist_earnings(symbol, earnings)
    pe_hist = None
    try:
        ps = prices.get_daily(symbol, years=10)
        pe_hist = fa.pe_history(ps.df["close"], earnings, years=5)
    except Exception as e:
        log.info("pe history failed: %s", e)
    now = dt.datetime.now(dt.timezone.utc)
    past = [e for e in earnings if e["date"] <= now and e.get("eps_actual") is not None]
    upcoming = sorted([e for e in earnings if e["date"] > now], key=lambda e: e["date"])
    reaction = earnings_reaction(symbol, past)
    cal = cached(f"calendar:{symbol}", 12 * 3600, lambda: yahoo.calendar(symbol))
    return clean({
        "symbol": symbol, "fundamentals": f, "score": score, "pe_history": pe_hist,
        "next_earnings": upcoming[0] if upcoming else None,
        "earnings_estimates": {k: cal.get(k) for k in ("Earnings Average", "Earnings Low", "Earnings High",
                                                        "Revenue Average", "Revenue Low", "Revenue High")} if cal else None,
        "earnings_history": [{**e, "beat": (e["eps_actual"] > e["eps_estimate"]) if e.get("eps_estimate") is not None else None}
                             for e in sorted(past, key=lambda e: e["date"], reverse=True)[:12]],
        "earnings_reaction": reaction,
        "notes": ["매출 실제치 vs 예상치와 가이던스 변경은 유료 실적 데이터가 필요해, Yahoo 실적 캘린더의 EPS 실제치 vs 예상치만 표시합니다.",
                  "PER 백분위는 조정(Non-GAAP) EPS 기준입니다."],
        "source": "Yahoo Finance", "updated_at": utcnow().isoformat()})


def _persist_earnings(symbol: str, rows: list[dict]) -> None:
    if not rows:
        return
    try:
        with SessionLocal() as db:
            upsert(db, Earnings, [{"symbol": symbol, "earnings_date": e["date"], "eps_estimate": e.get("eps_estimate"),
                                   "eps_actual": e.get("eps_actual"), "surprise_pct": e.get("surprise_pct"),
                                   "source": "yahoo"} for e in rows], ["symbol", "earnings_date"])
            db.commit()
    except Exception as e:
        log.debug("earnings persist failed: %s", e)


def earnings_reaction(symbol: str, past: list[dict]) -> dict | None:
    """Price move from the close before the report to 1 and 5 sessions after, split by beat/miss."""
    try:
        df = prices.get_daily(symbol, years=10).df
    except Exception:
        return None
    rows = []
    for e in past:
        d = pd.Timestamp(e["date"]).tz_convert("America/New_York" if not symbol.endswith((".KS", ".KQ")) else "Asia/Seoul")
        # After-close reports trade on the next session.
        first = d.normalize().tz_localize(None) + (pd.Timedelta(days=1) if d.hour >= 16 else pd.Timedelta(0))
        after = df.index[df.index >= first]
        before = df.index[df.index < first]
        if len(after) < 6 or not len(before):
            continue
        p0 = df.loc[before[-1], "close"]
        r1 = df.loc[after[0], "close"] / p0 - 1
        r5 = df.loc[after[4], "close"] / p0 - 1
        beat = e["eps_actual"] > e["eps_estimate"] if e.get("eps_estimate") is not None else None
        rows.append({"date": str(first.date()), "surprise_pct": e.get("surprise_pct"), "beat": beat,
                     "move_1d": float(r1), "move_5d": float(r5)})
    if not rows:
        return None
    def agg(sub):
        if not sub:
            return None
        return {"n": len(sub), "avg_1d": float(np.mean([r["move_1d"] for r in sub])),
                "avg_5d": float(np.mean([r["move_5d"] for r in sub])),
                "positive_1d": float(np.mean([r["move_1d"] > 0 for r in sub]))}
    return {"events": rows[:12], "beats": agg([r for r in rows if r["beat"]]),
            "misses": agg([r for r in rows if r["beat"] is False]), "all": agg(rows)}


# -------------------------------------------------------------------- scores
def macro_view(symbol: str, sector: str | None, industry: str | None) -> dict:
    reg = current_regime()
    if not reg:
        return {"score": None, "reasons": [], "risks": ["Macro data unavailable"]}
    ft = reg["factor_trends"]
    group = macro_scoring.map_group(sector, industry)
    from .universe import group_rs
    rs_map = group_rs()
    g = macro_scoring.group_score(group, ft, rs_map.get(group)) if group else None
    country = macro_scoring.group_score("Korea", ft, rs_map.get("Korea")) if symbol.endswith((".KS", ".KQ")) else None
    betas = None
    try:
        ff = macro.factor_frame(3)
        px = prices.get_daily(symbol, years=10).df["close"]
        betas = macro_scoring.factor_betas(px, ff)
    except Exception as e:
        log.info("factor betas failed %s: %s", symbol, e)
    res = macro_scoring.stock_macro_score(g, country, betas, macro.factor_moves(60))
    res["regime"] = {"label": reg["label"], "score": reg["score"], "quadrant": reg["quadrant"]}
    return clean(res)


def scores(symbol: str, weights: dict | None = None, blend: dict | None = None) -> dict:
    """Overall = Technical / Macro / Fundamental blend (default 50/30/20, user-configurable)."""
    t = technicals(symbol, weights=weights)
    p = profile(symbol)
    m = macro_view(symbol, p.get("sector"), p.get("industry"))
    f = fundamental(symbol, p)
    blend = blend or {"technical": 0.5, "macro": 0.3, "fundamental": 0.2}
    parts = {"technical": t["signal"]["score"], "macro": m.get("score"), "fundamental": f.get("score")}
    avail = {k: v for k, v in parts.items() if v is not None}
    tw = sum(blend[k] for k in avail)
    overall = sum(blend[k] * v for k, v in avail.items()) / tw if tw else None
    rating = None
    if overall is not None:
        rating = ("Strong Buy" if overall >= 78 else "Buy" if overall >= 62 else "Neutral" if overall > 40 else
                  "Sell" if overall > 28 else "Strong Sell")
    _store_signal(symbol, t, m, f, overall)
    change = score_change(symbol)
    return clean({"symbol": symbol, "overall": overall, "overall_band": scoring.band(overall), "rating": rating,
                  "technical": t["signal"]["score"], "technical_signal": t["signal"]["signal"],
                  "macro": m.get("score"), "fundamental": f.get("score"), "blend": blend,
                  "weights_used": {k: round(blend[k] / tw, 3) for k in avail} if tw else {},
                  "macro_detail": m, "fundamental_detail": f, "horizons": t["signal"]["horizons"],
                  "change": change, "as_of": t["as_of"],
                  "disclaimer": "점수는 과거 데이터에 규칙을 적용한 요약이며, 미래 예측이나 투자 권유가 아닙니다."})


def _store_signal(symbol: str, t: dict, m: dict, f: dict, overall: float | None) -> None:
    try:
        with SessionLocal() as db:
            prev = t["signal"].get("previous")
            if prev:
                exists = db.execute(select(Signal.id).where(Signal.symbol == symbol,
                                                            Signal.as_of == dt.date.fromisoformat(prev["as_of"]))).first()
                if not exists:
                    db.add(Signal(symbol=symbol, as_of=dt.date.fromisoformat(prev["as_of"]), technical=prev["score"],
                                  signal=prev["signal"], components=prev["components"], horizons={}))
                    db.flush()
            upsert(db, Signal, [{"symbol": symbol, "as_of": dt.date.fromisoformat(t["as_of"]),
                                 "technical": t["signal"]["score"], "macro": m.get("score"), "fundamental": f.get("score"),
                                 "overall": overall, "signal": t["signal"]["signal"],
                                 "horizons": {k: v["score"] for k, v in t["signal"]["horizons"].items()},
                                 "components": {c["key"]: c["score"] for c in t["signal"]["components"]},
                                 "created_at": utcnow()}], ["symbol", "as_of"])
            db.commit()
    except Exception as e:
        log.debug("signal persist failed: %s", e)


def score_change(symbol: str, since: dt.date | None = None) -> dict | None:
    """Compare the latest stored score snapshot with the previous one (or the one at/before `since`)."""
    with SessionLocal() as db:
        rows = db.execute(select(Signal).where(Signal.symbol == symbol).order_by(Signal.as_of.desc()).limit(30)).scalars().all()
    if len(rows) < 2:
        return None
    cur = rows[0]
    prev = next((r for r in rows[1:] if since is None or r.as_of <= since), rows[1])
    comp_changes = []
    for k, v in (cur.components or {}).items():
        pv = (prev.components or {}).get(k)
        if pv is not None and v is not None and abs(v - pv) >= 5:
            comp_changes.append({"component": k, "from": pv, "to": v})
    return {"from_date": str(prev.as_of), "to_date": str(cur.as_of),
            "technical": [prev.technical, cur.technical], "macro": [prev.macro, cur.macro],
            "overall": [prev.overall, cur.overall], "signal": [prev.signal, cur.signal],
            "drivers": sorted(comp_changes, key=lambda c: -abs(c["to"] - c["from"]))}
