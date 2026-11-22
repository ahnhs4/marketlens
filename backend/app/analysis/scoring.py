"""Technical scoring and the Buy/Sell signal engine.

Every component score is 0-100 with 50 = neutral, built from explicit,
piecewise-linear rules on measured values. The composite is

    Score = 50 + sum_i w_i * (s_i - 50)            (weights sum to 1)

so each component's contribution is directly readable as signed points
("+12 Trend, -4 Volatility") in the UI's "Why?" panel.

The signal is never driven by a single indicator: the label requires the
composite to clear a threshold AND passes a set of gates (trend filter, regime
filter, overbought filter).

Two scoring styles (설정 > 채점 방식), applied everywhere (stock page, portfolio, recommendations):
  * 'user' (내 규칙, default): the user's rules in analysis/user_rules.py - overbought on the short /
    medium / long RSI, a price at its high or under resistance and dead crosses are marked down hard;
    oversold, turnarounds and golden crosses are marked up hard. Timing gets 35% of the technical score,
    breakouts to new highs earn nothing, and the RSI component reads overbought as negative.
  * 'evidence' (데이터 검증): only rules whose forward returns were measured on this app's own price
    history, separately for the US and Korea (analysis/timing_evidence.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..i18n_ko import STATUS_KO, STRUCTURE_KO, ZONE_KO, pattern as pko
from . import user_rules
from .timing_evidence import RULES as TIMING_RULES

# Scoring style: services set a provider that reads 설정 (preferences.timing_style); tests pass `style`.
STYLE_PROVIDER = {"get": lambda: "user"}


def current_style() -> str:
    try:
        s = STYLE_PROVIDER["get"]()
    except Exception:
        s = "user"
    return s if s in ("user", "evidence") else "user"

DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 0.18, "momentum": 0.12, "rsi": 0.08, "volume": 0.08, "breakout": 0.08,
    "support_resistance": 0.10, "volatility": 0.06, "pattern": 0.10, "timing": 0.20,
}
HORIZON_WEIGHTS: dict[str, dict[str, float]] = {
    "short": {"trend_short": 0.15, "momentum_short": 0.15, "rsi": 0.15, "volume": 0.10, "breakout": 0.05,
              "support_resistance": 0.15, "timing": 0.25},
    "medium": {"trend": 0.25, "momentum": 0.18, "macd": 0.12, "pattern": 0.12, "adx": 0.08, "volume": 0.10, "timing": 0.15},
    "long": {"trend_long": 0.30, "momentum_long": 0.20, "drawdown": 0.15, "volatility": 0.10, "fundamental": 0.15,
             "timing": 0.10},
}
# 'user' style: timing dominates, new highs earn nothing.
DEFAULT_WEIGHTS_USER: dict[str, float] = {
    "trend": 0.15, "momentum": 0.08, "rsi": 0.10, "volume": 0.07, "breakout": 0.0,
    "support_resistance": 0.10, "volatility": 0.05, "pattern": 0.10, "timing": 0.35,
}
HORIZON_WEIGHTS_USER: dict[str, dict[str, float]] = {
    "short": {"trend_short": 0.10, "momentum_short": 0.10, "rsi": 0.20, "volume": 0.10, "support_resistance": 0.15,
              "timing": 0.35},
    "medium": {"trend": 0.20, "momentum": 0.10, "macd": 0.10, "pattern": 0.10, "adx": 0.05, "volume": 0.05, "timing": 0.40},
    "long": {"trend_long": 0.25, "momentum_long": 0.10, "drawdown": 0.20, "volatility": 0.05, "fundamental": 0.15,
             "timing": 0.25},
}
SIGNAL_THRESHOLDS = {"strong_buy": 78.0, "buy": 62.0, "sell": 40.0, "strong_sell": 28.0}
SCORE_BANDS = [(90, "Exceptional"), (80, "Strong"), (70, "Positive"), (60, "Moderately Positive"),
               (50, "Neutral"), (40, "Weak"), (30, "Very Weak"), (0, "Highly Risky")]


def band(score: float | None) -> str | None:
    if score is None:
        return None
    for lo, name in SCORE_BANDS:
        if score >= lo:
            return name
    return "Highly Risky"


def lin(x: float | None, x0: float, x1: float, y0: float = 0.0, y1: float = 100.0) -> float:
    """Clamp-interpolate x from [x0, x1] onto [y0, y1]."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 50.0
    if x1 == x0:
        return y1
    t = (x - x0) / (x1 - x0)
    return float(y0 + (y1 - y0) * min(1.0, max(0.0, t)))


def _g(row: pd.Series, k: str) -> float | None:
    v = row.get(k)
    return None if v is None or pd.isna(v) else float(v)


@dataclass
class Component:
    key: str
    label: str
    score: float
    details: list[str] = field(default_factory=list)

    def to_dict(self, weight: float | None = None) -> dict:
        d = {"key": self.key, "label": self.label, "score": round(self.score, 1), "details": self.details}
        if weight is not None:
            d["weight"] = weight
            d["points"] = round(weight * (self.score - 50), 1)
        return d


# --------------------------------------------------------------------- components
def trend_component(ind: pd.DataFrame) -> Component:
    r = ind.iloc[-1]
    c = float(r["close"])
    det, pts = [], []
    smas = {p: _g(r, f"sma{p}") for p in (5, 20, 60, 120, 200)}
    chain = [p for p in (5, 20, 60, 120, 200) if smas[p] is not None]
    if len(chain) >= 3:
        good = sum(smas[a] > smas[b] for a, b in zip(chain, chain[1:]))
        pts.append(lin(good / (len(chain) - 1), 0, 1))
        det.append(f"이평선 정배열 {good}/{len(chain) - 1}쌍 ({' > '.join(map(str, sorted(chain, key=lambda p: -smas[p])))})")
    if smas[200]:
        d = (c / smas[200] - 1) * 100
        pts.append(lin(d, -15, 15))
        det.append(f"주가가 SMA200 대비 {d:+.1f}%")
    if smas[60]:
        d = (c / smas[60] - 1) * 100
        pts.append(lin(d, -10, 10))
    s200 = ind["sma200"]
    if len(s200.dropna()) > 21:
        sl = (s200.iloc[-1] / s200.iloc[-21] - 1) * 100
        pts.append(lin(sl, -3, 3))
        det.append(f"SMA200 20일 기울기 {sl:+.2f}%")
    pdi, mdi, adx_ = _g(r, "plus_di"), _g(r, "minus_di"), _g(r, "adx")
    if pdi is not None and mdi is not None and adx_ is not None:
        dirn = 1 if pdi > mdi else -1
        pts.append(50 + dirn * lin(adx_, 10, 40, 0, 50))
        det.append(f"ADX {adx_:.0f} ({'+DI 우위·상승 추세' if dirn > 0 else '-DI 우위·하락 추세'})")
    return Component("trend", "추세", float(np.mean(pts)) if pts else 50.0, det)


def momentum_component(ind: pd.DataFrame) -> Component:
    r = ind.iloc[-1]
    det, pts = [], []
    for k, lo, hi in (("mom20", -0.10, 0.10), ("mom60", -0.20, 0.20), ("mom120", -0.30, 0.30)):
        v = _g(r, k)
        if v is not None:
            pts.append(lin(v, lo, hi))
            det.append(f"{k[3:]}일 수익률 {v:+.1%}")
    h = ind["macd_hist"].dropna()
    if len(h) > 5:
        pts.append(75 if h.iloc[-1] > 0 and h.iloc[-1] > h.iloc[-4] else 60 if h.iloc[-1] > 0 else
                   25 if h.iloc[-1] < h.iloc[-4] else 40)
        det.append(f"MACD 히스토그램 {h.iloc[-1]:+.2f} ({'상승 중' if h.iloc[-1] > h.iloc[-4] else '하락 중'})")
    return Component("momentum", "모멘텀", float(np.mean(pts)) if pts else 50.0, det)


def rsi_component(ind: pd.DataFrame, rsi_info: dict, market: str = "US", style: str = "evidence") -> Component:
    v = rsi_info.get("value")
    if v is None:
        return Component("rsi", "RSI", 50.0, ["RSI 데이터 없음"])
    above200 = bool(pd.notna(ind["sma200"].iloc[-1]) and ind["close"].iloc[-1] > ind["sma200"].iloc[-1])
    # 'evidence': mapping follows the measured forward returns (timing_evidence): in Korea RSI ≥ 70 lagged and
    # RSI ≤ 30 below the 200-day line led; in the US neither extreme hurt. 'user': overbought = late, oversold = early.
    if style == "user":
        s = 15 if v >= 80 else 25 if v >= 70 else 45 if v >= 60 else 55 if v >= 40 else 68 if v >= 30 else 80 if v > 20 else 90
    elif market == "KR":
        s = 40 if v >= 70 else 48 if v >= 60 else 52 if v >= 40 else 54 if v >= 30 else (58 if not above200 else 45)
    else:
        s = 56 if v >= 70 else 55 if v >= 50 else 50 if v >= 30 else (54 if not above200 else 48)
    mom = rsi_info.get("momentum_5d")
    if mom is not None:
        s += float(np.clip(mom, -10, 10)) * 0.3
    for dv in rsi_info.get("divergences", []):
        s += 5 if dv["direction"] == "bullish" else -5
    det = [f"RSI {v:.0f} ({ZONE_KO.get(rsi_info.get('zone'), rsi_info.get('zone'))})"]
    if mom is not None:
        det.append(f"RSI 5일 변화 {mom:+.1f}")
    det += [d["type"] for d in rsi_info.get("divergences", [])]
    return Component("rsi", "RSI", float(np.clip(s, 0, 100)), det)


def timing_signals(ind: pd.DataFrame, ma: dict, rsi_info: dict, market: str = "US") -> list[tuple[float, str]]:
    """Signed points for entry timing from the measured rule table of this market (+ = has led, − = has lagged)."""
    r = ind.iloc[-1]
    c = float(r["close"])
    rules = TIMING_RULES.get(market, TIMING_RULES["US"])
    fired: list[str] = []
    s200, s20, hi52 = _g(r, "sma200"), _g(r, "sma20"), _g(r, "hi52")
    v = rsi_info.get("value")
    if s20 and (c / s20 - 1) * 100 > 8:
        fired.append("ext20_8")
    if v is not None and v >= 70:
        fired.append("rsi_70")
    if v is not None and v <= 30 and s200 and c < s200:
        fired.append("rsi_30_below200")
    pctb = _g(r, "bb_pctb")
    if pctb is not None:
        fired.append("bb_above" if pctb > 1 else "bb_below" if pctb < 0 else "")
    if hi52:
        gap = (c / hi52 - 1) * 100
        fired.append("hi52_0_2" if gap >= -2 else "hi52_2_5" if gap >= -5 else "hi52_5_8" if gap >= -8 else
                     "hi52_8_15" if gap >= -15 else "hi52_15_30" if gap >= -30 else "hi52_30_plus")
    cc = ind["close"]
    if len(cc) > 252 and pd.notna(cc.iloc[-252]) and pd.notna(cc.iloc[-22]):
        m121 = cc.iloc[-22] / cc.iloc[-252] - 1
        fired.append("mom12_40" if m121 >= 0.40 else "mom12_30" if m121 >= 0.30 else "")
    out = []
    for k in fired:
        rule = rules.get(k)
        if rule and rule.points:
            out.append((rule.points, f"{rule.label} - 과거 {rule.evidence}"))
    return out


def style_timing_signals(ind: pd.DataFrame, ma: dict, rsi_info: dict, market: str, style: str, sr: dict | None = None,
                         periods: dict | None = None) -> list[tuple[float, str]]:
    if style == "user":
        return user_rules.signals(ind, sr, periods)
    return timing_signals(ind, ma, rsi_info, market)


def timing_component(ind: pd.DataFrame, ma: dict, rsi_info: dict, market: str = "US", style: str = "evidence",
                     sr: dict | None = None, periods: dict | None = None) -> Component:
    sig = style_timing_signals(ind, ma, rsi_info, market, style, sr, periods)
    s = 50 + sum(p for p, _ in sig)
    if style == "user":
        empty = "과매수 · 과매도 · 고점 · 골든/데드크로스 신호 없음 (중립)"
    else:
        empty = f"{'한국' if market == 'KR' else '미국'} 데이터에서 검증된 타이밍 신호 없음 (중립)"
    det = [f"{t} ({p:+.0f})" for p, t in sorted(sig, key=lambda x: -abs(x[0]))] or [empty]
    return Component("timing", "진입 타이밍", float(np.clip(s, 0, 100)), det)


def volume_component(ind: pd.DataFrame) -> Component:
    d = ind.iloc[-20:]
    det, pts = [], []
    up = d.loc[d["close"] >= d["close"].shift(1), "volume"].sum()
    dn = d.loc[d["close"] < d["close"].shift(1), "volume"].sum()
    if up + dn > 0:
        ratio = up / max(dn, 1)
        pts.append(lin(np.log(ratio), np.log(0.5), np.log(2)))
        det.append(f"상승일/하락일 거래량 비율(20일) {ratio:.2f}")
    obv_ = ind["obv"]
    if len(obv_) > 21:
        slope = (obv_.iloc[-1] - obv_.iloc[-21]) / (ind["volume"].iloc[-60:].mean() * 20 + 1e-9)
        pts.append(lin(slope, -0.5, 0.5))
        det.append(f"OBV 20일 추세 {'상승' if slope > 0 else '하락'}")
    vr = _g(ind.iloc[-1], "vol_ratio")
    ret = _g(ind.iloc[-1], "ret1")
    if vr is not None and ret is not None:
        pts.append(50 + np.sign(ret) * lin(vr, 1.0, 2.5, 0, 30))
        det.append(f"오늘 거래량 20일 평균의 {vr:.1f}배 (등락 {ret:+.1%})")
    return Component("volume", "거래량", float(np.mean(pts)) if pts else 50.0, det)


def breakout_component(ind: pd.DataFrame, patterns: list[dict], market: str = "US", style: str = "evidence") -> Component:
    r = ind.iloc[-1]
    det, pts = [], []
    c = float(r["close"])
    hi52 = _g(r, "hi52")
    if hi52:
        det.append(f"52주 고점 대비 {(c / hi52 - 1) * 100:+.1f}%")
    if style == "user":  # new highs are not rewarded (the timing rules mark them down instead)
        return Component("breakout", "돌파", 50.0, det + ["내 규칙: 고점 돌파는 가산하지 않음"])
    # A confirmed breakout (closed through the level on volume) counts as a plus.
    recent = [p for p in patterns if p["pattern"] in ("Breakout", "Breakdown") and p["bars_ago"] <= 10]
    for p in recent:
        pts.append(80 if p["direction"] == "bullish" else 15)
        det.append(f"{pko(p['pattern'])} {p['bars_ago']}일 전 (거래량 {p['levels'].get('volume_ratio', 0):.1f}배)")
    hi55 = _g(r, "hi55_prev")
    if hi55 and market == "KR" and not any(p["direction"] == "bullish" for p in recent):
        dist = (c / hi55 - 1) * 100
        if -3 <= dist <= 0:  # Korea: names near their highs have tended to lead (timing_evidence)
            pts.append(60)
            det.append(f"55일 고점까지 {abs(dist):.1f}% - 한국 데이터상 고점 근처 종목이 이후 강했음")
    return Component("breakout", "돌파", float(np.mean(pts)) if pts else 50.0, det)


def sr_component(sr: dict) -> Component:
    det = []
    sup, res = sr["nearest_support_pct"], sr["nearest_resistance_pct"]
    rr = sr.get("reward_risk")
    s = lin(np.log(rr) if rr else 0, np.log(0.4), np.log(2.5)) if rr else 50
    if abs(sup) < 2:
        s += 8
        det.append(f"지지선 위 {abs(sup):.1f}% - 손절 기준이 가까움")
    if res < 2 and not sr["resistances"][0].get("projected"):
        s -= 8
        det.append(f"저항선까지 {res:.1f}%")
    det.insert(0, f"지지선 {sup:.1f}% / 저항선 +{res:.1f}% (손익비 {rr:.2f})" if rr else "지지/저항 불명확")
    return Component("support_resistance", "지지/저항", float(np.clip(s, 0, 100)), det)


def volatility_component(ind: pd.DataFrame) -> Component:
    a = ind["atr_pct"].dropna()
    if len(a) < 60:
        return Component("volatility", "변동성", 50.0, ["이력 부족"])
    cur = float(a.iloc[-1])
    pct = float((a.iloc[-252:] < cur).mean())
    s = lin(pct, 0.95, 0.2, 20, 70)  # calmer than usual -> higher score
    bw = ind["bb_width"].dropna()
    squeeze = len(bw) > 120 and bw.iloc[-1] <= bw.iloc[-120:].quantile(0.15)
    det = [f"ATR 가격의 {cur:.1f}% (1년 중 하위 {pct:.0%} 수준)"]
    if squeeze:
        s += 5
        det.append("볼린저 밴드 수축 (변동성 축소)")
    return Component("volatility", "변동성", float(np.clip(s, 0, 100)), det)


def pattern_component(patterns: list[dict]) -> Component:
    if not patterns:
        return Component("pattern", "패턴", 50.0, ["진행 중인 차트 패턴 없음"])
    s, det = 50.0, []
    for p in patterns:
        if p["direction"] == "neutral":
            det.append(f"{pko(p['pattern'])} ({STATUS_KO.get(p['status'], p['status'])})")
            continue
        hist = p.get("historical") or {}
        hr = hist.get("success_rate") if hist.get("sufficient") else None
        edge = (hr - 0.5) * 2 if hr is not None else 0.2
        w = p["confidence"] * (1.0 if p["status"] == "confirmed" else 0.5) * (0.6 + max(-0.4, min(0.4, edge)))
        s += (25 if p["direction"] == "bullish" else -25) * w
        hs = f", 과거 성공률 {hr:.0%} (n={hist['n']})" if hr is not None else ""
        det.append(f"{pko(p['pattern'])} {STATUS_KO.get(p['status'], p['status'])} {p['bars_ago']}일 전, 신뢰도 {p['confidence']:.0%}{hs}")
    return Component("pattern", "패턴", float(np.clip(s, 0, 100)), det)


# --------------------------------------------------------------------- horizons
def horizon_scores(ind: pd.DataFrame, comps: dict[str, Component], fundamental: float | None, market: str = "US",
                   style: str = "evidence") -> dict:
    r = ind.iloc[-1]
    c = float(r["close"])
    sub: dict[str, Component] = {}

    s5, s20 = _g(r, "sma5"), _g(r, "sma20")
    ts = [lin((c / s20 - 1) * 100, -6, 6)] if s20 else []
    if s5 and s20:
        ts.append(70 if s5 > s20 else 30)
    sub["trend_short"] = Component("trend_short", "단기 추세 (5/20일선)", float(np.mean(ts)) if ts else 50,
                                   [f"주가가 SMA20 대비 {(c / s20 - 1) * 100:+.1f}%" if s20 else "SMA20 없음",
                                    (f"SMA5 {'>' if s5 > s20 else '<'} SMA20" if s5 and s20 else "")])
    m5, m20 = _g(r, "mom5"), _g(r, "mom20")
    sub["momentum_short"] = Component("momentum_short", "단기 모멘텀",
                                      float(np.mean([lin(m5, -0.06, 0.06), lin(m20, -0.10, 0.10)])),
                                      [f"5일 {m5:+.1%}" if m5 is not None else "", f"20일 {m20:+.1%}" if m20 is not None else ""])
    h = ind["macd_hist"].dropna()
    mac = 50.0
    if len(h) > 5:
        mac = 70 if h.iloc[-1] > 0 else 30
        mac += 10 if h.iloc[-1] > h.iloc[-5] else -10
    sub["macd"] = Component("macd", "MACD", mac, [f"MACD 히스토그램 {h.iloc[-1]:+.2f} ({'상승' if len(h) > 5 and h.iloc[-1] > h.iloc[-5] else '하락'} 중)" if len(h) else "데이터 없음"])
    adx_, pdi, mdi = _g(r, "adx"), _g(r, "plus_di"), _g(r, "minus_di")
    sub["adx"] = Component("adx", "추세 강도 (ADX)",
                           50 + (1 if (pdi or 0) > (mdi or 0) else -1) * lin(adx_, 15, 40, 0, 40) if adx_ else 50,
                           [f"ADX {adx_:.0f} ({'+DI' if (pdi or 0) > (mdi or 0) else '-DI'} 우위)" if adx_ else "데이터 없음"])
    s200 = _g(r, "sma200")
    tl = []
    if s200:
        tl.append(lin((c / s200 - 1) * 100, -20, 20))
        sl = ind["sma200"].dropna()
        if len(sl) > 61:
            tl.append(lin((sl.iloc[-1] / sl.iloc[-61] - 1) * 100, -6, 6))
    sub["trend_long"] = Component("trend_long", "장기 추세 (200일선)", float(np.mean(tl)) if tl else 50,
                                  [f"주가가 SMA200 대비 {(c / s200 - 1) * 100:+.1f}%" if s200 else "SMA200 없음"])
    m120, m250 = _g(r, "mom120"), _g(r, "mom250")
    ml = [x for x in (lin(m120, -0.3, 0.3) if m120 is not None else None,
                      lin(m250, -0.4, 0.4) if m250 is not None else None) if x is not None]
    sub["momentum_long"] = Component("momentum_long", "장기 모멘텀 (6~12개월)", float(np.mean(ml)) if ml else 50,
                                     [f"6개월 {m120:+.1%}" if m120 is not None else "", f"12개월 {m250:+.1%}" if m250 is not None else ""])
    hi52 = _g(r, "hi52")
    dd = (c / hi52 - 1) if hi52 else None
    # Measured (timing_evidence): KR - within 30% of the high led, >30% below lagged;
    # US - the distance to the high made little difference, deep drawdowns slightly led.
    if dd is None:
        dds, note = 50.0, "데이터 없음"
    elif style == "user":
        dds = 30.0 if dd >= -0.05 else 45.0 if dd >= -0.15 else 60.0 if dd >= -0.30 else 55.0
        note = ("고점권 - 내 규칙상 감점" if dd >= -0.05 else "고점 근처" if dd >= -0.15 else "조정 후 - 가격 부담 덜함"
                if dd >= -0.30 else "고점 대비 30% 넘게 하락")
    elif market == "KR":
        dds = 70.0 if dd >= -0.15 else 60.0 if dd >= -0.30 else 30.0
        note = "고점 근처 · 강한 종목" if dd >= -0.15 else "중간 조정" if dd >= -0.30 else "고점 대비 30% 넘게 하락 - 약한 종목"
    else:
        dds = 55.0 if dd >= -0.30 else 60.0
        note = "고점과의 거리는 미국 데이터상 영향 작음" if dd >= -0.30 else "낙폭 과대 - 미국 데이터상 이후 소폭 반등"
    sub["drawdown"] = Component("drawdown", "52주 고점 대비 위치", dds,
                                [f"52주 고점 대비 {dd:+.1%} - {note}" if dd is not None else note])
    sub["fundamental"] = Component("fundamental", "펀더멘털", fundamental if fundamental is not None else 50,
                                   [f"펀더멘털 점수 {fundamental:.0f}" if fundamental is not None else "펀더멘털 데이터 없음 (중립 처리)"])
    pool = {**comps, **sub}
    out = {}
    for hz, weights in (HORIZON_WEIGHTS_USER if style == "user" else HORIZON_WEIGHTS).items():
        w = dict(weights)
        if hz == "long" and fundamental is None:
            w.pop("fundamental")
        tot = sum(w.values())
        w = {k: v / tot for k, v in w.items()}
        score = 50 + sum(w[k] * (pool[k].score - 50) for k in w)
        out[hz] = {"score": round(score, 1), "view": view_label(score), "band": band(score),
                   "components": [pool[k].to_dict(round(w[k], 3)) for k in w]}
    out["short"]["range"] = "1~20 거래일"
    out["medium"]["range"] = "20~120 거래일"
    out["long"]["range"] = "120 거래일 이상"
    return out


def view_label(score: float) -> str:
    return "Bullish" if score >= 60 else "Bearish" if score < 42 else "Neutral"


# --------------------------------------------------------------------- signal
def regime_adjust(weights: dict[str, float], regime: str | None) -> tuple[dict[str, float], dict[str, float], str | None]:
    """Risk-On: momentum/breakout weighted up. Risk-Off: stricter BUY thresholds, trend/S-R up."""
    w = dict(weights)
    th = dict(SIGNAL_THRESHOLDS)
    note = None
    if regime == "Risk-On":
        w["momentum"] *= 1.25
        w["breakout"] *= 1.25
        note = "Risk-On(위험선호) 국면: 모멘텀·돌파 가중치 ×1.25"
    elif regime == "Risk-Off":
        w["trend"] *= 1.2
        w["support_resistance"] *= 1.2
        w["breakout"] *= 0.8
        th["buy"] += 6
        th["strong_buy"] += 6
        note = "Risk-Off(위험회피) 국면: 매수 기준 +6점, 돌파 가중치 ×0.8"
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()}, th, note


def _is_default(weights: dict | None) -> bool:
    return not weights or all(abs(float(weights.get(k, v)) - v) < 1e-6 for k, v in DEFAULT_WEIGHTS.items())


def technical_signal(ind: pd.DataFrame, *, ma: dict, rsi_info: dict, sr: dict, patterns: list[dict],
                     regime: str | None = None, weights: dict[str, float] | None = None,
                     fundamental: float | None = None, market: str = "US", style: str | None = None,
                     periods: dict | None = None) -> dict:
    style = style or current_style()
    comps = {
        "trend": trend_component(ind),
        "momentum": momentum_component(ind),
        "rsi": rsi_component(ind, rsi_info, market, style),
        "volume": volume_component(ind),
        "breakout": breakout_component(ind, patterns, market, style),
        "support_resistance": sr_component(sr),
        "volatility": volatility_component(ind),
        "pattern": pattern_component(patterns),
        "timing": timing_component(ind, ma, rsi_info, market, style, sr, periods),
    }
    # In 'user' style the untouched default weights switch to the timing-heavy set; custom weights are kept.
    defaults = DEFAULT_WEIGHTS_USER if style == "user" and _is_default(weights) else DEFAULT_WEIGHTS
    base_w = {**defaults, **({} if style == "user" and _is_default(weights) else (weights or {}))}
    w, th, regime_note = regime_adjust(base_w, regime)
    score = 50 + sum(w[k] * (comps[k].score - 50) for k in comps)
    score = float(np.clip(score, 0, 100))

    r = ind.iloc[-1]
    c = float(r["close"])
    s200 = _g(r, "sma200")
    gates = []
    label = ("STRONG BUY" if score >= th["strong_buy"] else "BUY" if score >= th["buy"] else
             "STRONG SELL" if score <= th["strong_sell"] else "SELL" if score <= th["sell"] else "NEUTRAL")
    tm = comps["timing"].score
    # Timing gate: when the measured timing rules all point down (e.g. Korea: far above the
    # 20-day line + RSI ≥ 70 + above the upper band), it is not a buy however strong the trend.
    if label in ("BUY", "STRONG BUY") and tm <= 25:
        gates.append(f"진입 타이밍 {tm:.0f} ≤ 25: 과거 데이터상 이후 성과가 약했던 자리 → 매수 신호 보류")
        label = "NEUTRAL"
    elif label == "STRONG BUY" and tm <= 40:
        gates.append(f"진입 타이밍 {tm:.0f} ≤ 40: 강력 매수 → 매수로 하향")
        label = "BUY"
    # Trend gate: no bullish label against a weak trend - unless a clear turn / golden cross is under way.
    if label in ("BUY", "STRONG BUY") and comps["trend"].score < 45:
        if tm >= 75:
            gates.append(f"추세 점수 {comps['trend'].score:.0f} < 45 이지만 진입 타이밍 {tm:.0f} (검증된 타이밍 신호 우세) → 매수 유지")
            label = "BUY"
        else:
            gates.append(f"추세 점수 {comps['trend'].score:.0f} < 45: 매수 신호 보류")
            label = "NEUTRAL"
    if label == "STRONG BUY" and s200 and c < s200:
        gates.append("주가가 SMA200 아래: 강력 매수 → 매수로 하향")
        label = "BUY"
    if label in ("SELL", "STRONG SELL") and comps["trend"].score > 60:
        gates.append(f"추세 점수 {comps['trend'].score:.0f} > 60: 매도 신호 완화")
        label = "NEUTRAL"

    bull, bear = _factors(ind, ma, rsi_info, sr, patterns, regime, market, style, periods)
    return {
        "score": round(score, 1), "signal": label, "band": band(score),
        "components": [comps[k].to_dict(round(w[k], 3)) for k in comps],
        "weights": {k: round(v, 3) for k, v in w.items()}, "thresholds": th,
        "regime": regime, "regime_note": regime_note, "gates": gates,
        "bullish_factors": bull, "bearish_factors": bear,
        "horizons": horizon_scores(ind, comps, fundamental, market, style),
        "market": market, "style": style,
        "formula": "점수 = 50 + Σ 가중치 × (구성요소 점수 − 50) · 구성요소 점수는 0~100, 50 = 중립",
    }


def _factors(ind, ma, rsi_info, sr, patterns, regime, market="US", style="evidence", periods=None) -> tuple[list[str], list[str]]:
    r = ind.iloc[-1]
    c = float(r["close"])
    bull, bear = [], []
    above = [p for p in (20, 60, 120, 200) if _g(r, f"sma{p}") and c > r[f"sma{p}"]]
    below = [p for p in (20, 60, 120, 200) if _g(r, f"sma{p}") and c <= r[f"sma{p}"]]
    if above:
        bull.append(f"주가가 SMA{'/'.join(map(str, above))} 위")
    if below:
        bear.append(f"주가가 SMA{'/'.join(map(str, below))} 아래")
    if "Strong Bullish" in ma["structure"]:
        bull.append(f"{STRUCTURE_KO[ma['structure']]} ({ma['order']})")
    elif "Strong Bearish" in ma["structure"]:
        bear.append(f"{STRUCTURE_KO[ma['structure']]} ({ma['order']})")
    for e in ma["events"]:
        (bull if e["direction"] == "bullish" else bear).append(f"{e['event']} ({e['bars_ago']}일 전)")
    # Entry timing first: the strongest reasons (overbought = late, turn / golden cross = early) lead the lists.
    lead_bull, lead_bear = [], []
    for pts, txt in sorted(style_timing_signals(ind, ma, rsi_info, market, style, sr, periods), key=lambda x: -abs(x[0])):
        target = (lead_bull if pts > 0 else lead_bear) if abs(pts) >= 10 else (bull if pts > 0 else bear)
        target.append(txt)
    v = rsi_info.get("value")
    if v is not None and 45 <= v < 65:
        mom = rsi_info.get("momentum_5d") or 0
        bull.append(f"RSI {v:.0f}, 과열 없이 모멘텀 {'개선 중' if mom >= 0 else '둔화 중'}")
    elif v is not None and 35 < v < 45:
        bear.append(f"RSI {v:.0f} (45 미만) - 모멘텀 약함")
    m20 = _g(r, "mom20")
    if m20 is not None:
        (bull if m20 > 0 else bear).append(f"20일 모멘텀 {m20:+.1%}")
    vr = _g(r, "vol_ratio")
    if vr is not None and vr >= 1.3:
        (bull if (_g(r, "ret1") or 0) > 0 else bear).append(f"거래량 20일 평균의 {vr:.1f}배")
    for p in patterns:
        if p["direction"] == "neutral":
            continue
        hist = p.get("historical") or {}
        hs = f", 과거 성공률 {hist['success_rate']:.0%} (n={hist['n']})" if hist.get("sufficient") else ""
        (bull if p["direction"] == "bullish" else bear).append(f"{pko(p['pattern'])} {STATUS_KO.get(p['status'], p['status'])} (신뢰도 {p['confidence']:.0%}{hs})")
    ns, nr = sr["supports"][0], sr["resistances"][0]
    if abs(ns["distance_pct"]) <= 4:
        bull.append(f"지지선 {ns['price']:,.2f} 위 {abs(ns['distance_pct']):.1f}%에서 거래")
    if nr["distance_pct"] <= 3 and not nr.get("projected"):
        bear.append(f"저항선 {nr['price']:,.2f}까지 {nr['distance_pct']:.1f}%")
    atr_pct = _g(r, "atr_pct")
    a = ind["atr_pct"].dropna()
    if atr_pct and len(a) > 120 and atr_pct > a.iloc[-252:].quantile(0.8):
        bear.append(f"변동성(ATR) 확대 - 가격의 {atr_pct:.1f}%, 최근 1년 상위 20%")
    if regime == "Risk-On":
        bull.append("시장 국면: 위험선호(Risk-On)")
    elif regime == "Risk-Off":
        bear.append("시장 국면: 위험회피(Risk-Off) - 매수 기준 강화")
    return lead_bull + bull, lead_bear + bear
