"""Do analyst signals predict returns? Monthly panel study, US and Korea separately.

    cd backend && .venv/bin/python -m tools.analyst_event_study [--refresh]

Data: US - every S&P 500 member's rating / price-target actions from Yahoo (upgrades_downgrades);
      KR - every KOSPI 100 / KOSDAQ 50 member's broker reports from Naver research (target price, rating).
      Prices from the app database.
At each month-end, for each stock (only what was public by that day):
  * consensus upside: mean of each firm's latest target (within 365 days, >= 3 firms) / close - 1
  * revision breadth: (raises - cuts) / (raises + cuts) of target changes in the last 90 days (>= 3 changes)
  * rating change: net upgrades - downgrades in the last 30 days
Forward return: buy at the next day's open, hold 20 / 60 trading days, minus the median stock of that market
over the same window (excess). Points use the same formula as the timing rules:
4 × 60-day median excess % × share of years with the same sign, capped at ±20; kept when the 20- and 60-day
medians agree in sign, the bucket is large enough and the result is at least 3 points.
Raw downloads are cached in data/ so a re-run only needs --refresh for new data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import universe  # noqa: E402
from tools.timing_event_study import load  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
NAVER = "https://stock.naver.com/api/stockSecurity/researches/v2/company"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Referer": "https://stock.naver.com/research/company"}
OPINION_RANK = {"strongbuy": 3, "buy": 2, "outperform": 2, "hold": 1, "neutral": 1, "marketperform": 1, "reduce": 0,
                "sell": 0, "underperform": 0, "strongsell": 0}


# ------------------------------------------------------------------ downloads
def fetch_us(symbols: list[str]) -> pd.DataFrame:
    import yfinance as yf

    def one(s: str) -> pd.DataFrame | None:
        for _ in range(3):
            try:
                ud = yf.Ticker(s).upgrades_downgrades
                if ud is None or ud.empty:
                    return None
                d = ud.reset_index().rename(columns={"GradeDate": "date", "Firm": "firm", "Action": "action",
                                                     "priceTargetAction": "pt_action", "currentPriceTarget": "pt",
                                                     "priorPriceTarget": "pt_prior", "ToGrade": "to", "FromGrade": "from"})
                d["symbol"] = s
                return d[["symbol", "date", "firm", "action", "pt_action", "pt", "pt_prior", "to", "from"]]
            except Exception:
                time.sleep(2)
        return None
    with ThreadPoolExecutor(max_workers=4) as ex:
        parts = [p for p in ex.map(one, symbols) if p is not None]
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None)
    return out


def fetch_kr(symbols: list[str], since: str) -> pd.DataFrame:
    import httpx
    c = httpx.Client(headers=UA, timeout=20)
    rows = []
    for s in symbols:
        code = s.split(".")[0]
        for idx in range(0, 40):
            try:
                j = c.get(NAVER, params=[("itemCodes", code), ("size", "50"), ("index", str(idx))]).json()
            except Exception:
                time.sleep(2)
                continue
            items = j.get("items") or []
            for it in items:
                rows.append({"symbol": s, "date": it.get("writeDate"), "firm": it.get("brokerName"), "nid": it.get("nid"),
                             "pt": float(it["goalPrice"]) if (it.get("goalPrice") or "").replace(".", "").isdigit() else np.nan,
                             "opinion": (it.get("opinionType") or "").lower()})
            if not items or not j.get("hasNext") or (items and items[-1].get("writeDate", "9") < since):
                break
            time.sleep(0.15)
    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"])
    return out


def kr_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Naver reports -> per-report target-price and rating changes vs the same broker's previous report."""
    df = df.dropna(subset=["firm"]).sort_values(["symbol", "firm", "date"])
    g = df.groupby(["symbol", "firm"])
    prev_pt, prev_op = g["pt"].shift(1), g["opinion"].shift(1)
    df["pt_action"] = np.where(df["pt"] > prev_pt * 1.001, "Raises", np.where(df["pt"] < prev_pt * 0.999, "Lowers", ""))
    r_now, r_prev = df["opinion"].map(OPINION_RANK), prev_op.map(OPINION_RANK)
    df["action"] = np.where(r_now > r_prev, "up", np.where(r_now < r_prev, "down", "main"))
    return df


# ------------------------------------------------------------------ panel
def panel(actions: pd.DataFrame, prices: dict[str, pd.DataFrame], start: str) -> pd.DataFrame:
    rows = []
    acts = {s: g.sort_values("date") for s, g in actions.groupby("symbol")}
    for s, px in prices.items():
        px = px.dropna(subset=["open", "close"])
        if len(px) < 120:
            continue
        idx = px.index
        month_ends = idx.to_series().groupby([idx.year, idx.month]).max()
        a = acts.get(s)
        for d in month_ends[month_ends >= pd.Timestamp(start)]:
            i = idx.get_loc(d)
            if i + 61 >= len(idx):
                continue
            row = {"symbol": s, "date": d, "close": px["close"].iloc[i], "entry": px["open"].iloc[i + 1],
                   "exit20": px["close"].iloc[i + 20], "exit60": px["close"].iloc[i + 60]}
            if a is not None:
                upto = a[a["date"] <= d + pd.Timedelta(hours=23, minutes=59)]
                recent = upto[upto["date"] > d - pd.Timedelta(days=365)]
                pts = recent[recent["pt"] > 0].groupby("firm")["pt"].last()
                row["n_firms"] = len(pts)
                row["upside"] = pts.mean() / row["close"] - 1 if len(pts) >= 3 else np.nan
                r90 = upto[upto["date"] > d - pd.Timedelta(days=90)]
                ups, downs = (r90["pt_action"] == "Raises").sum(), (r90["pt_action"] == "Lowers").sum()
                row["breadth"] = (ups - downs) / (ups + downs) if ups + downs >= 3 else np.nan
                r30 = upto[upto["date"] > d - pd.Timedelta(days=30)]
                row["net_rating"] = (r30["action"] == "up").sum() - (r30["action"] == "down").sum()
            rows.append(row)
    p = pd.DataFrame(rows)
    for h in (20, 60):
        p[f"ret{h}"] = p[f"exit{h}"] / p["entry"] - 1
        p[f"ex{h}"] = (p[f"ret{h}"] - p.groupby("date")[f"ret{h}"].transform("median")) * 100
    return p


BUCKETS = {
    "upside_neg": ("평균 목표가가 현재가보다 낮음", lambda p: p["upside"] < 0),
    "upside_0_10": ("목표가 상승여력 0~10%", lambda p: (p["upside"] >= 0) & (p["upside"] < 0.10)),
    "upside_10_20": ("목표가 상승여력 10~20%", lambda p: (p["upside"] >= 0.10) & (p["upside"] < 0.20)),
    "upside_20_35": ("목표가 상승여력 20~35%", lambda p: (p["upside"] >= 0.20) & (p["upside"] < 0.35)),
    "upside_35_plus": ("목표가 상승여력 35% 이상", lambda p: p["upside"] >= 0.35),
    "rev_up": ("최근 90일 목표가 대부분 상향", lambda p: p["breadth"] >= 0.5),
    "rev_mixed": ("최근 90일 목표가 상향·하향 혼재", lambda p: (p["breadth"] > -0.5) & (p["breadth"] < 0.5)),
    "rev_down": ("최근 90일 목표가 대부분 하향", lambda p: p["breadth"] <= -0.5),
    "upgrade_30": ("최근 30일 투자의견 상향", lambda p: p["net_rating"] > 0),
    "downgrade_30": ("최근 30일 투자의견 하향", lambda p: p["net_rating"] < 0),
}


def report(p: pd.DataFrame, min_n: int) -> dict:
    out = {}
    for key, (label, f) in BUCKETS.items():
        sub = p[f(p).fillna(False)] if key in BUCKETS else p
        n = len(sub)
        if n == 0:
            continue
        m20, m60 = float(sub["ex20"].median()), float(sub["ex60"].median())
        years = sub.groupby(sub["date"].dt.year)["ex20"].median()
        same = float((np.sign(years) == np.sign(m20)).mean()) if len(years) else 0.0
        pts = float(np.clip(4 * m60 * same, -20, 20))
        keep = n >= min_n and np.sign(m20) == np.sign(m60) and abs(pts) >= 3
        out[key] = {"label": label, "n": n, "ex20": m20, "ex60": m60, "years": f"{int((np.sign(years) == np.sign(m20)).sum())}/{len(years)}",
                    "points": round(pts) if keep else 0, "kept": bool(keep)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    cons = universe.constituents()
    res = {}
    us_file, kr_file = DATA / "analyst_actions_us.csv.gz", DATA / "analyst_reports_kr.csv.gz"
    if args.refresh or not us_file.exists():
        fetch_us(list(cons["SP500"])).to_csv(us_file, index=False)
    if args.refresh or not kr_file.exists():
        fetch_kr(list(cons["KOSPI"]) + list(cons["KOSDAQ"]), "2023-06-01").to_csv(kr_file, index=False)
    us = pd.read_csv(us_file, parse_dates=["date"])
    kr = kr_changes(pd.read_csv(kr_file, parse_dates=["date"]))
    for market, acts, start, min_n in (("US", us, "2022-07-01", 1000), ("KR", kr, "2024-07-01", 300)):
        prices = load(sorted(acts["symbol"].unique()), "2022-01-01" if market == "US" else "2023-06-01")
        p = panel(acts, prices, start)
        res[market] = {"symbols": int(p["symbol"].nunique()), "rows": len(p), "from": str(p["date"].min().date()),
                       "to": str(p["date"].max().date()), "coverage": float(p["upside"].notna().mean()), "buckets": report(p, min_n)}
        print(f"\n== {market}: {res[market]['symbols']} stocks, {len(p)} stock-months {res[market]['from']} ~ {res[market]['to']}, "
              f"consensus coverage {res[market]['coverage'] * 100:.0f}%")
        for k, b in res[market]["buckets"].items():
            print(f"  {k:15} n={b['n']:6} 20d {b['ex20']:+.2f}% 60d {b['ex60']:+.2f}% years {b['years']} -> {b['points']:+d} {'KEEP' if b['kept'] else ''}")
    (DATA / "analyst_study.json").write_text(json.dumps({"run": dt.date.today().isoformat(), **res}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
