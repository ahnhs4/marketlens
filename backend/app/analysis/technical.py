"""Moving-average structure and RSI analysis (state + events, with numbers)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..i18n_ko import STRUCTURE_KO
from .levels import find_pivots

CROSS_PAIRS = ((5, 20), (20, 60), (50, 200), (60, 120), (120, 200))


def _v(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


def _date(idx) -> str:
    return str(idx.date()) if hasattr(idx, "date") else str(idx)


def ma_analysis(ind: pd.DataFrame, lookback_events: int = 10) -> dict:
    last = ind.iloc[-1]
    price = float(last["close"])
    smas = {p: _v(last.get(f"sma{p}")) for p in (5, 20, 60, 120, 200)}
    chain = [5, 20, 60, 120, 200]
    avail = [p for p in chain if smas[p] is not None]
    bull_pairs = sum(1 for a, b in zip(avail, avail[1:]) if smas[a] > smas[b])
    pairs = max(len(avail) - 1, 1)
    if bull_pairs == pairs and pairs >= 3:
        structure = "Strong Bullish Structure"
    elif bull_pairs == 0 and pairs >= 3:
        structure = "Strong Bearish Structure"
    elif bull_pairs / pairs >= 0.75:
        structure = "Bullish Structure"
    elif bull_pairs / pairs <= 0.25:
        structure = "Bearish Structure"
    else:
        structure = "Mixed Structure"
    order = " > ".join(str(p) for p in sorted(avail, key=lambda p: -smas[p]))

    events = []
    n = len(ind)
    for a, b in CROSS_PAIRS:
        fa, fb = ind.get(f"sma{a}"), ind.get(f"sma{b}")
        if fa is None or fb is None:
            continue
        diff = (fa - fb).to_numpy()
        for i in range(max(1, n - lookback_events), n):
            if np.isnan(diff[i]) or np.isnan(diff[i - 1]):
                continue
            if diff[i - 1] <= 0 < diff[i] or diff[i - 1] >= 0 > diff[i]:
                up = diff[i] > 0
                name = f"{'골든크로스' if up else '데드크로스'} (SMA{a}/{b})"
                events.append({"event": name, "kind": "golden" if up else "death", "direction": "bullish" if up else "bearish",
                               "date": _date(ind.index[i]), "bars_ago": n - 1 - i, "pair": [a, b]})
    # price vs SMA200 crossing
    if smas[200] is not None and n > 2:
        c, s200 = ind["close"].to_numpy(), ind["sma200"].to_numpy()
        for i in range(max(1, n - lookback_events), n):
            if not np.isnan(s200[i - 1]) and (c[i - 1] - s200[i - 1]) * (c[i] - s200[i]) < 0:
                up = c[i] > s200[i]
                events.append({"event": f"주가 SMA200 {'상향 돌파' if up else '하향 이탈'}", "kind": "price200",
                               "direction": "bullish" if up else "bearish", "date": _date(ind.index[i]),
                               "bars_ago": n - 1 - i, "pair": [0, 200]})

    states = []
    if smas[200] is not None:
        d200 = (price / smas[200] - 1) * 100
        states.append({"state": f"주가 {'>' if d200 >= 0 else '<'} SMA200", "value": d200,
                       "bullish": d200 >= 0, "detail": f"SMA200 대비 {d200:+.1f}%"})
    for a, b in ((20, 60), (60, 120), (120, 200)):
        if smas[a] is not None and smas[b] is not None:
            up = smas[a] > smas[b]
            states.append({"state": f"SMA{a} {'>' if up else '<'} SMA{b}", "bullish": up,
                           "value": (smas[a] / smas[b] - 1) * 100, "detail": f"{(smas[a] / smas[b] - 1) * 100:+.1f}%"})

    spread = None
    regime = "normal"
    mids = [c for c in ("sma20", "sma60", "sma120") if c in ind]
    if len(mids) == 3 and ind[mids].iloc[-1].notna().all():
        sp = (ind[mids].max(axis=1) - ind[mids].min(axis=1)) / ind["close"] * 100
        spread = float(sp.iloc[-1])
        prev = sp.iloc[-11] if len(sp) > 11 else np.nan
        if spread < 3 and spread <= sp.iloc[-60:].mean():
            regime = "compression"
        elif pd.notna(prev) and spread > 5 and spread > prev * 1.3:
            regime = "expansion"
    slopes = {}
    for p, lag in ((20, 5), (60, 10), (200, 20)):
        col = ind.get(f"sma{p}")
        if col is not None and len(col) > lag and pd.notna(col.iloc[-1]) and pd.notna(col.iloc[-1 - lag]):
            slopes[f"sma{p}"] = float((col.iloc[-1] / col.iloc[-1 - lag] - 1) * 100)
    return {"price": price, "smas": smas, "structure": structure, "structure_ko": STRUCTURE_KO[structure], "order": order,
            "bullish_pairs": bull_pairs,
            "pairs": pairs, "events": sorted(events, key=lambda e: e["bars_ago"]), "states": states,
            "ma_spread_pct": spread, "spread_regime": regime, "slopes_pct": slopes}


def rsi_analysis(ind: pd.DataFrame, lookback: int = 90, recent: int = 25) -> dict:
    r = ind["rsi"]
    if r.dropna().empty:
        return {"value": None}
    val = float(r.iloc[-1])
    zone = ("oversold" if val < 30 else "bearish" if val < 50 else "bullish" if val <= 70 else "overbought")
    mom = float(val - r.iloc[-6]) if len(r) > 6 and pd.notna(r.iloc[-6]) else None
    tail = r.dropna().iloc[-14:]
    slope = float(np.polyfit(np.arange(len(tail)), tail.to_numpy(), 1)[0]) if len(tail) >= 5 else None
    trend = None if slope is None else ("rising" if slope > 0.5 else "falling" if slope < -0.5 else "flat")

    divergences = []
    d = ind.iloc[-lookback:]
    piv = find_pivots(d["close"].to_numpy(), d["close"].to_numpy(), k=3)
    n = len(d)
    rv = d["rsi"].to_numpy()
    for kind, label in (("L", "강세 다이버전스"), ("H", "약세 다이버전스")):
        pts = [p for p in piv if p.kind == kind and not np.isnan(rv[p.idx])]
        if len(pts) < 2:
            continue
        p1, p2 = pts[-2], pts[-1]
        if n - 1 - p2.idx > recent or p2.idx - p1.idx < 5:
            continue
        if kind == "L" and p2.price < p1.price and rv[p2.idx] > rv[p1.idx] + 2:
            divergences.append({"type": label, "direction": "bullish", "date": _date(d.index[p2.idx]),
                                "detail": f"주가 저점 하락({p1.price:,.2f}→{p2.price:,.2f}) 중 RSI 저점 상승 "
                                          f"({rv[p1.idx]:.0f}→{rv[p2.idx]:.0f})"})
        if kind == "H" and p2.price > p1.price and rv[p2.idx] < rv[p1.idx] - 2:
            divergences.append({"type": label, "direction": "bearish", "date": _date(d.index[p2.idx]),
                                "detail": f"주가 고점 상승({p1.price:,.2f}→{p2.price:,.2f}) 중 RSI 고점 하락 "
                                          f"({rv[p1.idx]:.0f}→{rv[p2.idx]:.0f})"})
    above200 = pd.notna(ind["sma200"].iloc[-1]) and ind["close"].iloc[-1] > ind["sma200"].iloc[-1]
    if zone == "oversold":
        ctx = ("장기 상승 추세 속 과매도 - 눌림목일 수 있으나 RSI 반등 확인이 필요"
               if above200 else "하락 추세 속 과매도 - RSI가 30 아래에 오래 머물 수 있어 단독 매수 근거가 아님")
    elif zone == "overbought":
        ctx = ("추세가 받쳐주는 과매수 - 모멘텀은 강하지만 단기 조정 위험이 높음"
               if above200 else "SMA200 아래에서 과매수 - 역추세 반등이 소진될 수 있음")
    elif zone == "bullish":
        ctx = "RSI 50~70 모멘텀 구간 - 상승 추세와 부합"
    else:
        ctx = "RSI 30~50 구간 - 모멘텀이 약함"
    days_ob = int((r.iloc[-20:] > 70).sum())
    days_os = int((r.iloc[-20:] < 30).sum())
    return {"value": val, "zone": zone, "momentum_5d": mom, "slope_14d": slope, "trend": trend,
            "divergences": divergences, "context": ctx, "days_overbought_20d": days_ob,
            "days_oversold_20d": days_os}


HZ_KO = {"short": "단기", "medium": "중기", "long": "장기"}


def _osc_state(k: pd.Series, d: pd.Series, hi: float, lo: float, lookback: int = 20) -> dict | None:
    kk, dd = k.dropna(), d.dropna()
    if len(kk) < 5 or len(dd) < 5:
        return None
    kv, dv = float(kk.iloc[-1]), float(dd.iloc[-1])
    zone = "overbought" if kv >= hi else "oversold" if kv <= lo else "upper" if kv >= 50 else "lower"
    slope = float(kk.iloc[-1] - kk.iloc[-4]) if len(kk) >= 4 else 0.0
    diff = (k - d).dropna()
    cross = None
    for i in range(len(diff) - 1, max(0, len(diff) - lookback) - 1, -1):
        if i == 0:
            break
        a, b = diff.iloc[i - 1], diff.iloc[i]
        if a <= 0 < b or a >= 0 > b:
            level = float(k.loc[diff.index[i]])
            cross = {"type": "up" if b > 0 else "down", "bars_ago": len(diff) - 1 - i, "date": _date(diff.index[i]),
                     "level": round(level, 1), "in_extreme": level <= lo + 10 if b > 0 else level >= hi - 10}
            break
    return {"k": round(kv, 1), "d": round(dv, 1), "zone": zone, "slope": round(slope, 1),
            "rising": kv > dv, "cross": cross}


def oscillator_states(ind: pd.DataFrame) -> dict:
    """Short/medium/long state of the three lower-pane oscillators (slow stochastic and RSI)."""
    out: dict = {"stoch": {}, "rsi": {}}
    for hz in ("short", "medium", "long"):
        if f"stoch_{hz}_k" in ind:
            out["stoch"][hz] = _osc_state(ind[f"stoch_{hz}_k"], ind[f"stoch_{hz}_d"], 80, 20)
        col = {"short": "rsi_short", "medium": "rsi", "long": "rsi_long"}[hz]
        if col in ind and f"{col}_sig" in ind:
            out["rsi"][hz] = _osc_state(ind[col], ind[f"{col}_sig"], 70, 30)
    return out


def describe_osc(state: dict | None, name: str) -> str:
    """One Korean sentence for an oscillator state."""
    if not state:
        return f"{name}: 데이터를 불러올 수 없습니다"
    zone = {"overbought": "과매수권", "oversold": "과매도권", "upper": "중립 위쪽", "lower": "중립 아래쪽"}[state["zone"]]
    trend = "상승 중" if state["slope"] > 1 else "하락 중" if state["slope"] < -1 else "횡보"
    s = f"{name} %K {state['k']:.0f} / %D {state['d']:.0f} ({zone}, {trend})"
    c = state.get("cross")
    if c and c["bars_ago"] <= 10:
        when = "오늘" if c["bars_ago"] == 0 else f"{c['bars_ago']}일 전"
        if c["type"] == "up":
            s += f" · {when} %K가 %D를 상향 돌파" + (" (과매도권에서 상승 전환)" if c["in_extreme"] else "")
        else:
            s += f" · {when} %K가 %D를 하향 이탈" + (" (과매수권에서 하락 전환)" if c["in_extreme"] else "")
    return s
