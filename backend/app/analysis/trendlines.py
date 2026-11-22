"""Trendlines that price is respecting right now, ranked by how credible they are.

Method (the way chartists draw them, made mechanical):
  1. Swing points: a bar whose low (high) is the lowest (highest) of the k bars on each side.
  2. Candidates: every pair of swing lows (support) / swing highs (resistance) at least 10 bars apart,
     joined by a straight line in bar-index space (what the chart shows).
  3. A close more than 0.35 ATR beyond the line is a violation. A line is kept only if it has at most one
     violation since its first anchor and none in the last 10 bars - so it is being respected now.
  4. Touches: swing points within 0.35 ATR of the line (anchors included). 3+ touches = confirmed,
     2 = provisional (drawn dashed).
  5. Only lines within 6 ATR (and 15%) of the current price count - a line far from price is not being traded.
     Credibility score (0-100) = touches (most weight) + span + a recent touch - violations - distance
     from the current price; too-steep lines and scores under 35 are dropped. Near-duplicates keep the best one.
  6. The best support and resistance together are named when they form a pattern:
     parallel = 채널 (상승/하락/횡보 박스); converging = 삼각형 (상승/하락/대칭) or 쐐기 (상승/하락).
Lines describe the past; a break of a confirmed line is the usual signal, not a prediction.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TOL_ATR = 0.35
MIN_GAP = 10


def _pivots(x: np.ndarray, k: int, kind: str) -> list[int]:
    out = []
    for i in range(k, len(x) - k):
        win = x[i - k:i + k + 1]
        if (kind == "low" and x[i] == win.min() and (win == x[i]).sum() == 1) or \
           (kind == "high" and x[i] == win.max() and (win == x[i]).sum() == 1):
            out.append(i)
    return out


def _evaluate(a: int, b: int, piv: list[int], ext: np.ndarray, close: np.ndarray, atr: np.ndarray, kind: str) -> dict | None:
    n = len(close)
    m = (ext[b] - ext[a]) / (b - a)
    xs = np.arange(a, n)
    line = ext[a] + m * (xs - a)
    tol = TOL_ATR * atr[a:]
    if kind == "support":
        broke = close[a:] < line - tol
    else:
        broke = close[a:] > line + tol
    viol = int(broke.sum())
    if viol > 1 or broke[-10:].any():
        return None
    touches = [p for p in piv if p >= a and abs(ext[p] - (ext[a] + m * (p - a))) <= TOL_ATR * atr[p]]
    # keep touches at least 3 bars apart
    t2: list[int] = []
    for p in touches:
        if not t2 or p - t2[-1] >= 3:
            t2.append(p)
    if len(t2) < 2:
        return None
    end_val = ext[a] + m * (n - 1 - a)
    return {"a": a, "b": b, "m": m, "touches": t2, "violations": viol, "end": end_val}


def find(bars: pd.DataFrame, k: int = 5, per_side: int = 2) -> dict:
    """Trendlines over `bars` (open/high/low/close/atr, any interval). Times are the bars' index values."""
    if len(bars) < 40:
        return {"lines": [], "pattern": None, "method": __doc__}
    h, lo, c = (bars[x].to_numpy(float) for x in ("high", "low", "close"))
    atr = bars["atr"].to_numpy(float) if "atr" in bars else np.full(len(c), np.nan)
    med = np.nanmedian(atr) if np.isfinite(atr).any() else np.nanmedian(h - lo)
    atr = np.where(np.isfinite(atr), atr, med)
    n = len(c)
    out = []
    for kind, ext, piv in (("support", lo, _pivots(lo, k, "low")), ("resistance", h, _pivots(h, k, "high"))):
        cands = []
        for i, a in enumerate(piv):
            for b in piv[i + 1:]:
                if b - a < MIN_GAP:
                    continue
                ev = _evaluate(a, b, piv, ext, c, atr, kind)
                if not ev:
                    continue
                slope_pct_bar = ev["m"] / c[-1] * 100
                if abs(slope_pct_bar) > 1.2:     # steeper than ~25% a month on daily bars: not a usable trendline
                    continue
                if kind == "support" and c[-1] < ev["end"] - TOL_ATR * atr[-1]:
                    continue
                if kind == "resistance" and c[-1] > ev["end"] + TOL_ATR * atr[-1]:
                    continue
                span = n - 1 - ev["a"]
                dist_atr = abs(c[-1] - ev["end"]) / atr[-1]
                if ev["end"] <= 0 or dist_atr > 6 or abs(c[-1] / ev["end"] - 1) > 0.15:
                    continue   # unbroken but far from price: not a line the market is trading against now
                last_touch_ago = n - 1 - ev["touches"][-1]
                score = (min(len(ev["touches"]), 6) * 13 + min(math.log2(max(span, 10) / 10), 4) * 6
                         + (10 if last_touch_ago <= 15 else 5 if last_touch_ago <= 40 else 0)
                         - ev["violations"] * 12 - min(dist_atr, 8) * 2.5)
                cands.append({**ev, "kind": kind, "span": span, "dist_atr": dist_atr, "last_touch_ago": last_touch_ago,
                              "score": score, "slope_pct_bar": slope_pct_bar})
        cands.sort(key=lambda x: -x["score"])
        kept: list[dict] = []
        for cd in cands:
            dup = any(abs(cd["end"] - kp["end"]) < 0.8 * atr[-1] and abs(cd["m"] - kp["m"]) < max(abs(kp["m"]) * 0.5, 0.02 * atr[-1])
                      for kp in kept)
            if not dup and cd["score"] >= 35:
                kept.append(cd)
            if len(kept) >= per_side:
                break
        out += kept

    idx = bars.index
    lines = []
    for ln in out:
        a, m = ln["a"], ln["m"]
        val = lambda x: float((lo if ln["kind"] == "support" else h)[a] + m * (x - a))  # noqa: E731
        conf = len(ln["touches"]) >= 3
        direction = "상승" if ln["slope_pct_bar"] > 0.03 else "하락" if ln["slope_pct_bar"] < -0.03 else "수평"
        name = f"{direction} {'지지' if ln['kind'] == 'support' else '저항'}선"
        dist_pct = (c[-1] / ln["end"] - 1) * 100
        lines.append({
            "kind": ln["kind"], "name": name, "confirmed": conf, "touches": len(ln["touches"]),
            "violations": ln["violations"], "span_bars": ln["span"], "score": round(float(np.clip(ln["score"], 0, 100)), 1),
            "start": {"time": _t(idx[a]), "value": val(a)}, "end": {"time": _t(idx[n - 1]), "value": val(n - 1)},
            "touch_points": [{"time": _t(idx[p]), "value": val(p)} for p in ln["touches"]],
            "slope_pct_per_20": round(ln["slope_pct_bar"] * 20, 2), "distance_pct": round(dist_pct, 2),
            "last_touch_bars_ago": ln["last_touch_ago"],
            "note": (f"{len(ln['touches'])}번 닿고 지켜짐" + (f" · 이탈 {ln['violations']}회 후 회복" if ln["violations"] else "")
                     + f" · 마지막 터치 {ln['last_touch_ago']}봉 전 · 현재가가 선보다 {abs(dist_pct):.1f}% {'위' if dist_pct >= 0 else '아래'}"),
        })
    lines.sort(key=lambda x: -x["score"])
    return {"lines": lines, "pattern": _pattern(lines, c[-1]), "method": __doc__}


def _t(ts) -> str | int:
    if getattr(ts, "tzinfo", None) is not None:
        return int(pd.Timestamp(ts).timestamp())
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _pattern(lines: list[dict], price: float) -> dict | None:
    """Name the shape made by the best support and the best resistance, when they overlap in time."""
    s = next((x for x in lines if x["kind"] == "support"), None)
    r = next((x for x in lines if x["kind"] == "resistance"), None)
    if not s or not r:
        return None
    ss, rs = s["slope_pct_per_20"], r["slope_pct_per_20"]
    flat = 1.0   # % per 20 bars
    gap_end = r["end"]["value"] - s["end"]["value"]
    if gap_end <= 0:
        return None
    gap_now_pct = gap_end / price * 100
    conv = rs - ss    # negative = lines approaching each other
    if abs(ss) < flat and abs(rs) < flat:
        name, bias = "횡보 박스", "중립"
    elif abs(conv) < 0.6 and ss * rs > 0:
        name, bias = ("상승 채널", "상승 추세 유지") if ss > 0 else ("하락 채널", "하락 추세 유지")
    elif conv < 0:
        if abs(rs) < flat and ss > flat:
            name, bias = "상승 삼각형", "위쪽 돌파 쪽으로 기울어진 모양"
        elif abs(ss) < flat and rs < -flat:
            name, bias = "하락 삼각형", "아래쪽 이탈 쪽으로 기울어진 모양"
        elif ss > 0 and rs < 0:
            name, bias = "대칭 삼각형", "방향 미정 - 돌파 방향을 따름"
        elif ss > 0 and rs > 0:
            name, bias = "상승 쐐기", "상승이 둔해지는 모양 (아래 이탈 경계)"
        else:
            name, bias = "하락 쐐기", "하락이 둔해지는 모양 (위 돌파 가능성)"
    else:
        name, bias = "확산형", "변동성이 커지는 모양"
    return {"name": name, "bias": bias, "support": s["name"], "resistance": r["name"], "width_pct": round(gap_now_pct, 2),
            "touches": s["touches"] + r["touches"],
            "note": f"{s['name']}({s['touches']}회) · {r['name']}({r['touches']}회) 사이 폭 {gap_now_pct:.1f}%"}
