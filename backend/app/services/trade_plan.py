"""One-card trade plan: entry, stop, targets, reward:risk and a position size from a per-trade loss limit.

Every price is tied to something measurable on the chart (a support / resistance level and what forms it,
or a multiple of ATR), so the card shows *why* a number is where it is. Nothing here places an order.

Sizing: quantity = floor(account x risk% / per-share risk), capped by a maximum position weight.
US prices are converted to KRW at the latest USD/KRW so the loss limit is in the account's currency.
"""
from __future__ import annotations

import math

from ..analysis import indicators, levels, user_rules
from . import prices
from .clock import market_of
from .store import clean, utcnow

STOP_BUFFER_ATR = 0.5          # stop sits this far below the support it relies on
STOP_MIN_ATR, STOP_MAX_ATR = 1.0, 3.5
STOP_FALLBACK_ATR = 2.0
MIN_RR = 1.5
DEFAULT_ACCOUNT_KRW = 10_000_000
DEFAULT_RISK_PCT = 1.0
DEFAULT_MAX_WEIGHT_PCT = 25.0


def _tick(price: float, kr: bool) -> float:
    """Round to something tradable: KRX tick sizes for won prices, cents otherwise."""
    if not kr:
        return round(price, 2)
    for limit, step in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < limit:
            return round(price / step) * step
    return round(price / 1000) * 1000


def _basis(level: dict) -> str:
    src = " · ".join(level.get("sources") or []) or "가격대"
    return f"{level['label']} ({src})"


def _stop(entry: float, atr: float, supports: list[dict], kr: bool) -> dict:
    for lv in supports:
        px = lv["price"] - STOP_BUFFER_ATR * atr
        dist = (entry - px) / atr if atr else 0
        if STOP_MIN_ATR <= dist <= STOP_MAX_ATR:
            return {"price": _tick(px, kr), "kind": "support",
                    "basis": f"{_basis(lv)} {lv['price']:,.2f} 아래로 ATR의 {STOP_BUFFER_ATR:g}배 여유",
                    "detail": f"지지선이 깨지면 가설이 틀린 것으로 보고 정리 · 현재가에서 ATR {dist:.1f}배 거리"}
    px = entry - STOP_FALLBACK_ATR * atr
    near = supports[0] if supports else None
    why = ("가까운 지지선이 없어" if near is None else
           f"가장 가까운 지지선({near['price']:,.2f})이 너무 {'가까워' if (entry - near['price']) / atr < STOP_MIN_ATR else '멀어'}")
    return {"price": _tick(px, kr), "kind": "atr",
            "basis": f"매수가 − ATR(14) × {STOP_FALLBACK_ATR:g}",
            "detail": f"{why} 평소 변동폭의 {STOP_FALLBACK_ATR:g}배를 기준으로 잡음"}


def _targets(entry: float, risk: float, resistances: list[dict], kr: bool) -> list[dict]:
    out = []
    for lv in resistances[:2]:
        if lv["price"] <= entry:
            continue
        out.append({"label": lv["label"], "price": _tick(lv["price"], kr), "kind": "resistance",
                    "basis": _basis(lv), "projected": bool(lv.get("projected"))})
    if not out:   # price discovery: no overhead level, fall back to multiples of the risk taken
        for mult in (2, 3):
            out.append({"label": f"{mult}R", "price": _tick(entry + mult * risk, kr), "kind": "multiple",
                        "basis": f"위쪽 저항선이 없어 손절폭의 {mult}배를 목표로 둠", "projected": True})
    for t in out:
        t["pct"] = t["price"] / entry - 1
        t["rr"] = (t["price"] - entry) / risk if risk > 0 else None
    return out


def _size(entry: float, stop: float, fx: float, account: float, risk_pct: float, max_weight_pct: float) -> dict:
    per_share = max(entry - stop, 0.0)
    per_share_krw = per_share * fx
    budget = account * risk_pct / 100
    by_risk = math.floor(budget / per_share_krw) if per_share_krw > 0 else 0
    by_weight = math.floor(account * max_weight_pct / 100 / (entry * fx)) if entry > 0 else 0
    qty = max(0, min(by_risk, by_weight))
    value = qty * entry * fx
    return {"account_krw": account, "risk_pct": risk_pct, "risk_budget_krw": budget, "max_weight_pct": max_weight_pct,
            "per_share_risk": per_share, "per_share_risk_krw": per_share_krw,
            "quantity": qty, "quantity_by_risk": by_risk, "quantity_by_weight": by_weight,
            "capped_by_weight": by_weight < by_risk,
            "position_value_krw": value, "weight_pct": (value / account * 100) if account else None,
            "loss_if_stopped_krw": qty * per_share_krw,
            "formula": f"수량 = (계좌 {account:,.0f}원 × {risk_pct:g}%) ÷ 1주당 손실 {per_share_krw:,.0f}원"
                       f" = {by_risk:,}주" + (f" → 비중 한도 {max_weight_pct:g}%로 {by_weight:,}주까지" if by_weight < by_risk else "")}


def plan(symbol: str, account_krw: float = DEFAULT_ACCOUNT_KRW, risk_pct: float = DEFAULT_RISK_PCT,
         max_weight_pct: float = DEFAULT_MAX_WEIGHT_PCT) -> dict:
    ps = prices.get_daily(symbol, years=2)
    ind = indicators.compute_all(ps.df)
    kr = market_of(symbol) == "KR"
    sr = levels.support_resistance(ind, vp=levels.volume_profile(ind.iloc[-126:]))
    last = ind.iloc[-1]
    entry, atr = float(sr["price"]), float(sr["atr"])
    fx = 1.0
    if not kr:
        fx = prices.get_quote("KRW=X").price
    stop = _stop(entry, atr, sr.get("supports") or [], kr)
    risk = entry - stop["price"]
    targets = _targets(entry, risk, sr.get("resistances") or [], kr)
    rr = targets[0]["rr"] if targets else None
    sizing = _size(entry, stop["price"], fx, account_krw, risk_pct, max_weight_pct)

    warnings = []
    row = last.to_dict()
    if user_rules.stoch_ml_overbought(row):
        warnings.append("중기·장기 스토캐스틱이 모두 과매수 - 내 규칙에서는 신규 매수 대상이 아닙니다")
    os_reasons = user_rules.rsi_ml_oversold(ind)
    if os_reasons:
        warnings.append("RSI 과매도 구간 - 반등 확인 전 매수는 내 규칙에서 제외됩니다 (" + ", ".join(os_reasons) + ")")
    if rr is not None and rr < MIN_RR:
        warnings.append(f"1차 목표가 기준 손익비 {rr:.2f} : 1 - 기준({MIN_RR:g} : 1)에 못 미칩니다. 지지선 근처까지 기다리는 편이 유리합니다")
    if sizing["quantity"] == 0:
        warnings.append("이 손실 한도로는 1주도 살 수 없습니다 - 한도를 올리거나 손절폭이 좁은 자리에서 다시 계산하세요")
    if sizing["capped_by_weight"] and sizing["quantity"] > 0:
        warnings.append(f"손실 한도로는 {sizing['quantity_by_risk']:,}주까지 가능하지만 한 종목 비중 한도({max_weight_pct:g}%) 때문에 줄였습니다")

    # Waiting for the first support instead of buying now: same stop, better entry.
    pullback = None
    s1 = (sr.get("supports") or [None])[0]
    if s1 and stop["kind"] == "support" and s1["price"] < entry and targets:
        pe = _tick(s1["price"], kr)
        prisk = pe - stop["price"]
        if prisk > 0:
            pullback = {"entry": pe, "basis": f"{_basis(s1)}까지 눌릴 때 매수", "distance_pct": pe / entry - 1,
                        "rr": (targets[0]["price"] - pe) / prisk,
                        "quantity": _size(pe, stop["price"], fx, account_krw, risk_pct, max_weight_pct)["quantity"]}

    return clean({
        "symbol": symbol, "currency": "KRW" if kr else "USD", "fx_usdkrw": None if kr else fx,
        "as_of": str(ind.index[-1].date()), "updated_at": utcnow().isoformat(),
        "entry": {"price": entry, "basis": "최근 종가", "detail": f"{ind.index[-1].date()} 종가 기준 (장중이면 실제 체결가는 다를 수 있음)"},
        "stop": {**stop, "pct": stop["price"] / entry - 1, "atr_multiple": risk / atr if atr else None},
        "targets": targets, "reward_risk": rr, "min_reward_risk": MIN_RR,
        "sizing": sizing, "pullback": pullback, "warnings": warnings,
        "indicators": {"atr": atr, "atr_pct": float(last["atr_pct"]) if last.get("atr_pct") == last.get("atr_pct") else None,
                       "rsi": float(last["rsi"]) if last.get("rsi") == last.get("rsi") else None,
                       "stoch_medium": float(last["stoch_medium_k"]) if last.get("stoch_medium_k") == last.get("stoch_medium_k") else None,
                       "stoch_long": float(last["stoch_long_k"]) if last.get("stoch_long_k") == last.get("stoch_long_k") else None},
        "levels": {"supports": [{"label": x["label"], "price": x["price"], "sources": x.get("sources")} for x in sr.get("supports") or []],
                   "resistances": [{"label": x["label"], "price": x["price"], "sources": x.get("sources")} for x in sr.get("resistances") or []]},
        "data": ps.meta(),
        "disclaimer": "지지·저항과 변동폭으로 계산한 계획표이며 매수 권유가 아닙니다. 주문은 직접 넣어야 하고, 이 앱은 주문을 내지 않습니다.",
    })
