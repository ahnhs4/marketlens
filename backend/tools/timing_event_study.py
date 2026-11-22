"""Event study behind analysis/timing_evidence.py: which timing rules predicted forward returns?

Usage (from backend/):  .venv/bin/python tools/timing_event_study.py


Read-only on the app database. For every stock-day where a rule fires, the forward return is
measured from the NEXT day's open to the close h days later, minus the median stock over the
same window. Reported per rule: cases, 20/60-day median excess %, and how many calendar years
the 20-day median had the same sign. timing_evidence.py turns these into points.
"""
from __future__ import annotations

import sys
import warnings

import numpy as np
import pandas as pd
from sqlalchemy import select

warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from app.analysis import indicators as I  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import DailyPrice  # noqa: E402

H = (5, 20, 60)


def load(symbols: list[str], start: str) -> dict[str, pd.DataFrame]:
    rows = []
    with SessionLocal() as db:
        for i in range(0, len(symbols), 900):
            part = symbols[i:i + 900]
            rows += db.execute(select(DailyPrice.symbol, DailyPrice.date, DailyPrice.open, DailyPrice.high, DailyPrice.low,
                                      DailyPrice.close, DailyPrice.volume)
                               .where(DailyPrice.symbol.in_(part), DailyPrice.date >= pd.Timestamp(start).date())).all()
    df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return {s: g.set_index("date").sort_index() for s, g in df.groupby("symbol")}


def first(x: pd.Series) -> pd.Series:
    return x & ~x.shift(1, fill_value=False)


def features(d: pd.DataFrame) -> pd.DataFrame | None:
    d = d.dropna(subset=["close", "high", "low"])
    if len(d) < 320:
        return None
    c, h, lo, o = d["close"], d["high"], d["low"], d["open"]
    f = pd.DataFrame(index=d.index)
    rsi14, rsi2 = I.rsi(c, 14), I.rsi(c, 2)
    sma = {p: c.rolling(p, min_periods=p).mean() for p in (5, 20, 50, 60, 200)}
    st = {hz: I.stochastic_slow(h, lo, c, *I.STOCH_SETS[hz]) for hz in ("short", "medium", "long")}
    bb = I.bollinger(c)
    hist = I.macd(c)["macd_hist"]
    hi52 = h.rolling(252, min_periods=252).max()
    gap52 = (c / hi52 - 1) * 100
    above200 = c > sma[200]
    ext20 = (c / sma[20] - 1) * 100

    # ---------------- forward returns: next open -> close h days later
    entry = o.shift(-1)
    for n in H:
        f[f"fwd{n}"] = c.shift(-n) / entry - 1
    # cross-sectional factors (ranked later per date)
    f["mom12_1"] = c.shift(21) / c.shift(252) - 1
    f["near52"] = c / hi52
    f["ret5"] = c / c.shift(5) - 1
    f["ret20"] = c / c.shift(20) - 1

    ev: dict[str, pd.Series] = {}
    ev["12-1개월 수익률 30~40%"] = (f["mom12_1"] >= 0.30) & (f["mom12_1"] < 0.40)
    ev["12-1개월 수익률 ≥ 40%"] = f["mom12_1"] >= 0.40
    # overbought states
    ev["RSI14 ≥ 70 (과매수)"] = rsi14 >= 70
    ev["RSI14 ≥ 80"] = rsi14 >= 80
    ev["스토캐스틱 단기·중기 ≥ 80"] = (st["short"][0] >= 80) & (st["medium"][0] >= 80)
    ev["20일선 +8% 이상 이격"] = ext20 > 8
    ev["볼린저 상단 돌파 (%b > 1)"] = bb["bb_pctb"] > 1
    # oversold states
    ev["RSI14 ≤ 30 (과매도, 전체)"] = rsi14 <= 30
    ev["RSI14 ≤ 30 + 200일선 위"] = (rsi14 <= 30) & above200
    ev["RSI14 ≤ 30 + 200일선 아래"] = (rsi14 <= 30) & ~above200
    ev["RSI(2) < 10 + 200일선 위 (Connors)"] = (rsi2 < 10) & above200
    ev["스토캐스틱 중기/장기 ≤ 20"] = (st["medium"][0] <= 20) | (st["long"][0] <= 20)
    ev["볼린저 하단 이탈 (%b < 0)"] = bb["bb_pctb"] < 0
    # turns (first day only)
    for hz, nm in (("short", "단기"), ("medium", "중기"), ("long", "장기")):
        k, dd = st[hz]
        ev[f"{nm} 스토캐스틱 과매도권 상승 전환"] = (k.shift(1) <= dd.shift(1)) & (k > dd) & (np.minimum(k, k.shift(1)) <= 30)
    ev["MACD 시그널 상향 돌파"] = (hist.shift(1) < 0) & (hist >= 0)
    below10 = (c < sma[20]).rolling(10).sum().shift(1)
    ev["20일선 회복 (조정 후)"] = (c > sma[20]) & (c.shift(1) <= sma[20].shift(1)) & (below10 >= 5)
    # crosses (first day)
    for a, b, lag, ahead in ((5, 20, 3, 5), (20, 60, 3, 5), (50, 200, 5, 10)):
        diff = sma[a] - sma[b]
        ev[f"골든크로스 {a}/{b} (당일)"] = (diff.shift(1) <= 0) & (diff > 0)
        ev[f"데드크로스 {a}/{b} (당일)"] = (diff.shift(1) >= 0) & (diff < 0)
        gp = (sma[a] / sma[b] - 1) * 100
        sl = (gp - gp.shift(lag)) / lag
        ev[f"골든크로스 임박 {a}/{b}"] = first((gp < 0) & (gp >= -3) & (sl > 0) & (-gp / sl <= ahead))
    # proximity to the 52-week high (states)
    ev["52주 고점 0~2% 아래"] = gap52 >= -2
    ev["52주 고점 2~5% 아래"] = (gap52 < -2) & (gap52 >= -5)
    ev["52주 고점 5~8% 아래"] = (gap52 < -5) & (gap52 >= -8)
    ev["52주 고점 8~15% 아래"] = (gap52 < -8) & (gap52 >= -15)
    ev["52주 고점 15~30% 아래"] = (gap52 < -15) & (gap52 >= -30)
    ev["52주 고점 30% 이상 아래"] = gap52 < -30

    # ---------------- approximate app timing score (same point rules, no divergence / multi-year high)
    pts = pd.Series(0.0, index=d.index)
    pts += np.select([rsi14 >= 80, rsi14 >= 70, rsi14 >= 65, rsi14 <= 30, rsi14 <= 35],
                     [-30, -22, -10, np.where(above200, 18, 8), np.where(above200, 8, 3)], 0)
    sk, mk, lk = st["short"][0], st["medium"][0], st["long"][0]
    pts += np.where((sk >= 80) & (mk >= 80), -12, np.where(sk >= 85, -5, 0))
    pts += np.where((mk <= 20) | (lk <= 20), 8, 0)
    pts += np.select([ext20 > 15, ext20 > 8], [-15, -8], 0)
    pts += np.select([gap52 >= -2, gap52 >= -5, gap52 >= -8], [-14, -8, -4], 0)
    pts += np.select([bb["bb_pctb"] > 1, bb["bb_pctb"] < 0], [-6, 6], 0)
    turn = pd.Series(0.0, index=d.index)
    for hz, p in (("short", 6), ("medium", 10), ("long", 14)):
        k, dd = st[hz]
        x = ((k.shift(1) <= dd.shift(1)) & (k > dd) & (np.minimum(k, k.shift(1)) <= 30)).astype(float)
        recent = x.rolling(3, min_periods=1).max()
        turn = np.maximum(turn, recent * p)
    pts += turn
    rising3 = (hist < 0) & (hist > hist.shift(1)) & (hist.shift(1) > hist.shift(2)) & (hist.shift(2) > hist.shift(3))
    crossed5 = ((hist.shift(1) < 0) & (hist >= 0)).astype(float).rolling(5, min_periods=1).max() > 0
    pts += np.where(rising3, 8, 0) + np.where(crossed5 & (hist > 0), 10, 0)
    pts += np.where(ev["20일선 회복 (조정 후)"].astype(float).rolling(3, min_periods=1).max() > 0, 8, 0)
    for a, b, p in ((5, 20, 6), (20, 60, 10), (50, 200, 12)):
        g = ev[f"골든크로스 {a}/{b} (당일)"].astype(float)
        dth = ev[f"데드크로스 {a}/{b} (당일)"].astype(float)
        for lagd in range(0, 11):
            pts += g.shift(lagd, fill_value=0) * p * 1.3 * (1 - lagd / 20) - dth.shift(lagd, fill_value=0) * p * (1 - lagd / 20)
    f["timing"] = np.clip(50 + pts, 0, 100)
    for k_, v in ev.items():
        f[k_] = v.fillna(False).astype(bool)
    return f.iloc[260:]  # warm-up for 52-week / 200-day windows



def panel(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    for s, d in frames.items():
        x = features(d)
        if x is not None:
            x["symbol"] = s
            parts.append(x)
    p = pd.concat(parts)
    p.index.name = "date"
    p = p.reset_index()
    for n in H:
        p[f"m{n}"] = p[f"fwd{n}"] - p.groupby("date")[f"fwd{n}"].transform("median")
    p["year"] = p["date"].dt.year
    return p


def report(p: pd.DataFrame) -> pd.DataFrame:
    skip = {"date", "symbol", "timing", "mom12_1", "near52", "ret5", "ret20", "year"}
    rows = []
    yrs = sorted(p["year"].unique())
    for k in [c for c in p.columns if c not in skip and p[c].dtype == bool]:
        s = p[p[k]]
        yr = [s.loc[s["year"] == y, "m20"].median() for y in yrs if (s["year"] == y).sum() > 200]
        med20, med60 = s["m20"].median() * 100, s["m60"].median() * 100
        same = sum(np.sign(v) == np.sign(med20) for v in yr)
        pts = float(np.clip(4 * med60, -20, 20) * (same / len(yr) if yr else 0))
        keep = len(s) >= 2000 and (np.sign(med20) == np.sign(med60) or abs(med20) < 0.05) and abs(pts) >= 3
        rows.append({"rule": k, "n": len(s), "med20": med20, "med60": med60, "years_same": f"{same}/{len(yr)}",
                     "points": round(pts) if keep else 0})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    from app.services import trend_flow, universe

    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    for label, syms, start in (("US", universe.constituents()["SP500"], "2021-06-01"),
                               ("KR", [u["symbol"] for u in trend_flow.kr_universe()], "2023-06-01")):
        p = panel(load(syms, start))
        print(f"\n### {label}: {p['symbol'].nunique()} stocks, {p['date'].min().date()} ~ {p['date'].max().date()}")
        print(report(p).round(2).to_string(index=False))
