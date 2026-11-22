"""Fear & Greed index service (공포·탐욕 지수): data loading, member history, caching.

The two breadth indicators need ~3 years of daily bars for every S&P 500 member (52-week
window + one-year normalisation + a one-year chart). Universe scans store 2 years, so a
background job deepens member history to HISTORY_YEARS once (later scans keep it current).
Until that finishes the index is computed from the other five indicators and says so.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from ..analysis import fear_greed as fg
from ..cache import cache, cached
from ..db import SessionLocal
from ..models import DailyPrice
from ..providers.yahoo import yahoo
from . import macro, prices, universe
from .store import clean, utcnow

log = logging.getLogger(__name__)

HISTORY_YEARS = 4
NEED_DAYS = int(365.25 * 3) - 20       # first bar must be at least this old for full breadth history
READY_COVERAGE = 0.8                   # share of members with enough history
_hist = {"state": "idle", "message": None, "started_at": None, "finished_at": None, "coverage_pct": None}
_hist_lock = threading.Lock()
METHOD = ("7개 지표를 각각 최근 1년 평균 대비 위치(z-점수 → 0~100)로 바꾼 뒤 동일 가중 평균. "
          "CNN Fear & Greed와 같은 구성을 무료 데이터로 계산한 자체 지수이며 수치가 CNN과 다를 수 있습니다.")


def _sp500() -> list[str]:
    return list(universe.constituents().get("SP500") or [])


def history_coverage(symbols: list[str]) -> float:
    """Share of `symbols` whose stored daily history starts at least ~3 years back."""
    if not symbols:
        return 0.0
    cutoff = dt.date.today() - dt.timedelta(days=NEED_DAYS)
    with SessionLocal() as db:
        rows = db.execute(select(DailyPrice.symbol, func.min(DailyPrice.date))
                          .where(DailyPrice.symbol.in_(symbols)).group_by(DailyPrice.symbol)).all()
    deep = sum(1 for _, d in rows if d is not None and d <= cutoff)
    return deep / len(symbols)


def ensure_history() -> None:
    """Background: download HISTORY_YEARS of daily bars for members that lack them (idempotent)."""
    with _hist_lock:
        if _hist["state"] == "running":
            return
        _hist.update(state="running", started_at=utcnow().isoformat(), message="S&P 500 구성 종목 일봉 내려받는 중")
    try:
        syms = _sp500()
        cutoff = dt.date.today() - dt.timedelta(days=NEED_DAYS)
        with SessionLocal() as db:
            have = {s for s, d in db.execute(select(DailyPrice.symbol, func.min(DailyPrice.date))
                                             .where(DailyPrice.symbol.in_(syms)).group_by(DailyPrice.symbol)).all()
                    if d is not None and d <= cutoff}
        missing = [s for s in syms if s not in have]
        if missing:
            frames = yahoo.history_many(missing, period=f"{HISTORY_YEARS}y")
            prices.put_many_daily(frames, years=HISTORY_YEARS)
        cov = history_coverage(syms)
        _hist.update(state="done", finished_at=utcnow().isoformat(), coverage_pct=round(cov * 100, 1),
                     message=f"{len(missing)}개 종목 보충 · 이력 충분 {cov * 100:.0f}%")
        cache.delete_prefix("fg:")
    except Exception as e:
        log.exception("fear & greed history fill failed")
        _hist.update(state="error", finished_at=utcnow().isoformat(), message=str(e)[:200])


def _member_wide(symbols: list[str], years: int = HISTORY_YEARS) -> dict[str, pd.DataFrame]:
    start = dt.date.today() - dt.timedelta(days=int(365.25 * years) + 10)
    with SessionLocal() as db:
        rows = db.execute(select(DailyPrice.symbol, DailyPrice.date, DailyPrice.high, DailyPrice.low,
                                 DailyPrice.close, DailyPrice.volume)
                          .where(DailyPrice.symbol.in_(symbols), DailyPrice.date >= start)).all()
    df = pd.DataFrame(rows, columns=["symbol", "date", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return {c: df.pivot_table(index="date", columns="symbol", values=c, aggfunc="last").sort_index()
            for c in ("high", "low", "close", "volume")}


def _series(sym: str, col: str = "close") -> pd.Series | None:
    try:
        return prices.get_daily(sym, years=10).df[col]
    except Exception as e:
        log.warning("fear & greed input %s unavailable: %s", sym, e)
        return None


def _f(x) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def _explain(key: str, raw: pd.DataFrame, extra: dict, d: pd.Timestamp) -> str:
    x = _f(raw.at[d, key])
    if x is None:
        return "데이터를 불러올 수 없습니다 (Data unavailable)"
    if key == "momentum":
        return f"S&P 500 {extra['spx']:,.0f} · 125일 이동평균 대비 {x * 100:+.1f}%"
    if key == "strength":
        b = extra["breadth"]
        return f"52주 신고가 {int(b['new_highs'])}개 · 신저가 {int(b['new_lows'])}개 → 순 {x:+.1f}% ({int(b['members'])}종목)"
    if key == "breadth":
        b = extra["breadth"]
        share = (b["up_volume_ratio"] + 1) / 2 * 100 if _f(b["up_volume_ratio"]) is not None else None
        tail = f" · 오늘 상승 종목 거래량 비중 {share:.0f}%" if share is not None else ""
        return f"McClellan 거래량 합산지수 {x:,.0f} (오실레이터 {b['mcclellan_osc']:+.0f}){tail}"
    if key == "options":
        return f"VIX/VIX3M 5일 평균 {x:.2f} - " + ("역전(단기 변동성이 더 높음): 단기 공포" if x > 1 else "정상 기간구조(콘탱고)")
    if key == "volatility":
        return f"VIX {extra['vix']:.1f} · 50일 이동평균 대비 {x * 100:+.1f}%"
    if key == "safe_haven":
        return f"20거래일 수익률: 주식(SPY) {extra['spy20'] * 100:+.1f}% vs 국채(IEF) {extra['ief20'] * 100:+.1f}% → 차이 {x * 100:+.1f}%p"
    if key == "junk":
        avg = raw[key].loc[:d].iloc[-252:].mean()
        return f"하이일드 − 투자등급 스프레드 {x:.2f}%p (1년 평균 {avg:.2f}%p)"
    return f"{x:.3f}"


def _compute(ready: bool) -> dict:
    spx = _series("^GSPC")
    if spx is None or len(spx) < 400:
        raise RuntimeError("S&P 500 가격 데이터를 불러올 수 없습니다")
    vix, vix3m = _series("^VIX"), _series("^VIX3M")
    spy, ief = _series("SPY", "adj_close"), _series("IEF", "adj_close")
    hy, ig = macro._fred_one("BAMLH0A0HYM2"), macro._fred_one("BAMLC0A0CM")
    members = _sp500()
    breadth = None
    if ready:
        w = _member_wide(members)
        breadth = fg.member_breadth(w["close"], w["high"], w["low"], w["volume"])
    raw = fg.raw_components(spx, vix, vix3m, spy, ief, hy, ig, breadth)
    sc = fg.compute(raw)
    d = sc["index"].last_valid_index()
    if d is None:
        raise RuntimeError("지표 계산에 필요한 이력이 부족합니다")
    pos = sc.index.get_loc(d)

    def at(back: int) -> dict | None:
        i = pos - back
        if i < 0 or not np.isfinite(sc["index"].iloc[i]):
            return None
        v = float(sc["index"].iloc[i])
        return {"date": str(sc.index[i].date()), "value": round(v, 1), "label": fg.band(v)}

    b_row = breadth.loc[d].to_dict() if breadth is not None and d in breadth.index else {}
    spy_a = spy.reindex(sc.index).ffill() if spy is not None else None
    ief_a = ief.reindex(sc.index).ffill() if ief is not None else None
    extra = {"spx": float(spx.loc[d]), "breadth": b_row,
             "vix": float(vix.reindex(sc.index).ffill().loc[d]) if vix is not None else float("nan"),
             "spy20": float(spy_a.loc[d] / spy_a.shift(20).loc[d] - 1) if spy_a is not None else float("nan"),
             "ief20": float(ief_a.loc[d] / ief_a.shift(20).loc[d] - 1) if ief_a is not None else float("nan")}
    week_ago = sc.index[max(0, pos - 5)]
    comps = []
    for k, meta in fg.COMPONENTS.items():
        s = _f(sc.at[d, k])
        prev = _f(sc.at[week_ago, k])
        pending = k in ("strength", "breadth") and not ready
        spark = sc[k].iloc[max(0, pos - 251):pos + 1].dropna()
        comps.append({"key": k, "label": meta["label"], "score": None if s is None else round(s, 1),
                      "band": fg.band(s), "change_1w": None if s is None or prev is None else round(s - prev, 1),
                      "detail": "S&P 500 구성 종목 3년 이력 준비 중 - 완료되면 자동 반영" if pending else _explain(k, raw, extra, d),
                      "description": meta["desc"], "source": meta["source"],
                      "direction": "높을수록 탐욕" if meta["sign"] > 0 else "높을수록 공포",
                      "sparkline": [round(float(v), 1) for v in spark.iloc[::5]]})
    hist = sc["index"].iloc[max(0, pos - 3 * 252):pos + 1].dropna()
    spx_h = spx.reindex(hist.index)
    value = float(sc.at[d, "index"])
    return clean({
        "as_of": str(d.date()), "value": round(value, 1), "label": fg.band(value),
        "available": int(sc.at[d, "available"]), "total": len(fg.COMPONENTS),
        "compare": {"previous": at(1), "week": at(5), "month": at(21), "year": at(252)},
        "components": comps,
        "history": [{"date": str(t.date()), "value": round(float(v), 1), "spx": _f(spx_h.loc[t])} for t, v in hist.items()],
        "bands": [{"max": hi, "label": name} for hi, name in fg.BANDS],
        "method": METHOD,
        "notes": ["CNN 공식 지수 데이터는 자동 수집을 막아 두어 사용하지 않고, 같은 구성으로 직접 계산합니다.",
                  "주가 강도·시장 폭은 NYSE 전체 대신 현재 S&P 500 구성 종목으로 계산합니다 (생존 편향 있음).",
                  "풋/콜 비율은 무료로 받을 수 있는 신뢰할 만한 소스가 없어 VIX 기간구조(VIX/VIX3M)로 대체합니다.",
                  "하이일드·투자등급 스프레드는 FRED(ICE BofA) 일간 데이터로, 하루 늦게 반영될 수 있습니다."],
        "breadth_history": {**_hist, "ready": ready},
        "updated_at": utcnow().isoformat()})


def _retry_due() -> bool:
    """Start a history fill if none ran yet, or the last one ended over an hour ago."""
    if _hist["state"] == "running":
        return False
    if _hist["finished_at"] is None:
        return True
    return utcnow() - dt.datetime.fromisoformat(_hist["finished_at"]) > dt.timedelta(hours=1)


def index_payload() -> dict:
    ready = cached("fg:ready", 600, lambda: history_coverage(_sp500()) >= READY_COVERAGE)
    if not ready and _retry_due():
        threading.Thread(target=ensure_history, name="fg-history", daemon=True).start()
    return cached(f"fg:index:{ready}", 1800, lambda: _compute(ready))
