"""Pivots, volume profile (매물대) and support / resistance.

Support/resistance combines five independent evidence sources and clusters them:
swing highs/lows, volume-profile nodes (POC / VAH / VAL), moving averages,
previous period highs/lows (52-week, 20-day) and historical price clusters
(levels touched repeatedly). A level's strength is the sum of its evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Pivot:
    idx: int          # bar where the extreme occurred
    confirm_idx: int  # first bar at which the pivot is knowable (idx + k) - used for causal logic
    price: float
    kind: str         # "H" | "L"


def find_pivots(high: np.ndarray, low: np.ndarray, k: int = 5) -> list[Pivot]:
    """Swing points: a bar whose high (low) is the max (min) of the k bars on each side.

    A pivot at i is only *confirmed* at i + k, so consumers that must be causal
    (pattern history, backtests) filter on confirm_idx <= t.
    """
    n = len(high)
    out: list[Pivot] = []
    for i in range(k, n - k):
        wh = high[i - k:i + k + 1]
        wl = low[i - k:i + k + 1]
        if high[i] == wh.max() and (wh == high[i]).sum() == 1:
            out.append(Pivot(i, i + k, float(high[i]), "H"))
        if low[i] == wl.min() and (wl == low[i]).sum() == 1:
            out.append(Pivot(i, i + k, float(low[i]), "L"))
    out.sort(key=lambda p: (p.idx, p.kind))
    return out


def volume_profile(df: pd.DataFrame, bins: int = 40, value_area: float = 0.70) -> dict | None:
    """Horizontal volume histogram. Each bar's volume is spread uniformly over its high-low range."""
    d = df.dropna(subset=["high", "low", "volume"])
    if len(d) < 10:
        return None
    lo, hi = float(d["low"].min()), float(d["high"].max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    width = edges[1] - edges[0]
    for h, low_, v in zip(d["high"].to_numpy(), d["low"].to_numpy(), d["volume"].to_numpy()):
        if v <= 0:
            continue
        a = int(min(bins - 1, max(0, (low_ - lo) // width)))
        b = int(min(bins - 1, max(0, (h - lo) // width)))
        vol[a:b + 1] += v / (b - a + 1)
    poc_i = int(vol.argmax())
    total = vol.sum()
    inc = {poc_i}
    acc = vol[poc_i]
    lo_i, hi_i = poc_i, poc_i
    while acc < value_area * total and (lo_i > 0 or hi_i < bins - 1):
        down = vol[lo_i - 1] if lo_i > 0 else -1
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if up >= down:
            hi_i += 1
            acc += vol[hi_i]
            inc.add(hi_i)
        else:
            lo_i -= 1
            acc += vol[lo_i]
            inc.add(lo_i)
    centers = (edges[:-1] + edges[1:]) / 2
    return {
        "bins": [{"price_low": float(edges[i]), "price_high": float(edges[i + 1]), "price": float(centers[i]),
                  "volume": float(vol[i]), "in_value_area": i in inc} for i in range(bins)],
        "poc": float(centers[poc_i]),
        "vah": float(edges[hi_i + 1]),
        "val": float(edges[lo_i]),
        "start": str(d.index[0].date()) if hasattr(d.index[0], "date") else str(d.index[0]),
        "end": str(d.index[-1].date()) if hasattr(d.index[-1], "date") else str(d.index[-1]),
        "total_volume": float(total),
    }


def _cluster(cands: list[dict], tol: float) -> list[dict]:
    cands = sorted(cands, key=lambda c: c["price"])
    clusters: list[list[dict]] = []
    for c in cands:
        if clusters and abs(c["price"] - np.average([x["price"] for x in clusters[-1]],
                                                     weights=[x["w"] for x in clusters[-1]])) <= tol:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    out = []
    for cl in clusters:
        w = np.array([x["w"] for x in cl])
        price = float(np.average([x["price"] for x in cl], weights=w))
        sources: dict[str, int] = {}
        for x in cl:
            sources[x["src"]] = sources.get(x["src"], 0) + 1
        out.append({"price": price, "strength": float(w.sum()),
                    "sources": [f"{k} ×{v}" if v > 1 else k for k, v in sources.items()]})
    return out


def support_resistance(ind: pd.DataFrame, lookback: int = 250, vp: dict | None = None) -> dict:
    """`ind` is the indicator frame (needs ohlcv + sma*/atr/hi52/lo52/hi20/lo20)."""
    d = ind.iloc[-lookback:]
    price = float(d["close"].iloc[-1])
    atr_ = float(d["atr"].iloc[-1]) if pd.notna(d["atr"].iloc[-1]) else price * 0.02
    tol = max(0.6 * atr_, price * 0.008)
    n = len(d)
    cands: list[dict] = []

    piv = find_pivots(d["high"].to_numpy(), d["low"].to_numpy(), k=5)
    for p in piv:
        recency = 0.5 + (p.idx / max(n - 1, 1))  # 0.5 .. 1.5
        cands.append({"price": p.price, "w": 1.0 * recency, "src": "스윙 고점" if p.kind == "H" else "스윙 저점"})

    if vp is None:
        vp = volume_profile(d.iloc[-126:])
    if vp:
        cands += [{"price": vp["poc"], "w": 2.0, "src": "매물대 POC"},
                  {"price": vp["vah"], "w": 1.0, "src": "가치영역 상단"},
                  {"price": vp["val"], "w": 1.0, "src": "가치영역 하단"}]
        # High-volume nodes (top 15% of bins) act as historical price clusters.
        vols = np.array([b["volume"] for b in vp["bins"]])
        thr = np.quantile(vols, 0.85)
        for b in vp["bins"]:
            if b["volume"] >= thr:
                cands.append({"price": b["price"], "w": 0.6, "src": "거래 집중 가격대"})

    for p in (20, 60, 120, 200):
        v = d[f"sma{p}"].iloc[-1]
        if pd.notna(v):
            cands.append({"price": float(v), "w": 0.8 if p >= 120 else 0.6, "src": f"SMA{p}"})
    for col, label, w in (("hi52", "52주 고점", 1.5), ("lo52", "52주 저점", 1.5),
                          ("hi20", "20일 고점", 0.7), ("lo20", "20일 저점", 0.7)):
        v = d[col].iloc[-1]
        if pd.notna(v):
            cands.append({"price": float(v), "w": w, "src": label})

    levels = _cluster(cands, tol)
    for lv in levels:
        lv["distance_pct"] = (lv["price"] / price - 1) * 100
    strong = [lv for lv in levels if lv["strength"] >= 1.5]
    below = sorted([lv for lv in strong if lv["price"] < price - 0.25 * tol], key=lambda x: -x["price"])
    above = sorted([lv for lv in strong if lv["price"] > price + 0.25 * tol], key=lambda x: x["price"])

    def pick(lst: list[dict], prefix: str) -> list[dict]:
        chosen: list[dict] = []
        for lv in lst:
            if all(abs(lv["price"] - c["price"]) > tol for c in chosen):
                chosen.append(lv)
            if len(chosen) == 2:
                break
        return [{"label": f"{prefix}{i + 1}", **c, "projected": False} for i, c in enumerate(chosen)]

    sup, res = pick(below, "S"), pick(above, "R")
    if not res:  # price discovery: project ATR-based targets, clearly flagged
        for i, m in enumerate((2, 4)):
            p_ = price + m * atr_
            res.append({"label": f"R{i + 1}", "price": p_, "strength": 0.0, "distance_pct": (p_ / price - 1) * 100,
                        "sources": [f"추정 +{m} ATR (조회 기간 내 위쪽 저항 없음)"], "projected": True})
    if not sup:
        for i, m in enumerate((2, 4)):
            p_ = price - m * atr_
            sup.append({"label": f"S{i + 1}", "price": p_, "strength": 0.0, "distance_pct": (p_ / price - 1) * 100,
                        "sources": [f"추정 -{m} ATR (조회 기간 내 지지 없음)"], "projected": True})
    ns, nr = sup[0], res[0]
    down, up = abs(ns["distance_pct"]), abs(nr["distance_pct"])
    return {
        "price": price, "atr": atr_, "tolerance": tol,
        "supports": sup, "resistances": res,
        "nearest_support_pct": -down, "nearest_resistance_pct": up,
        "reward_risk": (up / down) if down > 0 else None,
        "all_levels": sorted(levels, key=lambda x: -x["strength"])[:12],
    }
