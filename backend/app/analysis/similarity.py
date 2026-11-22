"""Historical analog analysis: "when did this chart look like today, and what happened next?"

Method
1. Feature vector per day (all causal): RSI, distance from SMA50/SMA200, SMA20/SMA60 spread,
   SMA50 20-day slope, 20D/60D momentum, 5D/50D volume ratio, volatility vs its 1-year median,
   distance from the 52-week high and (optionally) the market-regime proxy of the benchmark index.
2. Robust z-scores (median / IQR over the candidate window) -> weighted RMS distance to today.
3. Candidates: days inside the lookback window that are at least `embargo` bars before today
   (so the current episode cannot match itself). Matches are picked greedily by distance with a
   minimum separation (de-clustering) so one episode is not counted many times.
4. Outcome for horizon h uses only matches with t + h <= today: every outcome is realised data.
5. Results are compared with the unconditional base rate over the same window and carry a
   Wilson 95% interval; small samples are flagged "insufficient sample size".
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

FEATURES: dict[str, tuple[str, float]] = {
    # key: (label, weight)
    "rsi": ("RSI(14)", 1.0),
    "d_sma200": ("주가 / SMA200", 1.0),
    "d_sma50": ("주가 / SMA50", 0.7),
    "spread_20_60": ("SMA20 / SMA60", 0.7),
    "slope_sma50": ("SMA50 20일 기울기", 0.7),
    "mom20": ("20일 모멘텀", 1.0),
    "mom60": ("60일 모멘텀", 0.8),
    "vol_ratio": ("거래량 5일 / 50일", 0.6),
    "volatility_rel": ("변동성 / 1년 중앙값", 0.6),
    "d_hi52": ("52주 고점 대비", 0.8),
    "mkt_regime": ("지수가 200일선 위 (벤치마크)", 0.6),
}
HORIZONS = (5, 10, 20, 60)


def feature_frame(ind: pd.DataFrame, benchmark_close: pd.Series | None = None) -> pd.DataFrame:
    f = pd.DataFrame(index=ind.index)
    c = ind["close"]
    f["rsi"] = ind["rsi"]
    f["d_sma200"] = c / ind["sma200"] - 1
    f["d_sma50"] = c / ind["sma50"] - 1
    f["spread_20_60"] = ind["sma20"] / ind["sma60"] - 1
    f["slope_sma50"] = ind["sma50"] / ind["sma50"].shift(20) - 1
    f["mom20"] = ind["mom20"]
    f["mom60"] = ind["mom60"]
    f["vol_ratio"] = ind["volume"].rolling(5).mean() / ind["vol_avg50"]
    f["volatility_rel"] = ind["vol20"] / ind["vol20"].rolling(252, min_periods=120).median()
    f["d_hi52"] = c / ind["hi52"] - 1
    if benchmark_close is not None and len(benchmark_close.dropna()) > 220:
        b = benchmark_close.reindex(ind.index).ffill()
        f["mkt_regime"] = (b > b.rolling(200, min_periods=200).mean()).astype(float)
        f.loc[b.rolling(200, min_periods=200).mean().isna(), "mkt_regime"] = np.nan
    return f


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def find_analogs(ind: pd.DataFrame, *, benchmark_close: pd.Series | None = None, lookback_years: float = 3.0,
                 max_matches: int = 30, max_distance: float = 1.0, min_separation: int = 10, embargo: int = 20,
                 min_sample: int = 10) -> dict:
    feats = feature_frame(ind, benchmark_close)
    cols = [k for k in FEATURES if k in feats and feats[k].notna().iloc[-1:].all()]
    price = ind["adj_close"] if "adj_close" in ind else ind["close"]
    T = len(ind) - 1
    lb = int(round(252 * lookback_years))
    start = max(0, T - lb)
    window = feats.iloc[start:T + 1][cols]
    valid = window.dropna()
    if len(valid) < 120 or not cols:
        return {"status": "insufficient_history", "message": "유사 국면 분석에 필요한 가격 이력이 부족합니다",
                "n_matches": 0}

    med = valid.median()
    iqr = (valid.quantile(0.75) - valid.quantile(0.25)).replace(0, np.nan)
    scale = (iqr / 1.349).fillna(valid.std()).replace(0, 1.0)
    z = (valid - med) / scale
    today = z.iloc[-1]
    w = np.array([FEATURES[c][1] for c in cols])
    w = w / w.sum()
    pos_of = {ts: i for i, ts in enumerate(ind.index)}
    cand = z.iloc[:-1]
    cand = cand[[pos_of[t] <= T - embargo for t in cand.index]]
    dist = np.sqrt(((cand - today) ** 2 * w).sum(axis=1))
    order = dist.sort_values()

    chosen: list[tuple[pd.Timestamp, float]] = []
    for ts, d in order.items():
        if d > max_distance:
            break
        p = pos_of[ts]
        if all(abs(p - pos_of[c]) >= min_separation for c, _ in chosen):
            chosen.append((ts, float(d)))
        if len(chosen) >= max_matches:
            break

    pv = price.to_numpy(float)
    matches = []
    for ts, d in sorted(chosen, key=lambda x: x[0]):
        p = pos_of[ts]
        fwd = {f"{h}d": (pv[p + h] / pv[p] - 1) if p + h <= T else None for h in HORIZONS}
        matches.append({"date": str(ts.date()), "distance": round(d, 3),
                        "features": {c: round(float(feats.at[ts, c]), 4) for c in cols}, "forward": fwd})

    # Base rate: every day in the window whose outcome is realised.
    base_idx = [pos_of[t] for t in valid.index[:-1]]
    horizons = {}
    for h in HORIZONS:
        rets = np.array([m["forward"][f"{h}d"] for m in matches if m["forward"][f"{h}d"] is not None])
        base = np.array([pv[p + h] / pv[p] - 1 for p in base_idx if p + h <= T])
        n = len(rets)
        k = int((rets > 0).sum())
        horizons[f"{h}d"] = {
            "horizon": h, "n": n, "positive": k, "negative": int((rets <= 0).sum()),
            "win_rate": (k / n) if n else None, "win_rate_ci95": wilson(k, n),
            "median_return": float(np.median(rets)) if n else None,
            "mean_return": float(rets.mean()) if n else None,
            "p25": float(np.quantile(rets, 0.25)) if n else None,
            "p75": float(np.quantile(rets, 0.75)) if n else None,
            "worst": float(rets.min()) if n else None, "best": float(rets.max()) if n else None,
            "base_win_rate": float((base > 0).mean()) if len(base) else None,
            "base_median_return": float(np.median(base)) if len(base) else None,
            "sufficient": n >= min_sample,
        }
    current = {c: {"label": FEATURES[c][0], "value": float(feats[c].iloc[-1])} for c in cols}
    n_total = len(matches)
    return {
        "status": "ok" if n_total >= min_sample else "insufficient_sample",
        "message": None if n_total >= min_sample else
        f"표본 부족(insufficient sample size): 독립적인 유사 사례가 {n_total}건뿐입니다 ({min_sample}건 이상 필요)",
        "as_of": str(ind.index[-1].date()), "lookback_years": lookback_years,
        "window_start": str(valid.index[0].date()), "n_matches": n_total, "matches": matches,
        "horizons": horizons, "current_features": current,
        "method": {"distance": "weighted RMS of robust z-scores", "max_distance": max_distance,
                   "min_separation_days": min_separation, "embargo_days": embargo, "min_sample": min_sample,
                   "weights": {c: round(float(x), 3) for c, x in zip(cols, w)}},
        "disclaimer": "과거 결과의 빈도일 뿐이며, 미래 수익률의 예측이나 보장이 아닙니다.",
    }
