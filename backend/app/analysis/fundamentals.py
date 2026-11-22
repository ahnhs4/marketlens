"""Fundamental scoring (valuation, growth, quality, balance sheet) and valuation history.

Missing fields are excluded from the weighted average (never imputed) and listed
as unavailable, so the score only reflects data that actually exists.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .scoring import band, lin


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def extract(info: dict) -> dict:
    """Normalise Yahoo quoteSummary fields into a stable schema."""
    mc = _f(info.get("marketCap"))
    fcf = _f(info.get("freeCashflow"))
    cash, debt = _f(info.get("totalCash")), _f(info.get("totalDebt"))
    ebitda = _f(info.get("ebitda"))
    de = _f(info.get("debtToEquity"))
    return {
        "market_cap": mc, "enterprise_value": _f(info.get("enterpriseValue")),
        "pe": _f(info.get("trailingPE")), "forward_pe": _f(info.get("forwardPE")),
        "peg": _f(info.get("trailingPegRatio") or info.get("pegRatio")),
        "ev_ebitda": _f(info.get("enterpriseToEbitda")), "ps": _f(info.get("priceToSalesTrailing12Months")),
        "pb": _f(info.get("priceToBook")),
        "fcf_yield": (fcf / mc) if fcf is not None and mc else None,
        "revenue_growth": _f(info.get("revenueGrowth")), "earnings_growth": _f(info.get("earningsGrowth")),
        "earnings_quarterly_growth": _f(info.get("earningsQuarterlyGrowth")),
        "gross_margin": _f(info.get("grossMargins")), "operating_margin": _f(info.get("operatingMargins")),
        "profit_margin": _f(info.get("profitMargins")), "roe": _f(info.get("returnOnEquity")),
        "roa": _f(info.get("returnOnAssets")),
        "debt_to_equity": (de / 100) if de is not None else None,  # Yahoo reports D/E in percent
        "total_cash": cash, "total_debt": debt, "net_debt": (debt - cash) if debt is not None and cash is not None else None,
        "net_debt_to_ebitda": ((debt - cash) / ebitda) if debt is not None and cash is not None and ebitda else None,
        "current_ratio": _f(info.get("currentRatio")), "free_cash_flow": fcf, "ebitda": ebitda,
        "dividend_yield": _f(info.get("dividendYield")), "payout_ratio": _f(info.get("payoutRatio")),
        "beta": _f(info.get("beta")), "shares_outstanding": _f(info.get("sharesOutstanding")),
        "float_shares": _f(info.get("floatShares")),
        "eps_ttm": _f(info.get("trailingEps")), "eps_forward": _f(info.get("forwardEps")),
        "target_mean_price": _f(info.get("targetMeanPrice")), "recommendation": info.get("recommendationKey"),
        "analyst_count": _f(info.get("numberOfAnalystOpinions")),
    }


# (field, label, x_bad, x_good) - linear map, x_good -> 100
RULES = {
    "valuation": [("forward_pe", "선행 PER", 45, 10), ("pe", "PER", 60, 12), ("peg", "PEG", 3.0, 0.8),
                  ("ev_ebitda", "EV/EBITDA", 35, 8), ("ps", "PSR", 20, 1.5), ("fcf_yield", "FCF 수익률", -0.01, 0.07)],
    "growth": [("revenue_growth", "매출 성장률", -0.05, 0.30), ("earnings_growth", "EPS 성장률", -0.10, 0.40)],
    "quality": [("roe", "ROE", 0.0, 0.30), ("gross_margin", "매출총이익률", 0.15, 0.65),
                ("operating_margin", "영업이익률", 0.0, 0.35)],
    "balance_sheet": [("debt_to_equity", "부채비율(D/E)", 2.5, 0.2), ("net_debt_to_ebitda", "순부채/EBITDA", 4.0, -1.0),
                      ("current_ratio", "유동비율", 0.8, 2.0)],
}
PILLAR_WEIGHTS = {"valuation": 0.30, "growth": 0.25, "quality": 0.30, "balance_sheet": 0.15}


def fundamental_score(f: dict) -> dict:
    pillars, missing = {}, []
    for pillar, rules in RULES.items():
        items = []
        for field, label, bad, good in rules:
            v = f.get(field)
            if v is None or (field in ("pe", "forward_pe", "ev_ebitda") and v <= 0):
                missing.append(label)
                continue
            s = lin(v, bad, good)
            items.append({"field": field, "label": label, "value": v, "score": round(s, 1)})
        pillars[pillar] = {"score": round(float(np.mean([i["score"] for i in items])), 1) if items else None,
                           "items": items}
    avail = {k: v["score"] for k, v in pillars.items() if v["score"] is not None}
    if not avail:
        return {"score": None, "pillars": pillars, "missing": missing, "band": None}
    tw = sum(PILLAR_WEIGHTS[k] for k in avail)
    score = sum(PILLAR_WEIGHTS[k] * s for k, s in avail.items()) / tw
    return {"score": round(score, 1), "band": band(score), "pillars": pillars, "missing": missing,
            "weights": PILLAR_WEIGHTS,
            "note": "절대 기준(업종 상대 아님); 없는 지표는 추정하지 않고 제외합니다."}


def pe_history(close: pd.Series, earnings: list[dict], years: int = 5) -> dict | None:
    """Trailing P/E over time from reported quarterly EPS (adjusted EPS as published on the earnings calendar).

    At each date only EPS reported on or before that date is used (no look-ahead).
    """
    rep = sorted([e for e in earnings if e.get("eps_actual") is not None], key=lambda e: e["date"])
    if len(rep) < 8 or close.empty:
        return None
    dates = pd.DatetimeIndex([pd.Timestamp(e["date"]).tz_convert(None).normalize() for e in rep])
    eps = pd.Series([e["eps_actual"] for e in rep], index=dates)
    eps = eps[~eps.index.duplicated(keep="last")]
    ttm = eps.rolling(4).sum().dropna()
    if ttm.empty:
        return None
    px = close[close.index >= close.index[-1] - pd.DateOffset(years=years)]
    ttm_daily = ttm.reindex(px.index.union(ttm.index)).ffill().reindex(px.index)
    pe = (px / ttm_daily).where(ttm_daily > 0).dropna()
    if len(pe) < 120:
        return None
    cur = float(pe.iloc[-1])
    pct = float((pe < cur).mean() * 100)
    weekly = pe.resample("W").last().dropna()
    return {"current": cur, "median": float(pe.median()), "min": float(pe.min()), "max": float(pe.max()),
            "percentile": pct, "years": years, "basis": "주가 / 최근 4개 분기 발표 EPS(조정 EPS) 합계",
            "assessment": ("역사적으로 비싼 편" if pct >= 75 else "역사적으로 싼 편" if pct <= 25 else "역사적 평균 수준"),
            "series": [{"date": str(d.date()), "pe": round(float(v), 2)} for d, v in weekly.items()]}
