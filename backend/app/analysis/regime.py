"""Macro regime engine.

Five sub-scores in [-1, +1] are computed from explicit rules on measured data:

  growth     (+ = expanding)          unemployment trend, jobless claims, payrolls, CFNAI, Philly Fed,
                                      industrial production, copper/gold ratio, Sahm rule
  inflation  (+ = rising pressure)    core CPI momentum vs trend, core PCE level, breakevens, oil
  policy     (+ = easing / lower rates) 2Y vs Fed funds (market-implied path), 2Y and 10Y trends, real yields
  liquidity  (+ = improving)          Fed net liquidity (WALCL - TGA - RRP), M2 growth, NFCI, HY spreads
  risk       (+ = risk appetite)      VIX level and term structure, S&P 500 trend, breadth, USD trend

Composite Risk-On score (0-100) =
  50 + 50 * (0.35*risk + 0.20*liquidity + 0.20*policy + 0.15*growth - 0.10*inflation)

Growth x inflation gives the business-cycle quadrant (softmax "probabilities"), and overlay
flags mark rate-cut / rate-hike expectations, liquidity-driven markets and recession warnings.
Every rule emits a sentence with the numbers behind it, so the result is fully explainable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

COMPOSITE_WEIGHTS = {"risk": 0.35, "liquidity": 0.20, "policy": 0.20, "growth": 0.15, "inflation": -0.10}


@dataclass
class Rule:
    category: str
    name: str
    score: float      # -1..1 (sign convention of the category)
    weight: float
    text: str
    value: float | None = None

    def to_dict(self) -> dict:
        return {"category": self.category, "name": self.name, "score": round(self.score, 3), "weight": self.weight,
                "text": self.text, "value": self.value}


def clip1(x: float) -> float:
    return float(max(-1.0, min(1.0, x)))


def scale(x: float, neg: float, pos: float) -> float:
    """Map x linearly so that x=neg -> -1 and x=pos -> +1 (clipped)."""
    if pos == neg:
        return 0.0
    return clip1(-1 + 2 * (x - neg) / (pos - neg))


def _last(s: pd.Series | None) -> tuple[float | None, pd.Timestamp | None]:
    if s is None or s.dropna().empty:
        return None, None
    s = s.dropna()
    return float(s.iloc[-1]), s.index[-1]


def _ago(s: pd.Series, days: int) -> float | None:
    s = s.dropna()
    if s.empty:
        return None
    target = s.index[-1] - pd.Timedelta(days=days)
    prior = s[s.index <= target]
    return float(prior.iloc[-1]) if len(prior) else None


def _obs_ago(s: pd.Series, n: int) -> float | None:
    s = s.dropna()
    return float(s.iloc[-1 - n]) if len(s) > n else None


def yoy(s: pd.Series) -> pd.Series:
    s = s.dropna()
    return (s / s.shift(12) - 1) * 100


def compute_regime(d: dict[str, pd.Series], breadth_pct_above_200: float | None = None) -> dict:
    rules: list[Rule] = []
    g = d.get

    # ------------------------------------------------------------- growth
    if g("UNRATE") is not None:
        u, _ = _last(g("UNRATE"))
        u3 = _obs_ago(g("UNRATE"), 3)
        if u is not None and u3 is not None:
            ch = u - u3
            rules.append(Rule("growth", "Unemployment trend", scale(-ch, -0.4, 0.2), 1.0,
                              f"실업률 {u:.1f}% (3개월간 {ch:+.1f}%p)", u))
    if g("ICSA") is not None:
        s = g("ICSA").dropna()
        if len(s) > 30:
            r = s.iloc[-4:].mean() / s.iloc[-26:].mean()
            rules.append(Rule("growth", "Jobless claims", scale(-(r - 1), -0.12, 0.05), 0.8,
                              f"신규 실업수당 청구 4주 평균 {s.iloc[-4:].mean() / 1000:.0f}천 건, 26주 평균 대비 {(r - 1) * 100:+.1f}%",
                              float(s.iloc[-1])))
    if g("PAYEMS") is not None:
        s = g("PAYEMS").dropna()
        if len(s) > 4:
            avg3 = (s.iloc[-1] - s.iloc[-4]) / 3
            rules.append(Rule("growth", "Payrolls", scale(avg3, -50, 200), 0.8,
                              f"비농업 고용 3개월 평균 {avg3:+.0f}천 명", float(avg3)))
    if g("CFNAI") is not None:
        s = g("CFNAI").dropna()
        if len(s) >= 3:
            ma3 = s.iloc[-3:].mean()
            rules.append(Rule("growth", "Chicago Fed activity", scale(ma3, -0.7, 0.3), 0.8,
                              f"시카고연은 활동지수(CFNAI) 3개월 평균 {ma3:+.2f} (-0.70 미만이면 경기침체 신호)", float(ma3)))
    if g("GACDFSA066MSFRBPHI") is not None:
        v, _ = _last(g("GACDFSA066MSFRBPHI"))
        if v is not None:
            rules.append(Rule("growth", "Philly Fed manufacturing", scale(v, -15, 15), 0.5,
                              f"필라델피아연은 제조업지수 {v:+.1f} (ISM 대체 지표)", v))
    if g("INDPRO") is not None:
        y = yoy(g("INDPRO")).dropna()
        if len(y):
            rules.append(Rule("growth", "Industrial production", scale(y.iloc[-1], -2, 3), 0.5,
                              f"산업생산 전년 대비 {y.iloc[-1]:+.1f}%", float(y.iloc[-1])))
    if g("copper") is not None and g("gold") is not None:
        cg = (g("copper") / g("gold").reindex(g("copper").index).ffill()).dropna()
        if len(cg) > 70:
            ch = (cg.iloc[-1] / cg.iloc[-61] - 1) * 100
            rules.append(Rule("growth", "Copper/gold ratio", scale(ch, -10, 10), 0.6,
                              f"구리/금 비율 60거래일 {ch:+.1f}% (시장의 성장 기대 지표)", float(ch)))
    if g("SAHMREALTIME") is not None:
        v, _ = _last(g("SAHMREALTIME"))
        if v is not None:
            rules.append(Rule("growth", "Sahm rule", -1.0 if v >= 0.5 else scale(-v, -0.5, 0.0) * 0.5, 0.7,
                              f"샴의 법칙 지표 {v:.2f} ({'발동' if v >= 0.5 else '미발동'}; 0.50 이상 = 경기침체 신호)", v))

    # ---------------------------------------------------------- inflation
    if g("CPILFESL") is not None:
        s = g("CPILFESL").dropna()
        if len(s) > 13:
            y12 = (s.iloc[-1] / s.iloc[-13] - 1) * 100
            a3 = ((s.iloc[-1] / s.iloc[-4]) ** 4 - 1) * 100
            rules.append(Rule("inflation", "Core CPI momentum", scale(a3 - y12, -1.0, 1.0) * 0.5 + scale(y12, 2.0, 4.0) * 0.5,
                              1.0, f"근원 CPI 전년 대비 {y12:.1f}%, 3개월 연율 {a3:.1f}%", float(y12)))
    if g("PCEPILFE") is not None:
        y = yoy(g("PCEPILFE")).dropna()
        if len(y):
            rules.append(Rule("inflation", "Core PCE", scale(y.iloc[-1], 2.0, 3.5), 0.8,
                              f"근원 PCE 전년 대비 {y.iloc[-1]:.1f}% (연준 목표 2%)", float(y.iloc[-1])))
    if g("T10YIE") is not None:
        v, _ = _last(g("T10YIE"))
        v60 = _ago(g("T10YIE"), 90)
        if v is not None and v60 is not None:
            rules.append(Rule("inflation", "Breakeven inflation", scale(v - v60, -0.3, 0.3) * 0.6 + scale(v, 2.0, 2.7) * 0.4,
                              0.6, f"10년 기대인플레이션(BEI) {v:.2f}% (3개월간 {(v - v60) * 100:+.0f}bp)", v))
    if g("oil") is not None:
        s = g("oil").dropna()
        if len(s) > 65:
            ch = (s.iloc[-1] / s.iloc[-61] - 1) * 100
            rules.append(Rule("inflation", "Oil", scale(ch, -20, 20), 0.6,
                              f"WTI 유가 ${s.iloc[-1]:.1f} (60거래일 {ch:+.1f}%)", float(s.iloc[-1])))

    # ------------------------------------------------------------- policy
    ff, _ = _last(g("DFF"))
    t2, _ = _last(g("DGS2"))
    if ff is not None and t2 is not None:
        gap = t2 - ff
        rules.append(Rule("policy", "2Y vs Fed funds", scale(-gap, -0.5, 0.75), 1.0,
                          f"미 2년물 금리 {t2:.2f}% vs 기준금리 {ff:.2f}% ({gap * 100:+.0f}bp: 시장은 "
                          f"{'금리 인하를' if gap < -0.15 else '금리 인상을' if gap > 0.15 else '동결을'} 반영)", gap))
    if g("DGS2") is not None:
        v60 = _ago(g("DGS2"), 90)
        if t2 is not None and v60 is not None:
            rules.append(Rule("policy", "2Y trend", scale(-(t2 - v60), -0.5, 0.5), 0.7,
                              f"미 2년물 금리 3개월간 {(t2 - v60) * 100:+.0f}bp", t2 - v60))
    if g("DGS10") is not None:
        t10, _ = _last(g("DGS10"))
        t10_20 = _obs_ago(g("DGS10"), 20)
        if t10 is not None and t10_20 is not None:
            rules.append(Rule("policy", "10Y trend", scale(-(t10 - t10_20), -0.35, 0.35), 1.0,
                              f"미 10년물 금리 20거래일간 {abs(t10 - t10_20) * 100:.0f}bp {'상승' if t10 > t10_20 else '하락'} "
                              f"({t10_20:.2f}% → {t10:.2f}%)", t10))
    if g("DFII10") is not None:
        v, _ = _last(g("DFII10"))
        v60 = _ago(g("DFII10"), 90)
        if v is not None and v60 is not None:
            rules.append(Rule("policy", "Real yields", scale(-(v - v60), -0.4, 0.4) * 0.6 + scale(-v, -2.5, -0.5) * 0.4, 0.7,
                              f"10년 실질금리 {v:.2f}% (3개월간 {(v - v60) * 100:+.0f}bp)", v))
    if g("DFF") is not None:
        f90 = _ago(g("DFF"), 92)
        if ff is not None and f90 is not None and abs(ff - f90) >= 0.1:
            rules.append(Rule("policy", "Fed actions", scale(-(ff - f90), -0.5, 0.5), 0.6,
                              f"기준금리 3개월간 {abs(ff - f90) * 100:.0f}bp {'인하' if ff < f90 else '인상'}", ff - f90))

    # ---------------------------------------------------------- liquidity
    if g("WALCL") is not None and g("WTREGEN") is not None and g("RRPONTSYD") is not None:
        w = g("WALCL").dropna()
        tga = g("WTREGEN").reindex(w.index, method="ffill")
        rrp = g("RRPONTSYD").reindex(w.index, method="ffill").fillna(0)
        nl = (w - tga - rrp * 1000) / 1e6  # $ trillions
        nl = nl.dropna()
        if len(nl) > 14:
            ch = nl.iloc[-1] - nl.iloc[-14]
            rules.append(Rule("liquidity", "Fed net liquidity", scale(ch, -0.3, 0.3), 1.0,
                              f"연준 순유동성 ${nl.iloc[-1]:.2f}조 (13주간 {ch * 1000:+.0f}십억 달러)", float(nl.iloc[-1])))
    if g("M2SL") is not None:
        y = yoy(g("M2SL")).dropna()
        if len(y):
            rules.append(Rule("liquidity", "M2 growth", scale(y.iloc[-1], -1, 6), 0.6,
                              f"M2 통화량 전년 대비 {y.iloc[-1]:+.1f}%", float(y.iloc[-1])))
    if g("NFCI") is not None:
        v, _ = _last(g("NFCI"))
        if v is not None:
            rules.append(Rule("liquidity", "Financial conditions", scale(-v, -0.3, 0.6), 0.8,
                              f"시카고연은 금융여건지수(NFCI) {v:+.2f} (평균보다 {'완화적' if v < 0 else '긴축적'})", v))
    if g("BAMLH0A0HYM2") is not None:
        v, _ = _last(g("BAMLH0A0HYM2"))
        v60 = _ago(g("BAMLH0A0HYM2"), 90)
        if v is not None and v60 is not None:
            rules.append(Rule("liquidity", "Credit spreads", scale(-(v - v60), -0.75, 0.75) * 0.5 + scale(-v, -6, -3) * 0.5, 0.9,
                              f"하이일드 스프레드 {v:.2f}% (3개월간 {(v - v60) * 100:+.0f}bp)", v))

    # --------------------------------------------------------------- risk
    if g("vix") is not None:
        s = g("vix").dropna()
        if len(s) > 60:
            v = float(s.iloc[-1])
            med = float(s.iloc[-252:].median())
            rules.append(Rule("risk", "VIX level", scale(-v, -28, -13) * 0.6 + scale(-(v / med - 1), -0.4, 0.2) * 0.4, 1.0,
                              f"VIX {v:.1f} (1년 중앙값 {med:.1f})", v))
            if g("vix3m") is not None and not g("vix3m").dropna().empty:
                v3 = float(g("vix3m").dropna().iloc[-1])
                ratio = v / v3
                rules.append(Rule("risk", "VIX term structure", scale(-ratio, -1.05, -0.85), 0.6,
                                  f"VIX/VIX3M {ratio:.2f} ({'역전(백워데이션) - 단기 불안' if ratio > 1 else '정상(콘탱고) - 안정'})", ratio))
    if g("spx") is not None:
        s = g("spx").dropna()
        if len(s) > 210:
            sma200 = s.iloc[-200:].mean()
            d200 = (s.iloc[-1] / sma200 - 1) * 100
            m20 = (s.iloc[-1] / s.iloc[-21] - 1) * 100
            rules.append(Rule("risk", "S&P 500 trend", scale(d200, -8, 8) * 0.6 + scale(m20, -5, 5) * 0.4, 1.0,
                              f"S&P 500 SMA200 대비 {d200:+.1f}%, 20일 {m20:+.1f}%", d200))
    if breadth_pct_above_200 is not None:
        rules.append(Rule("risk", "Breadth", scale(breadth_pct_above_200, 30, 70), 0.7,
                          f"S&P 500 종목 중 {breadth_pct_above_200:.0f}%가 200일선 위", breadth_pct_above_200))
    if g("dxy") is not None:
        s = g("dxy").dropna()
        if len(s) > 65:
            ch = (s.iloc[-1] / s.iloc[-61] - 1) * 100
            rules.append(Rule("risk", "US dollar", scale(-ch, -4, 4), 0.5,
                              f"달러인덱스 {s.iloc[-1]:.1f} (60일 {ch:+.1f}%; 달러 강세는 글로벌 유동성 긴축 요인)", ch))

    # --------------------------------------------------------- aggregate
    subs: dict[str, float] = {}
    for cat in ("growth", "inflation", "policy", "liquidity", "risk"):
        rs = [r for r in rules if r.category == cat]
        tw = sum(r.weight for r in rs)
        subs[cat] = sum(r.score * r.weight for r in rs) / tw if tw else 0.0
    composite = 50 + 50 * sum(COMPOSITE_WEIGHTS[k] * subs[k] for k in COMPOSITE_WEIGHTS)
    composite = float(np.clip(composite, 0, 100))
    label = "Risk-On" if composite >= 60 else "Risk-Off" if composite <= 40 else "Neutral"

    gs, inf = subs["growth"], subs["inflation"]
    fits = {"Inflationary Growth": gs + inf, "Disinflationary Growth": gs - inf,
            "Stagflation": -gs + inf, "Recessionary": -gs - inf}
    ex = {k: math.exp(v / 0.35) for k, v in fits.items()}
    tot = sum(ex.values())
    quadrant_probs = {k: v / tot for k, v in ex.items()}
    quadrant = max(quadrant_probs, key=quadrant_probs.get)

    overlays = []
    gap = next((r.value for r in rules if r.name == "2Y vs Fed funds"), None)
    t2ch = next((r.value for r in rules if r.name == "2Y trend"), None)
    if (gap is not None and gap < -0.25) or (t2ch is not None and t2ch < -0.30):
        overlays.append({"name": "Rate-Cut Expectation",
                         "reason": f"2년물 금리가 기준금리 대비 {gap * 100:+.0f}bp" if gap is not None else "2년물 금리 하락"})
    if (gap is not None and gap > 0.25) or (t2ch is not None and t2ch > 0.30):
        overlays.append({"name": "Rate-Hike Expectation",
                         "reason": (f"2년물 금리가 기준금리보다 {gap * 100:+.0f}bp 높음" if gap is not None and gap > 0.25
                                    else f"2년물 금리 3개월간 +{t2ch * 100:.0f}bp")})
    if subs["liquidity"] > 0.3 and subs["growth"] < 0.1:
        overlays.append({"name": "Liquidity-Driven", "reason": "성장 모멘텀은 약한데 유동성이 개선되는 장세"})
    sahm = next((r.value for r in rules if r.name == "Sahm rule"), None)
    cf = next((r.value for r in rules if r.name == "Chicago Fed activity"), None)
    if (sahm is not None and sahm >= 0.5) or (cf is not None and cf < -0.7):
        overlays.append({"name": "Recession Warning", "reason": "샴의 법칙 발동 또는 CFNAI < -0.70"})

    contrib = []
    for r in rules:
        w = COMPOSITE_WEIGHTS[r.category]
        cat_tw = sum(x.weight for x in rules if x.category == r.category)
        pts = 50 * w * r.score * r.weight / cat_tw if cat_tw else 0
        contrib.append((pts, r))
    contrib.sort(key=lambda x: -abs(x[0]))
    reasons = [{"text": r.text, "category": r.category, "points": round(p, 1),
                "direction": "supportive" if p > 0 else "headwind"} for p, r in contrib if abs(p) >= 0.3]
    return {
        "label": label, "score": round(composite, 1), "quadrant": quadrant,
        "quadrant_probabilities": {k: round(v, 3) for k, v in quadrant_probs.items()},
        "overlays": overlays,
        "subscores": {k: round(50 + 50 * v, 1) for k, v in subs.items()},
        "subscores_raw": {k: round(v, 3) for k, v in subs.items()},
        "reasons": reasons[:12],
        "rules": [r.to_dict() for r in rules],
        "formula": "Risk-On 점수 = 50 + 50×(0.35×위험선호 + 0.20×유동성 + 0.20×정책·금리 + 0.15×성장 − 0.10×인플레이션)",
        "notes": ["ISM 제조업/서비스업 PMI는 유료 데이터라 FRED에 없으므로 필라델피아연은 지수와 CFNAI로 대체합니다."],
    }


# ---------------------------------------------------------------- factor trends
def factor_trends(d: dict[str, pd.Series], subs_raw: dict[str, float]) -> dict[str, dict]:
    """Current direction of macro factors in [-1, 1] (+ = the named move is happening)."""
    out = {}

    def pct(s, n):
        s = s.dropna()
        return (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100 if len(s) > n else None

    t10 = d.get("DGS10")
    if t10 is not None and len(t10.dropna()) > 21:
        s = t10.dropna()
        ch = (s.iloc[-1] - s.iloc[-21]) * 100
        out["rates_down"] = {"value": clip1(-ch / 30), "text": f"10년물 20일 {ch:+.0f}bp"}
    curve = d.get("T10Y2Y")
    if curve is not None and len(curve.dropna()) > 61:
        s = curve.dropna()
        ch = (s.iloc[-1] - s.iloc[-61]) * 100
        out["curve_steepening"] = {"value": clip1(ch / 30), "text": f"장단기 금리차 {s.iloc[-1] * 100:+.0f}bp (60일 {ch:+.0f}bp)"}
    if d.get("dxy") is not None:
        ch = pct(d["dxy"], 60)
        if ch is not None:
            out["usd_down"] = {"value": clip1(-ch / 4), "text": f"달러인덱스 60일 {ch:+.1f}%"}
    if d.get("oil") is not None:
        ch = pct(d["oil"], 60)
        if ch is not None:
            out["oil_up"] = {"value": clip1(ch / 20), "text": f"WTI 60일 {ch:+.1f}%"}
    for k, label in (("growth", "성장"), ("inflation", "인플레이션"), ("liquidity", "유동성"), ("risk", "위험선호")):
        if k in subs_raw:
            out[k] = {"value": subs_raw[k], "text": f"{label} 점수 {50 + 50 * subs_raw[k]:.0f}/100"}
    return out
