"""Fear & Greed index (공포·탐욕 지수), computed from market data.

Built on the seven-indicator approach CNN describes for its index, using only data we can
fetch for free. Each indicator becomes a 0-100 score from how far today's value sits from
its trailing one-year average, relative to its usual spread: score = 100 × Φ(z), with
z = (x − mean₂₅₂) / std₂₅₂ and the sign flipped where a higher value means fear. The index
is the equal-weight average of the available indicator scores (at least 5 of 7).

  1. 시장 모멘텀   S&P 500 / 125일 이동평균 − 1                               (높을수록 탐욕)
  2. 주가 강도     S&P 500 구성 종목 (52주 신고가 − 신저가) / 종목 수         (높을수록 탐욕)
  3. 시장 폭       S&P 500 구성 종목 McClellan 거래량 합산지수                 (높을수록 탐욕)
  4. 옵션 심리     VIX / VIX3M 5일 평균 - 풋/콜 비율(유료) 대체                (높을수록 공포)
  5. 변동성        VIX / 50일 이동평균 − 1                                     (높을수록 공포)
  6. 안전자산 수요 20일 수익률 차이: SPY − IEF(미 7~10년 국채)                 (높을수록 탐욕)
  7. 정크본드 수요 하이일드 − 투자등급 회사채 스프레드 (FRED, ICE BofA OAS)   (좁을수록 탐욕)

Everything is causal (rolling windows use data up to each date only).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import ndtr

WINDOW = 252
MIN_PERIODS = 126
BANDS = [(25, "Extreme Fear"), (45, "Fear"), (55, "Neutral"), (75, "Greed"), (101, "Extreme Greed")]
LABEL_KO = {"Extreme Fear": "극단적 공포", "Fear": "공포", "Neutral": "중립", "Greed": "탐욕", "Extreme Greed": "극단적 탐욕"}

COMPONENTS = {
    "momentum": {"label": "시장 모멘텀", "sign": 1, "source": "S&P 500 (^GSPC)",
                 "desc": "S&P 500이 125일 이동평균보다 얼마나 위/아래에 있는지"},
    "strength": {"label": "주가 강도", "sign": 1, "source": "S&P 500 구성 종목 일봉",
                 "desc": "52주 신고가 종목 수 − 신저가 종목 수 (구성 종목 대비 %)"},
    "breadth": {"label": "시장 폭", "sign": 1, "source": "S&P 500 구성 종목 일봉",
                "desc": "상승 종목 거래량 − 하락 종목 거래량으로 만든 McClellan 거래량 합산지수"},
    "options": {"label": "옵션 심리", "sign": -1, "source": "VIX, VIX3M (Cboe, Yahoo 경유)",
                "desc": "단기(1개월) 대비 3개월 내재변동성 비율 5일 평균 - 풋/콜 비율은 유료 데이터라 기간구조로 대체"},
    "volatility": {"label": "변동성", "sign": -1, "source": "VIX (^VIX)",
                   "desc": "VIX가 50일 이동평균보다 얼마나 높은지/낮은지"},
    "safe_haven": {"label": "안전자산 수요", "sign": 1, "source": "SPY, IEF",
                   "desc": "최근 20거래일 주식(SPY) 수익률 − 미 7~10년 국채(IEF) 수익률"},
    "junk": {"label": "정크본드 수요", "sign": -1, "source": "FRED BAMLH0A0HYM2 − BAMLC0A0CM",
             "desc": "하이일드 회사채 스프레드 − 투자등급 회사채 스프레드 (좁을수록 위험 선호)"},
}


def band(score: float | None) -> str | None:
    if score is None or not np.isfinite(score):
        return None
    return next(name for hi, name in BANDS if score < hi)


def to_score(x: pd.Series, sign: int, window: int = WINDOW, min_periods: int = MIN_PERIODS) -> pd.Series:
    """0-100 from the trailing z-score (normal CDF)."""
    m = x.rolling(window, min_periods=min_periods).mean()
    sd = x.rolling(window, min_periods=min_periods).std()
    z = sign * (x - m) / sd.replace(0, np.nan)
    return pd.Series(100 * ndtr(z.to_numpy(dtype=float)), index=x.index)


def member_breadth(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, volume: pd.DataFrame,
                   min_members: int = 100) -> pd.DataFrame:
    """Wide (dates × symbols) member data -> net new highs share and McClellan volume summation."""
    hi = high.rolling(252, min_periods=252).max()
    lo = low.rolling(252, min_periods=252).min()
    valid = hi.notna()
    n = valid.sum(axis=1)
    nh = ((high >= hi) & valid).sum(axis=1)
    nl = ((low <= lo) & valid).sum(axis=1)
    strength = ((nh - nl) / n.replace(0, np.nan) * 100).where(n >= min_members)

    chg = close.diff()
    up_v = volume.where(chg > 0).sum(axis=1)
    dn_v = volume.where(chg < 0).sum(axis=1)
    counted = chg.notna().sum(axis=1)
    ratio = ((up_v - dn_v) / (up_v + dn_v).replace(0, np.nan)).where(counted >= min_members)
    # McClellan: 10% and 5% trend EMAs (≈19 / 39 days) of the ratio-adjusted net volume, then summed.
    osc = (ratio.ewm(alpha=0.10, adjust=False).mean() - ratio.ewm(alpha=0.05, adjust=False).mean()) * 1000
    summation = osc.cumsum().where(ratio.notna())
    return pd.DataFrame({"members": n, "new_highs": nh, "new_lows": nl, "strength": strength,
                         "up_volume_ratio": ratio, "mcclellan_osc": osc, "breadth": summation})


def raw_components(spx: pd.Series, vix: pd.Series, vix3m: pd.Series | None, spy: pd.Series, ief: pd.Series,
                   hy_oas: pd.Series | None, ig_oas: pd.Series | None, breadth: pd.DataFrame | None) -> pd.DataFrame:
    """Raw indicator values aligned on the S&P 500 trading calendar."""
    idx = spx.index
    al = lambda s: None if s is None else s.reindex(idx.union(s.index)).sort_index().ffill(limit=5).reindex(idx)  # noqa: E731
    vix_a, spy_a, ief_a = al(vix), al(spy), al(ief)
    out = pd.DataFrame(index=idx)
    out["momentum"] = spx / spx.rolling(125, min_periods=125).mean() - 1
    out["volatility"] = vix_a / vix_a.rolling(50, min_periods=50).mean() - 1
    out["safe_haven"] = (spy_a / spy_a.shift(20) - 1) - (ief_a / ief_a.shift(20) - 1)
    out["options"] = (vix_a / al(vix3m)).rolling(5, min_periods=3).mean() if vix3m is not None else np.nan
    out["junk"] = (al(hy_oas) - al(ig_oas)) if hy_oas is not None and ig_oas is not None else np.nan
    if breadth is not None and len(breadth):
        b = breadth.reindex(idx)
        out["strength"], out["breadth"] = b["strength"], b["breadth"]
    else:
        out["strength"] = out["breadth"] = np.nan
    return out[list(COMPONENTS)]


def compute(raw: pd.DataFrame, min_components: int = 5) -> pd.DataFrame:
    """Per-indicator scores + composite index (NaN where fewer than `min_components` are available)."""
    scores = pd.DataFrame({k: to_score(raw[k].astype(float), COMPONENTS[k]["sign"]) for k in COMPONENTS}, index=raw.index)
    avail = scores.notna().sum(axis=1)
    scores["index"] = scores[list(COMPONENTS)].mean(axis=1).where(avail >= min_components)
    scores["available"] = avail
    return scores
