"""Chart-pattern detection on the raw price series (no image recognition).

All detectors are causal: an event is stamped on the bar at which it becomes
knowable (pivots need k bars of confirmation; breakouts need the close). This
lets the same code produce (a) the patterns active today and (b) the full
history of past occurrences used for the per-pattern historical success rate.

Event fields: pattern, direction (bullish/bearish/neutral), status (confirmed /
forming), idx (detection bar), start_idx, confidence (0-1), levels, description.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .levels import Pivot, find_pivots

FWD_HORIZON = 20


def _ev(pattern, direction, idx, start_idx, confidence, status="confirmed", levels=None, desc=""):
    return {"pattern": pattern, "direction": direction, "idx": int(idx), "start_idx": int(start_idx),
            "confidence": float(np.clip(confidence, 0.05, 0.95)), "status": status, "levels": levels or {},
            "description": desc}


def _breakouts(c, h, l, vr, hi55, lo55, adx) -> list[dict]:
    out = []
    n = len(c)
    for t in range(1, n):
        if np.isnan(hi55[t]) or np.isnan(hi55[t - 1]):
            continue
        rng = h[t] - l[t]
        pos = (c[t] - l[t]) / rng if rng > 0 else 0.5
        vol = 0 if np.isnan(vr[t]) else vr[t]
        if c[t] > hi55[t] and c[t - 1] <= hi55[t - 1]:
            conf = 0.40 + 0.25 * min(1, max(0, vol - 1)) + 0.15 * pos + (0.1 if adx[t] > 20 else 0)
            out.append(_ev("Breakout", "bullish", t, t - 55, conf, levels={"level": hi55[t], "volume_ratio": vol},
                           desc=f"종가 {c[t]:,.2f}가 55일 고점 {hi55[t]:,.2f} 상향 돌파, 거래량 20일 평균의 {vol:.1f}배"))
        if not np.isnan(lo55[t]) and not np.isnan(lo55[t - 1]) and c[t] < lo55[t] and c[t - 1] >= lo55[t - 1]:
            conf = 0.40 + 0.25 * min(1, max(0, vol - 1)) + 0.15 * (1 - pos) + (0.1 if adx[t] > 20 else 0)
            out.append(_ev("Breakdown", "bearish", t, t - 55, conf, levels={"level": lo55[t], "volume_ratio": vol},
                           desc=f"종가 {c[t]:,.2f}가 55일 저점 {lo55[t]:,.2f} 하향 이탈, 거래량 20일 평균의 {vol:.1f}배"))
    return out


def _doubles(c, h, l, vr, piv: list[Pivot], n: int) -> list[dict]:
    out = []
    for kind in ("L", "H"):
        pts = [p for p in piv if p.kind == kind]
        used_second: set[int] = set()
        for i, p1 in enumerate(pts):
            for p2 in pts[i + 1:i + 4]:
                span = p2.idx - p1.idx
                if span < 10 or span > 120 or p2.idx in used_second:
                    continue
                diff = abs(p2.price - p1.price) / p1.price
                if diff > 0.03:
                    continue
                if kind == "L":
                    neck = float(h[p1.idx:p2.idx + 1].max())
                    base = min(p1.price, p2.price)
                    height = neck / max(p1.price, p2.price) - 1
                    if height < 0.04:
                        continue
                    name, direction = "Double Bottom", "bullish"
                    broke = lambda t: c[t] > neck  # noqa: E731
                    failed = lambda t: c[t] < base * 0.97  # noqa: E731
                    target = neck + (neck - base)
                else:
                    neck = float(l[p1.idx:p2.idx + 1].min())
                    base = max(p1.price, p2.price)
                    height = 1 - neck / min(p1.price, p2.price)
                    if height < 0.04:
                        continue
                    name, direction = "Double Top", "bearish"
                    broke = lambda t: c[t] < neck  # noqa: E731
                    failed = lambda t: c[t] > base * 1.03  # noqa: E731
                    target = neck - (base - neck)
                conf = 0.45 + 0.15 * (1 - diff / 0.03) + 0.15 * min(1, height / 0.10)
                done = False
                for t in range(p2.confirm_idx, min(n, p2.idx + 40)):
                    if failed(t):
                        done = True
                        break
                    if broke(t):
                        vol = 0 if np.isnan(vr[t]) else vr[t]
                        out.append(_ev(name, direction, t, p1.idx, conf + 0.15 * min(1, max(0, vol - 1)),
                                       levels={"neckline": neck, "target": target, "first": p1.price,
                                               "second": p2.price},
                                       desc=f"두 {'저점' if kind == 'L' else '고점'} {p1.price:,.2f}/{p2.price:,.2f} "
                                            f"(차이 {diff:.1%}); 넥라인 {neck:,.2f} 돌파"))
                        done = True
                        break
                # "forming" only once price has moved a third of the way from the 2nd extreme to the neckline
                progressed = (c[n - 1] >= p2.price + (neck - p2.price) / 3) if kind == "L" else \
                    (c[n - 1] <= p2.price - (p2.price - neck) / 3)
                if not done and progressed and p2.confirm_idx <= n - 1 < p2.idx + 40:
                    out.append(_ev(name, direction, n - 1, p1.idx, conf * 0.6, status="forming",
                                   levels={"neckline": neck, "target": target, "first": p1.price, "second": p2.price},
                                   desc=f"형성 중: 넥라인 {neck:,.2f} {'상향 돌파' if kind == 'L' else '하향 이탈'} 대기"))
                used_second.add(p2.idx)
    return out


def _head_shoulders(c, h, l, piv: list[Pivot], n: int) -> list[dict]:
    out = []
    for kind in ("H", "L"):
        pts = [p for p in piv if p.kind == kind]
        for i in range(len(pts) - 2):
            a, b, d = pts[i], pts[i + 1], pts[i + 2]
            span = d.idx - a.idx
            if span < 15 or span > 160:
                continue
            r = (b.idx - a.idx) / max(d.idx - b.idx, 1)
            if not 0.4 <= r <= 2.5:
                continue
            if kind == "H":
                if b.price < max(a.price, d.price) * 1.03 or abs(a.price - d.price) / b.price > 0.06:
                    continue
                t1 = a.idx + int(np.argmin(l[a.idx:b.idx + 1]))
                t2 = b.idx + int(np.argmin(l[b.idx:d.idx + 1]))
                y1, y2 = l[t1], l[t2]
                name, direction = "Head & Shoulders", "bearish"
            else:
                if b.price > min(a.price, d.price) * 0.97 or abs(a.price - d.price) / b.price > 0.06:
                    continue
                t1 = a.idx + int(np.argmax(h[a.idx:b.idx + 1]))
                t2 = b.idx + int(np.argmax(h[b.idx:d.idx + 1]))
                y1, y2 = h[t1], h[t2]
                name, direction = "Inverse Head & Shoulders", "bullish"
            if t2 == t1:
                continue
            slope = (y2 - y1) / (t2 - t1)
            neck_at = lambda t: y1 + slope * (t - t1)  # noqa: E731
            head_h = abs(b.price - neck_at(b.idx))
            sym = 1 - abs(a.price - d.price) / b.price / 0.06
            conf = 0.5 + 0.2 * sym + 0.1 * min(1, head_h / b.price / 0.08)
            done = False
            for t in range(d.confirm_idx, min(n, d.idx + 40)):
                if (kind == "H" and c[t] > b.price) or (kind == "L" and c[t] < b.price):
                    done = True
                    break
                if (kind == "H" and c[t] < neck_at(t)) or (kind == "L" and c[t] > neck_at(t)):
                    target = neck_at(t) - head_h if kind == "H" else neck_at(t) + head_h
                    out.append(_ev(name, direction, t, a.idx, conf,
                                   levels={"neckline": float(neck_at(t)), "head": b.price, "target": float(target)},
                                   desc=f"어깨 {a.price:,.2f}/{d.price:,.2f}, 머리 {b.price:,.2f}; 넥라인 돌파"))
                    done = True
                    break
            nk = neck_at(n - 1)
            progressed = (c[n - 1] <= d.price - (d.price - nk) / 3) if kind == "H" else \
                (c[n - 1] >= d.price + (nk - d.price) / 3)
            if not done and progressed and d.confirm_idx <= n - 1 < d.idx + 40:
                out.append(_ev(name, direction, n - 1, a.idx, conf * 0.6, status="forming",
                               levels={"neckline": float(neck_at(n - 1)), "head": b.price},
                               desc=f"형성 중: 넥라인 {neck_at(n - 1):,.2f} 아직 미돌파"))
    return out


def _triangles(c, piv: list[Pivot], mom60, n: int) -> list[dict]:
    out = []
    last_emit = -999
    by_confirm = sorted(piv, key=lambda p: p.confirm_idx)
    for j, p in enumerate(by_confirm):
        t = p.confirm_idx
        if t >= n or t - last_emit < 15:
            continue
        window = [q for q in by_confirm[:j + 1] if q.idx >= t - 60]
        hs = [q for q in window if q.kind == "H"][-4:]
        ls = [q for q in window if q.kind == "L"][-4:]
        if len(hs) < 2 or len(ls) < 2:
            continue
        mean_px = np.mean([q.price for q in hs + ls])
        uh = np.polyfit([q.idx for q in hs], [q.price for q in hs], 1)
        ul = np.polyfit([q.idx for q in ls], [q.price for q in ls], 1)
        su, sl = uh[0] / mean_px * 100, ul[0] / mean_px * 100  # % per bar
        start = min(hs[0].idx, ls[0].idx)
        w0 = np.polyval(uh, start) - np.polyval(ul, start)
        w1 = np.polyval(uh, t) - np.polyval(ul, t)
        if w0 <= 0 or w1 <= 0 or w1 > 0.75 * w0:
            continue
        flat = 0.03
        if abs(su) < flat and sl > 0.05:
            name, direction = "Ascending Triangle", "bullish"
        elif su < -0.05 and abs(sl) < flat:
            name, direction = "Descending Triangle", "bearish"
        elif su < -0.03 and sl > 0.03:
            name = "Symmetrical Triangle"
            direction = "bullish" if (not np.isnan(mom60[start]) and mom60[start] > 0) else "bearish"
        else:
            continue
        conf = 0.45 + 0.05 * (len(hs) + len(ls) - 4) + 0.2 * (1 - w1 / w0)
        status = "confirmed"
        out.append(_ev(name, direction, t, start, conf, status=status,
                       levels={"upper": float(np.polyval(uh, t)), "lower": float(np.polyval(ul, t)),
                               "upper_slope_pct": float(su), "lower_slope_pct": float(sl)},
                       desc=f"고점 {len(hs)}개 / 저점 {len(ls)}개 수렴; 폭이 가격의 {w0 / c[t]:.1%} → {w1 / c[t]:.1%}로 축소"))
        last_emit = t
    return out


def _flags(c, h, l, vr, n: int) -> list[dict]:
    out = []
    skip_until = -1
    for t in range(25, n):
        if t < skip_until:
            continue
        found = None
        for p in range(t - 20, t - 4):
            if p < 16:
                continue
            seg_h, seg_l = h[p + 1:t], l[p + 1:t]
            if len(seg_h) < 4:
                continue
            lo_pole, hi_pole = c[p - 15:p].min(), c[p - 15:p].max()
            bull_pole = c[p] / lo_pole - 1
            bear_pole = 1 - c[p] / hi_pole
            half = len(seg_h) // 2
            r1 = seg_h[:half].max() - seg_l[:half].min() if half else 0
            r2 = seg_h[half:].max() - seg_l[half:].min()
            converging = half >= 2 and r1 > 1.4 * r2
            if bull_pole >= 0.10:
                height = c[p] - lo_pole
                if seg_h.max() - seg_l.min() <= 0.5 * height and seg_l.min() >= c[p] - 0.5 * height \
                        and c[t] > seg_h.max():
                    found = ("Pennant" if converging else "Bull Flag", "bullish", p, bull_pole, seg_h.max())
                    break
            if bear_pole >= 0.10:
                height = hi_pole - c[p]
                if seg_h.max() - seg_l.min() <= 0.5 * height and seg_h.max() <= c[p] + 0.5 * height \
                        and c[t] < seg_l.min():
                    found = ("Bear Pennant" if converging else "Bear Flag", "bearish", p, bear_pole, seg_l.min())
                    break
        if found:
            name, direction, p, pole, lvl = found
            vol = 0 if np.isnan(vr[t]) else vr[t]
            conf = 0.45 + 0.2 * min(1, pole / 0.25) + 0.2 * min(1, max(0, vol - 1))
            out.append(_ev(name, direction, t, p - 15, conf, levels={"breakout": float(lvl), "pole_pct": float(pole)},
                           desc=f"{pole:.0%} 급등(깃대) 후 {t - p - 1}일 횡보; {lvl:,.2f} 돌파"))
            skip_until = t + 10
    return out


def _cup_handle(c, h, l, piv: list[Pivot], n: int) -> list[dict]:
    out = []
    highs = [p for p in piv if p.kind == "H"]
    last_t = -1
    for i, a in enumerate(highs):
        for cc in highs[i + 1:]:
            span = cc.idx - a.idx
            if span < 30:
                continue
            if span > 200:
                break
            if not 0.95 * a.price <= cc.price <= 1.05 * a.price:
                continue
            seg = l[a.idx:cc.idx + 1]
            b_rel = int(np.argmin(seg))
            bottom = seg[b_rel]
            depth = (a.price - bottom) / a.price
            if not 0.12 <= depth <= 0.40 or not 0.25 <= b_rel / span <= 0.75:
                continue
            lower_third = bottom + (a.price - bottom) / 3
            if (seg < lower_third).mean() < 0.15:  # V-shape rather than a rounded cup
                continue
            rim = max(a.price, cc.price)
            for t in range(cc.confirm_idx, min(n, cc.idx + 31)):
                hl = l[cc.idx + 1:t + 1].min() if t > cc.idx else cc.price
                hdepth = (cc.price - hl) / cc.price
                if hl < bottom + (a.price - bottom) / 2 or hdepth > 0.15:
                    break
                if c[t] > rim and t - cc.idx >= 5 and hdepth >= 0.02 and t > last_t:
                    conf = 0.5 + 0.15 * (1 - abs(b_rel / span - 0.5) * 2) + 0.15 * min(1, depth / 0.3)
                    out.append(_ev("Cup & Handle", "bullish", t, a.idx, conf,
                                   levels={"rim": float(rim), "bottom": float(bottom),
                                           "target": float(rim + (rim - bottom))},
                                   desc=f"{span}일간 깊이 {depth:.0%}의 컵, 손잡이 {hdepth:.0%}; 컵 상단 {rim:,.2f} 돌파"))
                    last_t = t + 20
                    break
                if t == n - 1 and t - cc.idx >= 5 and 0.02 <= hdepth <= 0.15:
                    out.append(_ev("Cup & Handle", "bullish", t, a.idx, 0.35, status="forming",
                                   levels={"rim": float(rim), "bottom": float(bottom)},
                                   desc=f"형성 중: 손잡이 깊이 {hdepth:.0%}, 돌파 기준 {rim:,.2f}"))
    return out


def _ranges(c, h, l, adx, n: int, win: int = 40) -> list[dict]:
    out = []
    state = False
    for t in range(win, n):
        hh, ll = h[t - win + 1:t + 1].max(), l[t - win + 1:t + 1].min()
        width = (hh - ll) / c[t - win + 1:t + 1].mean()
        now = width <= 0.10 and (not np.isnan(adx[t]) and adx[t] < 20)
        if now and not state:
            out.append(_ev("Range", "neutral", t, t - win + 1, 0.5 + 0.3 * (1 - width / 0.10),
                           levels={"top": float(hh), "bottom": float(ll)},
                           desc=f"{win}일 박스권 {ll:,.2f}~{hh:,.2f} (폭 {width:.1%}), ADX {adx[t]:.0f}"))
        state = now
    return out


def _reversals(c, sma50, macd_hist, piv: list[Pivot], n: int) -> list[dict]:
    out = []
    lows = [p for p in piv if p.kind == "L"]
    highs = [p for p in piv if p.kind == "H"]
    for t in range(61, n):
        if np.isnan(sma50[t]) or np.isnan(sma50[t - 1]) or np.isnan(sma50[t - 40]):
            continue
        prior = c[t - 60:t - 5] < sma50[t - 60:t - 5]
        if c[t] > sma50[t] and c[t - 1] <= sma50[t - 1] and prior.mean() >= 0.75 and sma50[t - 5] < sma50[t - 40] \
                and macd_hist[t] > 0:
            kl = [p for p in lows if p.confirm_idx <= t][-2:]
            if len(kl) == 2 and kl[1].price > kl[0].price:
                out.append(_ev("Trend Reversal (bullish)", "bullish", t, t - 60, 0.55,
                               desc="하락 추세 후 SMA50 회복 + 저점 상승 + MACD 히스토그램 양전환"))
        if c[t] < sma50[t] and c[t - 1] >= sma50[t - 1] and (~prior).mean() >= 0.75 and sma50[t - 5] > sma50[t - 40] \
                and macd_hist[t] < 0:
            kh = [p for p in highs if p.confirm_idx <= t][-2:]
            if len(kh) == 2 and kh[1].price < kh[0].price:
                out.append(_ev("Trend Reversal (bearish)", "bearish", t, t - 60, 0.55,
                               desc="상승 추세 후 SMA50 이탈 + 고점 하락 + MACD 히스토그램 음전환"))
    return out


def scan(ind: pd.DataFrame, k: int = 5) -> list[dict]:
    """All pattern events over the full history (causal)."""
    c, h, l, o = (ind[x].to_numpy(dtype=float) for x in ("close", "high", "low", "open"))
    n = len(c)
    if n < 80:
        return []
    vr = ind["vol_ratio"].to_numpy(dtype=float)
    piv = find_pivots(h, l, k)
    ev: list[dict] = []
    ev += _breakouts(c, h, l, vr, ind["hi55_prev"].to_numpy(float), ind["lo55_prev"].to_numpy(float),
                     ind["adx"].to_numpy(float))
    ev += _doubles(c, h, l, vr, piv, n)
    ev += _head_shoulders(c, h, l, piv, n)
    ev += _triangles(c, piv, ind["mom60"].to_numpy(float), n)
    ev += _flags(c, h, l, vr, n)
    ev += _cup_handle(c, h, l, piv, n)
    ev += _ranges(c, h, l, ind["adx"].to_numpy(float), n)
    ev += _reversals(c, ind["sma50"].to_numpy(float), ind["macd_hist"].to_numpy(float), piv, n)
    for e in ev:
        e["date"] = str(ind.index[e["idx"]].date())
        e["start_date"] = str(ind.index[max(0, e["start_idx"])].date())
    return sorted(ev, key=lambda e: e["idx"])


def historical_stats(ind: pd.DataFrame, events: list[dict], horizon: int = FWD_HORIZON) -> dict[str, dict]:
    """Forward performance of past confirmed events: enter next open, exit `horizon` bars later at the close."""
    o, c = ind["open"].to_numpy(float), ind["close"].to_numpy(float)
    n = len(c)
    stats: dict[str, list[float]] = {}
    for e in events:
        if e["status"] != "confirmed" or e["direction"] == "neutral" or e["idx"] + horizon >= n:
            continue
        entry = o[e["idx"] + 1]
        r = c[e["idx"] + horizon] / entry - 1
        signed = r if e["direction"] == "bullish" else -r
        stats.setdefault(e["pattern"], []).append(signed)
    out = {}
    for name, rs in stats.items():
        arr = np.array(rs)
        out[name] = {"n": int(len(arr)), "success_rate": float((arr > 0).mean()) if len(arr) else None,
                     "avg_signed_return": float(arr.mean()), "median_signed_return": float(np.median(arr)),
                     "horizon": horizon, "sufficient": bool(len(arr) >= 5)}
    return out


def current_patterns(ind: pd.DataFrame, recent: int = 15) -> dict:
    events = scan(ind)
    n = len(ind)
    stats = historical_stats(ind, events)
    active = [e for e in events if e["idx"] >= n - 1 - recent]
    # keep only the most recent instance of each pattern
    latest: dict[str, dict] = {}
    for e in active:
        key = f"{e['pattern']}|{e['status']}"
        if key not in latest or e["idx"] > latest[key]["idx"]:
            latest[key] = e
    confirmed_dirs = {e["direction"] for e in latest.values() if e["status"] == "confirmed" and e["direction"] != "neutral"}
    out = []
    for e in sorted(latest.values(), key=lambda x: (-x["idx"], -x["confidence"])):
        if e["status"] == "forming" and confirmed_dirs and e["direction"] not in confirmed_dirs | {"neutral"}:
            continue  # a confirmed pattern outranks an opposite pattern that is merely forming
        if e["status"] == "forming" and f"{e['pattern']}|confirmed" in latest:
            continue  # already confirmed - the forming instance adds nothing
        st = stats.get(e["pattern"])
        e = {**e, "bars_ago": n - 1 - e["idx"], "historical": st}
        out.append(e)
    return {"active": out, "history_stats": stats, "events_total": len(events),
            "history_start": str(ind.index[0].date()), "history_end": str(ind.index[-1].date()),
            "recent_events": [{k: v for k, v in e.items() if k not in ("levels",)} for e in events[-25:]]}
