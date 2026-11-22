"""Trend-following flow (추세추종 플로우) - pure functions on daily bars.

The four steps of the method:
  ① 조건검색      필수 [필] + 충분 [충] conditions narrow the whole market to ~10-50 names
  ② 시장보다 강한가  beats its index over 60 days and holds up on the index's down days (≤ 2.5×)
  ③ 타점이 있는가    a breakout line (10-bar high) with a stop at the 10-bar low within 8%
  ④ 같은 테마에서 하나 keep only the strongest name per industry

Then the trade rules: stop 4-8% under the recent swing, +3% → stop to breakeven,
+25% → sell half, hold the rest until the trend breaks.
All values use bars up to the last session only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Cond:
    key: str
    kind: str          # "필" (required) or "충" (sufficient - switch with the market)
    label: str
    why: str
    needs_shares: bool = False


CONDITIONS: list[Cond] = [
    Cond("above_ma150", "필", "오늘의 종가 > 150일 이평선", "최소한의 상승 추세 확인 (150일선 자체에는 의의 없음)"),
    Cond("off_low50", "필", "52주 최저가 대비 50% 이상", "최소한의 상승 가능폭과 추세 확인 · 크게 보면 횡보인 종목을 거름"),
    Cond("value_today", "필", "일일 거래대금 {value_today} 이상", "시장의 플레이어가 참여할 수 있는 규모인지 확인"),
    Cond("volume_today", "필", "일일 거래량 {volume_today} 이상", "시장의 플레이어가 참여할 수 있는 규모인지 확인"),
    Cond("value_spike120", "필", "120봉(6달) 안에 거래대금 {value_spike120} 이상 1회", "인기주가 될 수 있었던 종목인지 확인"),
    Cond("ma150_rising", "필", "150일 이평선이 최소 1달 전부터 상승", "최소한의 상승 추세 확인 (1달 대신 2~3달로도)"),
    Cond("ma_aligned", "필", "이평 정배열 (10 ≥ 20 ≥ 50)", "단기 · 중기 이평이 위를 향해 정렬"),
    Cond("near_high25", "충", "최근 1년 최고가의 25% 이내", "매물대 없이 오를 수 있는 구조인지 확인 (지수가 고점에서 내려온 장에선 풀어도 됨)"),
    Cond("listed_max", "충", "상장주식수 {listed_max} 이하", "관심을 받을 때 희소 가치가 있는지 (숫자만으로 정해지지 않음 → 직접 확인)", True),
    Cond("float_max", "충", "유통주식수 {float_max} 이하", "희소 가치 확인 (숫자만으로 정해지지 않음 → 직접 확인)", True),
    Cond("float_ratio", "충", "유통주식수 / 상장주식수 > 40%", "이미 누군가 독점한 상태는 아닌지 확인", True),
    Cond("range5_15", "충", "최근 5일 최고 · 최저 폭 15% 이하", "변동성이 과하지 않은지 확인 (상황에 따라 10% 이하로도)"),
]
COND_BY_KEY = {c.key: c for c in CONDITIONS}
REQUIRED = [c.key for c in CONDITIONS if c.kind == "필"]
# Presets from the method: strong market = everything on; normal / weak market = [필] + the 5-day range only.
MODES = {"bull": REQUIRED + [c.key for c in CONDITIONS if c.kind == "충"],
         "normal": REQUIRED + ["range5_15"],
         "bear": REQUIRED + ["range5_15"]}

THRESHOLDS = {
    "KR": {"value_today": 5e8, "volume_today": 5e4, "value_spike120": 5e10, "listed_max": 8e7, "float_max": 5e7},
    # US: the Korean values scaled to a market of large caps (adapted, not from the method).
    "US": {"value_today": 1e7, "volume_today": 2e5, "value_spike120": 3e8, "listed_max": 3e8, "float_max": 2.5e8},
}

STOP_MAX = 8.0          # % - wider than this = 아직 타점 아님
NEAR_PIVOT = 3.0        # % below the breakout line = 돌파 대기
EXTENDED = 5.0          # % above the line = 추격 금지
VALUE_SURGE = 1.5       # breakout needs 거래대금 ≥ 1.5 × 20-day average
DOWNSIDE_MAX = 2.5      # holds within 2~2.5× the index on down days


def fmt_threshold(key: str, v: float, market: str) -> str:
    if market == "KR":
        if key.startswith("value"):
            return f"{v / 1e8:,.0f}억 원"
        return f"{v / 1e4:,.0f}만 주" if v < 1e8 else f"{v / 1e8:,.1f}억 주"
    if key.startswith("value"):
        return f"${v / 1e6:,.0f}M"
    return f"{v / 1e3:,.0f}K주" if v < 1e6 else f"{v / 1e6:,.0f}M주"


def cond_labels(market: str) -> list[dict]:
    th = THRESHOLDS[market]
    out = []
    for c in CONDITIONS:
        label = c.label.format(**{k: fmt_threshold(k, v, market) for k, v in th.items()})
        out.append({"key": c.key, "kind": c.kind, "label": label, "why": c.why, "needs_shares": c.needs_shares})
    return out


def screen_one(c: np.ndarray, h: np.ndarray, lo: np.ndarray, v: np.ndarray, market: str) -> dict | None:
    """Condition values for one symbol (arrays oldest → newest). None if history is too short."""
    n = len(c)
    if n < 200 or not np.isfinite(c[-1]) or c[-1] <= 0:
        return None
    th = THRESHOLDS[market]
    s = pd.Series(c)
    ma = {p: s.rolling(p, min_periods=p).mean().to_numpy() for p in (10, 20, 50, 150)}
    value = c * np.nan_to_num(v)
    low52 = np.nanmin(lo[-252:])
    high52 = np.nanmax(h[-252:])
    m150 = ma[150]
    d150 = np.diff(m150[-22:])
    rng5 = (np.nanmax(h[-5:]) - np.nanmin(lo[-5:])) / np.nanmin(lo[-5:]) * 100
    res = {
        "close": float(c[-1]), "value": float(value[-1]), "volume": float(np.nan_to_num(v[-1])),
        "ma150": float(m150[-1]) if np.isfinite(m150[-1]) else None,
        "low52": float(low52), "high52": float(high52), "range5": float(rng5),
        "max_value120": float(np.nanmax(value[-120:])),
    }
    res["checks"] = {
        "above_ma150": bool(np.isfinite(m150[-1]) and c[-1] > m150[-1]),
        "off_low50": bool(c[-1] >= 1.5 * low52),
        "value_today": bool(value[-1] >= th["value_today"]),
        "volume_today": bool(np.nan_to_num(v[-1]) >= th["volume_today"]),
        "value_spike120": bool(res["max_value120"] >= th["value_spike120"]),
        "ma150_rising": bool(len(d150) == 21 and np.isfinite(d150).all() and (d150 > 0).mean() >= 0.9),
        "ma_aligned": bool(np.isfinite([ma[10][-1], ma[20][-1], ma[50][-1]]).all() and ma[10][-1] >= ma[20][-1] >= ma[50][-1]),
        "near_high25": bool(c[-1] >= 0.75 * high52),
        "range5_15": bool(rng5 <= 15),
    }
    return res


def share_checks(listed: float | None, floating: float | None, market: str) -> dict[str, bool | None]:
    th = THRESHOLDS[market]
    return {"listed_max": None if listed is None else listed <= th["listed_max"],
            "float_max": None if floating is None else floating <= th["float_max"],
            "float_ratio": None if not listed or floating is None else floating / listed > 0.4}


def strength(c: np.ndarray, idx: np.ndarray) -> dict | None:
    """Step ② - relative strength against the stock's index (aligned arrays)."""
    if len(c) < 61 or len(idx) < 61:
        return None
    r = np.diff(c[-61:]) / c[-61:-1]
    ri = np.diff(idx[-61:]) / idx[-61:-1]
    ok = np.isfinite(r) & np.isfinite(ri)
    r, ri = r[ok], ri[ok]
    down = ri < 0
    ratio = float(np.mean(r[down]) / np.mean(ri[down])) if down.sum() >= 5 and np.mean(ri[down]) != 0 else None
    ex20 = (c[-1] / c[-21] - idx[-1] / idx[-21]) * 100
    ex60 = (c[-1] / c[-61] - idx[-1] / idx[-61]) * 100
    rs = c[-120:] / idx[-120:]
    rs_high = float(rs[-1] / np.nanmax(rs) * 100)
    passed = ex60 > 0 and (ratio is None or ratio <= DOWNSIDE_MAX)
    return {"excess_20d": float(ex20), "excess_60d": float(ex60), "downside_ratio": ratio, "rs_vs_high": rs_high,
            "score": float(ex20 + ex60), "passed": bool(passed)}


def entry(c: np.ndarray, h: np.ndarray, lo: np.ndarray, v: np.ndarray) -> dict:
    """Step ③ - breakout line = prior 10-bar high, stop = 10-bar low (incl. today)."""
    pivot = float(np.nanmax(h[-11:-1]))
    stop = float(np.nanmin(lo[-10:]))
    close = float(c[-1])
    risk = (pivot - stop) / pivot * 100
    value = c * np.nan_to_num(v)
    avg20 = float(np.nanmean(value[-21:-1])) if len(value) > 21 else float("nan")
    surge = float(value[-1] / avg20) if avg20 > 0 else None
    if close > pivot:
        above = (close / pivot - 1) * 100
        if above > EXTENDED:
            state = "extended"
        elif risk > STOP_MAX:
            state = "wide"
        elif surge is not None and surge >= VALUE_SURGE:
            state = "breakout"
        else:
            state = "breakout_weak"
        gap = above
    else:
        gap = (pivot / close - 1) * 100
        state = "wide" if risk > STOP_MAX else "ready" if gap <= NEAR_PIVOT else "waiting"
    return {"pivot": pivot, "stop": stop, "risk_pct": float(risk), "gap_pct": float(gap), "value_surge": surge,
            "state": state, "passed": state in ("breakout", "ready")}


STATE_KO = {"breakout": "오늘 돌파 (거래대금 동반)", "breakout_weak": "돌파했지만 거래대금 부족", "extended": "돌파선 위 5% 이상 - 추격 금지",
            "ready": "돌파 대기 (돌파선 3% 이내)", "waiting": "돌파선까지 거리 있음 - 대기", "wide": "손절폭 8% 밖 = 아직 타점 아님"}


def trade_plan(pivot: float, stop: float, close: float, breakout_today: bool) -> dict:
    """The method's rules as prices: stop, breakeven trigger (+3%), half at +25%."""
    entry_px = close if breakout_today else pivot
    return {"entry": entry_px, "stop": stop, "stop_pct": (stop / entry_px - 1) * 100,
            "breakeven_at": entry_px * 1.03, "half_at": entry_px * 1.25,
            "rules": ["손절은 최근 흔들림(10봉 저점) 아래 - 4~8% 안이어야 살 수 있는 자리",
                      "+3% 오르면 손절선을 본전으로 올림",
                      "+25%에 닿으면 절반 매도, 나머지는 추세가 끝날 때까지 (예: 종가가 20일선 아래로 마감)",
                      "돈은 가장 좋은 4~5종목에만 - 더 좋은 종목이 보이면 갈아탐"]}


def market_state(idx_close: pd.Series) -> dict:
    """상승장 / 평소 / 하락장 from the index's trend, used to suggest a condition preset."""
    c = idx_close.dropna()
    if len(c) < 200:
        return {"state": "normal", "reasons": ["지수 이력이 부족해 '평소'로 둡니다"]}
    last = float(c.iloc[-1])
    m50, m200 = float(c.rolling(50).mean().iloc[-1]), float(c.rolling(200).mean().iloc[-1])
    hi = float(c.iloc[-252:].max())
    dd = (last / hi - 1) * 100
    r = [f"지수 {last:,.0f} · 50일선 {'위' if last > m50 else '아래'} · 200일선 {'위' if last > m200 else '아래'} · 1년 고점 대비 {dd:+.1f}%"]
    if last > m50 > m200 and dd > -5:
        return {"state": "bull", "reasons": r + ["50일선 > 200일선이고 고점 근처 → 상승장"]}
    if last < m200 and m50 < m200:
        return {"state": "bear", "reasons": r + ["200일선 아래, 50일선 < 200일선 → 하락장"]}
    return {"state": "normal", "reasons": r + ["추세가 섞여 있음 → 평소"]}
