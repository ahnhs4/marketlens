"""Macro opportunity scoring for sectors, industries, countries, assets and single stocks.

Sector/asset score (0-100):
    50 + 35 * sum_j(e_j * f_j) / sum_j |e_j|   (macro alignment, economic priors)
       + 15 * RS                                (market confirmation: 3M return vs S&P 500, in [-1, 1])
where e_j is the documented exposure of the group to macro factor j (table below) and
f_j in [-1, 1] is the factor's current direction from regime.factor_trends().

Stock macro score = 0.6 * group score (sector/industry, plus country for KR names)
                  + 0.4 * empirical sensitivity score, where the empirical part regresses the
stock's daily returns on market, rate, USD and oil moves (2y) and asks whether the factor
moves of the last 60 days were a tailwind or headwind for *this* stock.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..i18n_ko import group as gko

# Exposure priors: + means the group tends to benefit when the factor moves in the named direction.
EXPOSURES: dict[str, dict[str, float]] = {
    "Technology": {"rates_down": 0.8, "usd_down": 0.3, "oil_up": -0.2, "growth": 0.5, "inflation": -0.4, "liquidity": 0.7, "risk": 0.8},
    "Semiconductors": {"rates_down": 0.6, "usd_down": 0.3, "oil_up": -0.2, "growth": 0.7, "inflation": -0.3, "liquidity": 0.7, "risk": 1.0},
    "Software": {"rates_down": 0.9, "growth": 0.4, "inflation": -0.4, "liquidity": 0.8, "risk": 0.8},
    "Communication Services": {"rates_down": 0.5, "growth": 0.4, "liquidity": 0.5, "risk": 0.6, "inflation": -0.2},
    "Consumer Discretionary": {"rates_down": 0.6, "oil_up": -0.5, "growth": 0.6, "inflation": -0.5, "risk": 0.6},
    "Consumer Staples": {"rates_down": 0.3, "growth": -0.2, "inflation": -0.2, "risk": -0.6},
    "Health Care": {"rates_down": 0.3, "growth": -0.1, "risk": -0.3},
    "Biotech": {"rates_down": 0.8, "liquidity": 0.7, "risk": 0.7},
    "Financials": {"rates_down": -0.3, "curve_steepening": 0.6, "growth": 0.7, "risk": 0.5, "inflation": 0.1},
    "Regional Banks": {"rates_down": -0.2, "curve_steepening": 0.8, "growth": 0.7, "risk": 0.6, "liquidity": 0.5},
    "Industrials": {"rates_down": 0.2, "growth": 0.8, "risk": 0.5, "usd_down": 0.2},
    "Aerospace & Defense": {"growth": 0.3, "risk": -0.1, "rates_down": 0.2},
    "Homebuilders": {"rates_down": 1.0, "growth": 0.5, "inflation": -0.3},
    "Materials": {"growth": 0.7, "usd_down": 0.6, "inflation": 0.5, "oil_up": 0.2},
    "Energy": {"oil_up": 1.0, "inflation": 0.6, "usd_down": 0.3, "growth": 0.4, "rates_down": -0.2},
    "Utilities": {"rates_down": 0.9, "growth": -0.3, "risk": -0.5},
    "Real Estate": {"rates_down": 1.0, "liquidity": 0.5, "growth": 0.2},
    "Gold Miners": {"usd_down": 0.8, "rates_down": 0.8, "inflation": 0.6, "risk": -0.3},
    "Gold": {"usd_down": 0.8, "rates_down": 0.8, "inflation": 0.6, "risk": -0.5},
    "United States": {"liquidity": 0.6, "risk": 0.8, "growth": 0.6, "rates_down": 0.5},
    "Korea": {"usd_down": 0.8, "growth": 0.6, "risk": 0.7, "liquidity": 0.4},
    "Japan": {"usd_down": -0.3, "growth": 0.5, "risk": 0.6},
    "China": {"usd_down": 0.6, "growth": 0.6, "risk": 0.6, "liquidity": 0.4},
    "Eurozone": {"usd_down": 0.5, "growth": 0.6, "risk": 0.6},
    "Taiwan": {"usd_down": 0.5, "growth": 0.7, "risk": 0.9, "liquidity": 0.5},
    "India": {"usd_down": 0.5, "oil_up": -0.5, "growth": 0.6, "risk": 0.6},
    "Long Treasuries": {"rates_down": 1.0, "inflation": -0.8, "growth": -0.6, "risk": -0.5},
    "US Dollar": {"usd_down": -1.0, "risk": -0.4},
    "Crude Oil": {"oil_up": 1.0, "growth": 0.4, "inflation": 0.5},
    "Bitcoin": {"liquidity": 1.0, "risk": 0.9, "usd_down": 0.5, "rates_down": 0.5},
    "High Yield Credit": {"risk": 0.7, "liquidity": 0.7, "growth": 0.5, "rates_down": 0.3},
}

GROUP_PROXIES: list[tuple[str, str, str]] = [
    # (group, kind, proxy ETF / symbol)
    ("Technology", "Sector", "XLK"), ("Communication Services", "Sector", "XLC"),
    ("Consumer Discretionary", "Sector", "XLY"), ("Consumer Staples", "Sector", "XLP"),
    ("Health Care", "Sector", "XLV"), ("Financials", "Sector", "XLF"), ("Industrials", "Sector", "XLI"),
    ("Materials", "Sector", "XLB"), ("Energy", "Sector", "XLE"), ("Utilities", "Sector", "XLU"),
    ("Real Estate", "Sector", "XLRE"),
    ("Semiconductors", "Industry", "SMH"), ("Software", "Industry", "IGV"), ("Biotech", "Industry", "XBI"),
    ("Regional Banks", "Industry", "KRE"), ("Aerospace & Defense", "Industry", "ITA"),
    ("Homebuilders", "Industry", "XHB"), ("Gold Miners", "Industry", "GDX"),
    ("United States", "Country", "SPY"), ("Korea", "Country", "EWY"), ("Japan", "Country", "EWJ"),
    ("China", "Country", "FXI"), ("Eurozone", "Country", "EZU"), ("Taiwan", "Country", "EWT"),
    ("India", "Country", "INDA"),
    ("Long Treasuries", "Asset", "TLT"), ("Gold", "Asset", "GLD"), ("US Dollar", "Asset", "UUP"),
    ("Crude Oil", "Asset", "USO"), ("Bitcoin", "Asset", "BTC-USD"), ("High Yield Credit", "Asset", "HYG"),
]
FACTOR_LABELS = {"rates_down": "금리", "curve_steepening": "장단기 금리차", "usd_down": "달러",
                 "oil_up": "유가", "growth": "성장", "inflation": "인플레이션", "liquidity": "유동성",
                 "risk": "위험선호"}
# How to describe the factor's *actual* move: (when value > 0, when value < 0)
FACTOR_MOVES = {"rates_down": ("금리 하락", "금리 상승"), "curve_steepening": ("장단기 금리차 확대", "장단기 금리차 축소"),
                "usd_down": ("달러 약세", "달러 강세"), "oil_up": ("유가 상승", "유가 하락"),
                "growth": ("성장 개선", "성장 둔화"), "inflation": ("인플레이션 압력 상승", "인플레이션 완화"),
                "liquidity": ("유동성 개선", "유동성 위축"), "risk": ("위험선호 강함", "위험선호 약함")}

YAHOO_SECTOR_MAP = {"Technology": "Technology", "Financial Services": "Financials", "Energy": "Energy",
                    "Healthcare": "Health Care", "Consumer Cyclical": "Consumer Discretionary",
                    "Consumer Defensive": "Consumer Staples", "Industrials": "Industrials",
                    "Basic Materials": "Materials", "Utilities": "Utilities", "Real Estate": "Real Estate",
                    "Communication Services": "Communication Services"}
INDUSTRY_MAP = {"Semiconductors": "Semiconductors", "Semiconductor Equipment & Materials": "Semiconductors",
                "Software - Application": "Software", "Software - Infrastructure": "Software",
                "Banks - Regional": "Regional Banks", "Biotechnology": "Biotech",
                "Aerospace & Defense": "Aerospace & Defense", "Gold": "Gold Miners",
                "Residential Construction": "Homebuilders"}


def map_group(sector: str | None, industry: str | None) -> str | None:
    if industry and industry in INDUSTRY_MAP:
        return INDUSTRY_MAP[industry]
    if sector:
        return YAHOO_SECTOR_MAP.get(sector, sector if sector in EXPOSURES else None)
    return None


def group_score(group: str, factors: dict[str, dict], rs: float | None = None) -> dict:
    exp = EXPOSURES.get(group, {})
    contrib = []
    num, den = 0.0, 0.0
    for f, e in exp.items():
        if f not in factors:
            continue
        v = factors[f]["value"]
        num += e * v
        den += abs(e)
        move = FACTOR_MOVES.get(f, (f, f))[0 if v >= 0 else 1]
        contrib.append({"factor": FACTOR_LABELS.get(f, f), "move": move, "exposure": e, "factor_value": round(v, 3),
                        "points": e * v, "evidence": factors[f]["text"]})
    align = num / den if den else 0.0
    rs_term = 0.0 if rs is None else float(np.clip(rs, -1, 1))
    score = 50 + 35 * align + 15 * rs_term
    for c in contrib:
        c["points"] = round(35 * c["points"] / den, 1) if den else 0
    contrib.sort(key=lambda c: -abs(c["points"]))
    reasons = [f"{c['move']}({c['evidence']}) → {gko(group)}에 순풍" for c in contrib if c["points"] >= 1]
    risks = [f"{c['move']}({c['evidence']}) → {gko(group)}에 역풍" for c in contrib if c["points"] <= -1]
    if rs is not None:
        (reasons if rs > 0.15 else risks if rs < -0.15 else []).append(
            f"S&P 500 대비 3개월 상대강도 {'우위' if rs > 0 else '열위'} ({rs * 20:+.1f}%p)")
    return {"group": group, "score": round(float(np.clip(score, 0, 100)), 1), "alignment": round(align, 3),
            "relative_strength": None if rs is None else round(rs, 3), "contributions": contrib,
            "reasons": reasons, "risks": risks}


def relative_strength(px: pd.Series, bench: pd.Series, days: int = 63) -> float | None:
    """3-month excess return vs benchmark, scaled so +/-20% maps to +/-1."""
    px, bench = px.dropna(), bench.dropna()
    if len(px) <= days or len(bench) <= days:
        return None
    r = px.iloc[-1] / px.iloc[-1 - days] - 1
    b = bench.iloc[-1] / bench.iloc[-1 - days] - 1
    return float(np.clip((r - b) / 0.20, -1, 1))


FACTOR_COLS = ("market", "rate_bp", "usd", "oil")


def factor_betas(stock_close: pd.Series, factors: pd.DataFrame, years: float = 2.0) -> dict | None:
    """OLS of daily stock returns on factor moves (market %, 10Y bp change, USD %, oil %), ridge-stabilised."""
    r = stock_close.pct_change().rename("y")
    df = pd.concat([r, factors], axis=1, join="inner").dropna()
    df = df.iloc[-int(252 * years):]
    if len(df) < 120:
        return None
    X = df[list(FACTOR_COLS)].to_numpy()
    y = df["y"].to_numpy()
    Xc = X - X.mean(0)
    lam = 1e-6 * len(df)
    beta = np.linalg.solve(Xc.T @ Xc + lam * np.eye(X.shape[1]), Xc.T @ (y - y.mean()))
    resid = (y - y.mean()) - Xc @ beta
    r2 = 1 - resid.var() / y.var() if y.var() > 0 else 0
    return {"betas": dict(zip(FACTOR_COLS, beta.tolist())), "r2": float(r2), "n": int(len(df))}


def stock_macro_score(group_res: dict | None, country_res: dict | None, betas: dict | None,
                      factor_moves_60d: dict[str, float] | None) -> dict:
    parts, reasons, risks = [], [], []
    if group_res:
        g = group_res["score"]
        if country_res:
            g = 0.7 * g + 0.3 * country_res["score"]
            reasons += [f"[{gko(country_res['group'])}] {x}" for x in country_res["reasons"][:2]]
            risks += [f"[{gko(country_res['group'])}] {x}" for x in country_res["risks"][:2]]
        parts.append(("group", g, 0.6))
        reasons += [f"[{gko(group_res['group'])}] {x}" for x in group_res["reasons"][:4]]
        risks += [f"[{gko(group_res['group'])}] {x}" for x in group_res["risks"][:4]]
    emp = None
    if betas and factor_moves_60d:
        b = betas["betas"]
        labels = {"rate_bp": ("미 10년물 금리", "bp"), "usd": ("달러인덱스", "%"), "oil": ("유가", "%")}
        tail = 0.0
        details = []
        for k, (lab, unit) in labels.items():
            mv = factor_moves_60d.get(k)
            if mv is None:
                continue
            move_dec = mv if unit == "bp" else mv / 100
            eff = b[k] * move_dec * 100  # % return attributable
            tail += eff
            per = f"+10bp당 {b[k] * 1000:+.2f}%" if unit == "bp" else f"+1%당 {b[k]:+.2f}%"
            details.append({"factor": lab, "move": f"{mv:+.1f}{unit}", "sensitivity": per, "effect_pct": round(eff, 2)})
            if eff > 0.75:
                reasons.append(f"{lab} 60일 {mv:+.1f}{unit} 변동이 순풍: 추정 {eff:+.1f}% (민감도 {per})")
            elif eff < -0.75:
                risks.append(f"{lab} 60일 {mv:+.1f}{unit} 변동이 역풍: 추정 {eff:+.1f}% (민감도 {per})")
        emp_score = float(np.clip(50 + tail * 2.5, 0, 100))  # +/-20% estimated tailwind spans the full range
        emp = {"score": round(emp_score, 1), "tailwind_pct": round(tail, 2), "details": details,
               "r2": round(betas["r2"], 3), "market_beta": round(b["market"], 2), "n": betas["n"]}
        parts.append(("empirical", emp_score, 0.4))
    if not parts:
        return {"score": None, "reasons": [], "risks": ["이 종목의 매크로 데이터를 불러올 수 없습니다"]}
    tw = sum(w for _, _, w in parts)
    score = sum(s * w for _, s, w in parts) / tw
    return {"score": round(score, 1), "group": group_res, "country": country_res, "empirical": emp,
            "reasons": reasons, "risks": risks,
            "formula": "0.6 × 업종 매크로 점수(한국 종목은 국가 점수 30% 반영) + 0.4 × 실측 팩터 민감도 점수"}
