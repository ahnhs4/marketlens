"""Which indicators were already warning before S&P 500 declines of 10% or more?

Every indicator gets one rule, written down before looking at the results (common textbook thresholds, not tuned).
Each rule gives a daily on/off state on the S&P 500 trading calendar, using only what was published by that day:
monthly data count from about three weeks after the month ends, weekly data from a week later. Data revisions are not
undone (FRED serves the latest vintage), so revised series such as jobless claims look slightly better than they were.

Three checks per indicator, each over the indicator's own history:
* coverage - of the declines that started near a 52-week high (not the second leg of a bear market), how many had the
  warning on at some point in the six months before the peak;
* lift at highs - with the S&P 500 within 3% of its 52-week high, how much more often a 10% fall followed within six months
  when the warning was on. Measured only at highs, because inside a bear market almost everything "predicts" the next leg;
* precision - of the days it switched on (after 20 days off), how many were followed by a 10% fall within six months.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable

import numpy as np
import pandas as pd

from ..cache import cached
from ..providers.fred import fred
from .risk_signals import REARM, SWING, _cboe, _close, crossings, declines, forward_drawdowns
from .store import clean, utcnow

log = logging.getLogger(__name__)
LOOK = 126                 # six months of trading days: the window before a peak, and the horizon after a signal
NEAR_HIGH = 0.97           # "at highs" = within 3% of the 52-week closing high
FRED_IDS = ("T10Y3M", "T10Y2Y", "BAA10Y", "NFCI", "STLFSI4", "SAHMREALTIME", "ICSA", "DFF", "DGS10", "UMCSENT", "PERMIT", "DCOILWTICO", "DRTSCILM")
YAHOO = ("^DJT", "^SOX", "^RUT", "RSP", "SPY", "HYG", "IEF", "XLU", "HG=F", "GC=F", "DX-Y.NYB")
# publication lag in days and how stale a value may get before it no longer counts
LAG = {"SAHMREALTIME": (35, 70), "UMCSENT": (35, 70), "PERMIT": (50, 80), "DRTSCILM": (40, 130), "ICSA": (5, 21), "NFCI": (5, 21),
       "STLFSI4": (7, 21), "DCOILWTICO": (1, 10)}


def _aligned(s: pd.Series, days: pd.DatetimeIndex, lag: int = 0, stale: int = 7) -> pd.Series:
    """The latest value published by each trading day, or NaN once it is older than `stale` days."""
    s = s.dropna()
    s = s[~s.index.duplicated(keep="last")]
    s.index = pd.to_datetime(s.index) + pd.Timedelta(days=lag)
    both = s.index.union(days)
    val = s.reindex(both).ffill()
    seen = pd.Series(s.index, index=s.index).reindex(both).ffill()
    val[(both.to_series() - seen).dt.days > stale] = np.nan
    return val.reindex(days)


def _state(cond: pd.Series, *inputs: pd.Series) -> pd.Series:
    """1/0 where every input is known, NaN elsewhere."""
    known = pd.concat(inputs, axis=1).notna().all(axis=1)
    return cond.astype(float).where(known)


def _load(px: pd.Series) -> dict[str, pd.Series]:
    days, D = px.index, {}
    for sid in FRED_IDS:
        lag, stale = LAG.get(sid, (1, 10))
        try:
            D[sid] = _aligned(fred.series(sid, start=dt.date(1950, 1, 1)), days, lag, stale)
        except Exception as e:      # one missing series must not sink the study
            log.warning("leading study: %s unavailable (%s)", sid, e)
    for sym in YAHOO:
        try:
            D[sym] = _aligned(_close(sym, 40), days, 0, 7)
        except Exception as e:
            log.warning("leading study: %s unavailable (%s)", sym, e)
    for name in ("VIX", "VIX3M", "COR1M"):
        try:
            D[name] = _aligned(_cboe(name), days, 0, 7)
        except Exception as e:
            log.warning("leading study: %s unavailable (%s)", name, e)
    return D


def _rules(px: pd.Series, D: dict[str, pd.Series]) -> list[dict]:
    hi52 = px.rolling(252, min_periods=200).max()
    near = px / hi52 >= NEAR_HIGH
    ch = lambda s, n: s - s.shift(n)                       # noqa: E731
    ret = lambda s, n: s / s.shift(n) - 1                  # noqa: E731
    rel = lambda a, n: ret(a, n) - ret(px, n)              # noqa: E731
    R: list[tuple[str, str, str, str, Callable[[], pd.Series]]] = [
        # key, group, label, rule
        ("curve_3m", "금리·연준", "장단기 금리차 역전 (10년−3개월)", "지난 12개월 안에 한 번이라도 10년−3개월 금리차가 0 아래",
         lambda: _state((D["T10Y3M"] < 0).astype(float).rolling(252, min_periods=200).max() == 1, D["T10Y3M"])),
        ("curve_2y", "금리·연준", "장단기 금리차 역전 (10년−2년)", "지난 12개월 안에 한 번이라도 10년−2년 금리차가 0 아래",
         lambda: _state((D["T10Y2Y"] < 0).astype(float).rolling(252, min_periods=200).max() == 1, D["T10Y2Y"])),
        ("fed_hikes", "금리·연준", "연준 금리 인상 사이클", "기준금리가 12개월 전보다 1%p 이상 높음", lambda: _state(ch(D["DFF"], 252) >= 1.0, ch(D["DFF"], 252))),
        ("yield_shock", "금리·연준", "10년물 금리 급등", "10년 국채금리가 12개월 전보다 1%p 이상 높음", lambda: _state(ch(D["DGS10"], 252) >= 1.0, ch(D["DGS10"], 252))),
        ("credit_widen", "신용·금융여건", "회사채 스프레드 확대 (Baa−10년)", "무디스 Baa 금리 − 10년 국채가 3개월 전보다 0.3%p 이상 확대",
         lambda: _state(ch(D["BAA10Y"], 63) >= 0.3, ch(D["BAA10Y"], 63))),
        ("hy_diverge", "신용·금융여건", "하이일드가 국채보다 약세 (주가는 고점권)", "HYG/IEF가 3개월간 3% 이상 하락, S&P 500은 52주 고점의 3% 이내",
         lambda: _state((ret(D["HYG"] / D["IEF"], 63) <= -0.03) & near, ret(D["HYG"] / D["IEF"], 63))),
        ("nfci_rise", "신용·금융여건", "금융여건 긴축 (시카고 연준 NFCI)", "NFCI가 13주 전보다 0.15 이상 상승", lambda: _state(ch(D["NFCI"], 63) >= 0.15, ch(D["NFCI"], 63))),
        ("stress", "신용·금융여건", "금융 스트레스 평균 이상 (세인트루이스 연준)", "STLFSI가 0보다 큼", lambda: _state(D["STLFSI4"] > 0, D["STLFSI4"])),
        ("lending", "신용·금융여건", "은행 대출 기준 강화 (SLOOS)", "기업대출 기준을 강화한 은행 비율 − 완화한 비율이 20%p 이상", lambda: _state(D["DRTSCILM"] >= 20, D["DRTSCILM"])),
        ("sahm", "경기", "삼의 법칙 (실업률)", "실시간 삼의 법칙 지표가 0.5 이상", lambda: _state(D["SAHMREALTIME"] >= 0.5, D["SAHMREALTIME"])),
        ("claims", "경기", "실업수당 청구 증가", "4주 평균 신규 청구가 52주 최저보다 15% 이상 많음",
         lambda: _state(D["ICSA"].rolling(20).mean() / D["ICSA"].rolling(20).mean().rolling(252, min_periods=200).min() - 1 >= 0.15, D["ICSA"].rolling(20).mean().rolling(252, min_periods=200).min())),
        ("permits", "경기", "주택 인허가 감소", "건축 허가 건수가 12개월 전보다 15% 이상 감소", lambda: _state(ret(D["PERMIT"], 252) <= -0.15, ret(D["PERMIT"], 252))),
        ("sentiment", "경기", "소비자심리 급락 (미시간대)", "소비자심리지수가 12개월 전보다 15포인트 이상 낮음", lambda: _state(ch(D["UMCSENT"], 252) <= -15, ch(D["UMCSENT"], 252))),
        ("oil", "원자재·달러", "유가 급등", "WTI가 12개월 전보다 50% 이상 높음", lambda: _state(ret(D["DCOILWTICO"].where(D["DCOILWTICO"] > 0), 252) >= 0.5, ret(D["DCOILWTICO"].where(D["DCOILWTICO"] > 0), 252))),
        ("dollar", "원자재·달러", "달러 급등", "달러 인덱스가 12개월 전보다 8% 이상 높음", lambda: _state(ret(D["DX-Y.NYB"], 252) >= 0.08, ret(D["DX-Y.NYB"], 252))),
        ("copper_gold", "원자재·달러", "구리/금 비율 하락", "구리/금 가격 비율이 3개월간 10% 이상 하락", lambda: _state(ret(D["HG=F"] / D["GC=F"], 63) <= -0.10, ret(D["HG=F"] / D["GC=F"], 63))),
        ("breadth", "시장 내부", "시장 폭 축소 (동일가중 약세)", "RSP가 SPY보다 3개월간 3%p 이상 뒤처짐, S&P 500은 고점권",
         lambda: _state((ret(D["RSP"], 63) - ret(D["SPY"], 63) <= -0.03) & near, ret(D["RSP"], 63) - ret(D["SPY"], 63))),
        ("small_caps", "시장 내부", "소형주 약세 (러셀 2000)", "러셀 2000이 S&P 500보다 3개월간 5%p 이상 뒤처짐, S&P 500은 고점권",
         lambda: _state((rel(D["^RUT"], 63) <= -0.05) & near, rel(D["^RUT"], 63))),
        ("transports", "시장 내부", "운송주 약세 (다우 이론)", "다우 운송지수가 3개월간 5% 이상 하락, S&P 500은 고점권",
         lambda: _state((ret(D["^DJT"], 63) <= -0.05) & near, ret(D["^DJT"], 63))),
        ("semis", "시장 내부", "반도체 약세", "필라델피아 반도체지수가 S&P 500보다 3개월간 8%p 이상 뒤처짐, S&P 500은 고점권",
         lambda: _state((rel(D["^SOX"], 63) <= -0.08) & near, rel(D["^SOX"], 63))),
        ("defensives", "시장 내부", "유틸리티 강세 (방어주 선호)", "XLU가 S&P 500보다 3개월간 5%p 이상 앞섬, S&P 500은 고점권",
         lambda: _state((rel(D["XLU"], 63) >= 0.05) & near, rel(D["XLU"], 63))),
        ("vix_low", "변동성·심리", "VIX 매우 낮음 (안일함)", "VIX 1개월 평균이 13 미만", lambda: _state(D["VIX"].rolling(21).mean() < 13, D["VIX"].rolling(21).mean())),
        ("vix_up_at_high", "변동성·심리", "고점권에서 VIX 상승", "VIX가 3개월 최저보다 30% 이상 높음, S&P 500은 고점권",
         lambda: _state((D["VIX"] / D["VIX"].rolling(63).min() - 1 >= 0.30) & near, D["VIX"].rolling(63).min())),
        ("vix_inverted", "변동성·심리", "VIX 기간구조 역전", "VIX(1개월)가 VIX3M(3개월)보다 높음", lambda: _state(D["VIX"] > D["VIX3M"], D["VIX"], D["VIX3M"])),
        ("cor_low", "변동성·심리", "내재 상관 매우 낮음 (COR1M)", "Cboe COR1M이 15 미만", lambda: _state(D["COR1M"] < 15, D["COR1M"])),
        ("overheated", "가격", "1년 상승률 25% 이상 (과열)", "S&P 500이 12개월 전보다 25% 이상 높음", lambda: _state(ret(px, 252) >= 0.25, ret(px, 252))),
        ("below_200", "가격", "200일선 아래 (참고: 후행)", "S&P 500 종가가 200일 이동평균 아래", lambda: _state(px < px.rolling(200).mean(), px.rolling(200).mean())),
    ]
    out = []
    for key, group, label, rule, fn in R:
        try:
            out.append({"key": key, "group": group, "label": label, "rule": rule, "on": fn()})
        except KeyError as e:
            log.warning("leading study: %s skipped, missing %s", key, e)
    return out


def _evaluate(ind: dict, px: pd.Series, falls: list[dict], y: pd.Series, near: pd.Series) -> dict:
    on = ind["on"]
    ok = on.notna() & y.notna()
    o, lab = on[ok], y[ok].astype(bool)
    base = float(lab.mean()) if len(lab) else None
    p_on = float(lab[o == 1].mean()) if (o == 1).any() else None
    p_off = float(lab[o == 0].mean()) if (o == 0).any() else None
    hi = near[ok].astype(bool)
    base_hi = float(lab[hi].mean()) if hi.any() else None
    p_on_hi = float(lab[hi & (o == 1)].mean()) if (hi & (o == 1)).sum() >= 20 else None
    idx = px.index
    seen = []
    for f in falls:
        p = int(idx.get_loc(f["peak"]))
        win = on.iloc[max(0, p - LOOK):p + 1]
        if p < LOOK or win.notna().mean() < 0.5 or not f["fresh"]:
            seen.append(None)
            continue
        hit = win[win == 1]
        seen.append({"hit": bool(len(hit)), "lead": int((f["peak"] - hit.index[0]).days) if len(hit) else None, "at_peak": bool(win.iloc[-1] == 1)})
    known = [s for s in seen if s]
    starts = [d for d in crossings(on.fillna(0), 0.5, True) if not pd.isna(y.get(d))]
    good = sum(bool(y[d]) for d in starts)
    leads = [s["lead"] for s in known if s["hit"] and s["lead"] is not None]
    return {
        "key": ind["key"], "group": ind["group"], "label": ind["label"], "rule": ind["rule"],
        "since": None if not on.notna().any() else on.first_valid_index().strftime("%Y-%m-%d"),
        "on_now": None if pd.isna(on.iloc[-1]) else bool(on.iloc[-1] == 1),
        "on_share": round(float((o == 1).mean()), 3) if len(o) else None,
        "base": None if base is None else round(base, 3), "p_on": None if p_on is None else round(p_on, 3), "p_off": None if p_off is None else round(p_off, 3),
        "lift": None if not base or p_on is None else round(p_on / base, 2),
        "base_hi": None if base_hi is None else round(base_hi, 3), "p_on_hi": None if p_on_hi is None else round(p_on_hi, 3),
        "lift_hi": None if not base_hi or p_on_hi is None else round(p_on_hi / base_hi, 2),
        "on_share_hi": round(float((o[hi] == 1).mean()), 3) if hi.any() else None,
        "declines_seen": len(known), "declines_hit": sum(s["hit"] for s in known),
        "coverage": round(sum(s["hit"] for s in known) / len(known), 3) if known else None,
        "median_lead_days": int(np.median(leads)) if leads else None,
        "signals": len(starts), "signals_good": good, "precision": round(good / len(starts), 3) if starts else None,
        "_seen": seen,
    }


def study() -> dict:
    def build():
        px = _close("^GSPC", 40)
        px = px[px.index >= "1985-01-01"]
        hi52 = px.rolling(252, min_periods=200).max()
        near = (px / hi52 >= NEAR_HIGH)
        falls = [{**f, "fresh": bool(px.loc[f["peak"]] >= NEAR_HIGH * hi52.loc[f["peak"]])} for f in declines(px)]
        fwd = forward_drawdowns(px)[LOOK]
        y = (fwd <= -SWING).astype(float).where(fwd.notna())
        D = _load(px)
        rows = [_evaluate(ind, px, falls, y, near) for ind in _rules(px, D)]
        keep = [i for i, f in enumerate(falls) if f["fresh"] and f["peak"] >= pd.Timestamp("1990-01-01")]
        shown = [falls[i] for i in keep]
        matrix = {}
        for r in rows:
            seen = r.pop("_seen")
            matrix[r["key"]] = [None if seen[i] is None else (1 if seen[i]["hit"] else 0) for i in keep]
        return {
            "look_days": LOOK, "swing": SWING, "near_high": NEAR_HIGH, "rearm_days": REARM,
            "declines": [{"peak": f["peak"].strftime("%Y-%m-%d"), "trough": f["trough"].strftime("%Y-%m-%d"), "depth": round(f["depth"], 4), "open": f["open"]} for f in shown],
            "indicators": rows, "matrix": matrix,
            "now": {"date": px.index[-1].strftime("%Y-%m-%d"), "on": [r["key"] for r in rows if r["on_now"]]},
            "updated_at": utcnow().isoformat(),
        }
    return clean(cached("risk:leading", 12 * 3600, build))
