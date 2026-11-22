"""Daily regime labels used to split a backtest by market environment (analysis/regime_perf.py).

Three views, each from data the app already has:
  * rates      - US 10Y yield (^TNX) vs 126 sessions earlier (+/-0.25%p).
  * sentiment  - the app's own Fear & Greed index history.
  * risk       - risk-on when the benchmark index is above its 200-day average and VIX < 25
                 (the same filter the backtester's regime-aware strategies use).
"""
from __future__ import annotations

import logging

import pandas as pd

from ..analysis import backtest as bt
from ..cache import cached
from . import prices

log = logging.getLogger(__name__)

RATE_WINDOW = 126
RATE_BAND = 0.25
RATE_ORDER = ["금리 상승기", "금리 횡보기", "금리 하락기"]
FG_ORDER = ["공포", "중립", "탐욕"]
RISK_ORDER = ["위험 선호", "위험 회피"]


def _rates(years: int) -> pd.Series | None:
    try:
        y = prices.get_daily("^TNX", years=years).df["close"].dropna()
    except Exception:
        log.warning("regimes: ^TNX unavailable", exc_info=True)
        return None
    chg = (y - y.shift(RATE_WINDOW)).dropna()
    if chg.empty:
        return None
    return chg.map(lambda x: RATE_ORDER[0] if x > RATE_BAND else RATE_ORDER[2] if x < -RATE_BAND else RATE_ORDER[1])


def _sentiment() -> pd.Series | None:
    try:
        from . import fear_greed
        hist = fear_greed.index_payload().get("history") or []
    except Exception:
        log.warning("regimes: fear & greed unavailable", exc_info=True)
        return None
    rows = [(pd.Timestamp(h["date"]), h["value"]) for h in hist if h.get("value") is not None]
    if not rows:
        return None
    s = pd.Series(dict(rows)).sort_index()
    return s.map(lambda v: FG_ORDER[0] if v < 45 else FG_ORDER[2] if v >= 55 else FG_ORDER[1])


def _risk(symbol: str, years: int) -> pd.Series | None:
    try:
        bench = prices.get_daily("^KS11" if symbol.endswith((".KS", ".KQ")) else "^GSPC", years=years).df["close"]
        vix = prices.get_daily("^VIX", years=years).df["close"]
    except Exception:
        log.warning("regimes: benchmark unavailable", exc_info=True)
        return None
    reg = bt.regime_series(bench, vix)
    if reg is None:
        return None
    # the 200-day average needs 200 sessions before the label means anything
    reg = reg.iloc[200:]
    return reg.map(lambda v: RISK_ORDER[0] if v else RISK_ORDER[1]) if len(reg) else None


def dimensions(symbol: str, years: int = 10) -> list[dict]:
    kr = symbol.endswith((".KS", ".KQ"))

    def build():
        return [
            {"key": "rates", "name": "금리 방향", "labels": _rates(years), "order": RATE_ORDER,
             "note": f"미 10년물 금리(^TNX)가 {RATE_WINDOW}거래일(약 6개월) 전보다 {RATE_BAND:g}%p 넘게 올랐으면 상승기, 내렸으면 하락기"},
            {"key": "sentiment", "name": "공포 · 탐욕", "labels": _sentiment(), "order": FG_ORDER,
             "note": "이 앱의 공포·탐욕 지수 (45 미만 공포 · 55 이상 탐욕). 지수 이력이 있는 기간만 집계"},
            {"key": "risk", "name": "매크로 국면", "labels": _risk(symbol, years), "order": RISK_ORDER,
             "note": f"{'코스피' if kr else 'S&P 500'}가 200일선 위이고 VIX가 25 미만이면 위험 선호, 아니면 위험 회피"},
        ]
    return cached(f"regimes:dims:{'KR' if kr else 'US'}:{years}", 3600, build)
