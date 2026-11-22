"""Do the KR fundamental score's metrics predict returns? Annual cross-sections, KOSPI and KOSDAQ separately.

    cd backend && .venv/bin/python -m tools.kr_factor_study [--refresh-prices]

Data: OpenDART annual main accounts (FY 2015-) for every company listed today; daily prices from Yahoo (10 years,
cached in data/kr_factor_prices.pkl). Each May 1 (a month after the March 31 deadline, which some years falls on a
weekend), using only annual reports filed by then (receipt date in rcept_no; later corrections drop the company that year):
  * metrics and scores exactly as the app computes them (kr_financials.metrics / score, percentiles inside that
    year's cross-section)
  * market cap then = split-adjusted close x today's shares, scaled by paid-in capital then / now when no split or
    bonus issue happened in between (capital moves with rights issues and conversions)
Forward return: total return (adjusted close) from the first May session to the next one, minus the median of the
same market that year. Statistic: Spearman rank IC per year, then mean, t-stat and share of positive years; and the
top-minus-bottom quintile excess return.
Limits: only companies listed today (delisted names are missing, which flatters low-quality stocks the least);
cash-flow and dividend metrics need full statements, which DART only serves per company, so they are not tested.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis import kr_financials as kf  # noqa: E402
from app.providers.dart import dart  # noqa: E402
from app.providers.yahoo import yahoo  # noqa: E402
from app.services import dart_fin  # noqa: E402
from app.services.store import clean  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
PRICES = DATA / "kr_factor_prices.pkl"
OUT = DATA / "kr_factor_study.json"
FIRST_FY = 2015


def ensure_annuals() -> None:
    """Annual main accounts back to FY 2015 for every company listed today (~26 calls per year)."""
    corps = dart_fin.corp_map()
    live = dart_fin.listed()
    codes = [c["corp_code"] for s, c in corps.items() if s in live]
    have = set(dart_fin._load_log())  # noqa: SLF001   periods already stored ("2023-11011")
    by_code = {c["corp_code"]: s for s, c in corps.items()}
    from app.db import SessionLocal
    from app.models import DartFinancial
    from app.services.store import upsert, utcnow
    for y in range(FIRST_FY, dt.date.today().year - 4):
        if f"{y}-11011" in have:
            continue
        n = 0
        for i in range(0, len(codes), 100):
            part = codes[i:i + 100]
            parsed = kf.parse_main(dart.multi_accounts(part, y, "11011"))
            recs = [{"corp_code": cc, "stock_code": by_code[cc], "year": y, "reprt": "11011", "kind": "main", "fs_div": d["fs"],
                     "rcept_no": d.get("rcept_no"), "data": clean(d), "fetched_at": utcnow()}
                    for cc in part if (d := parsed.get(by_code[cc]))]
            with SessionLocal() as db:
                upsert(db, DartFinancial, recs, ["corp_code", "year", "reprt", "kind"])
                db.commit()
            n += len(recs)
        log = dart_fin._load_log()  # noqa: SLF001
        log[f"{y}-11011"] = dt.datetime.now().timestamp()
        dart_fin.MARKET_LOG.write_text(json.dumps(log))
        print(f"FY{y}: {n} companies")


def load_prices(refresh: bool) -> dict[str, pd.DataFrame]:
    if PRICES.exists() and not refresh:
        return pickle.loads(PRICES.read_bytes())
    live = dart_fin.listed()
    syms = [dart_fin.symbol_of(c, r["market"]) for c, r in live.items()]
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(syms), 200):
        frames = yahoo.history_many(syms[i:i + 200], period="10y", chunk=100)
        for s, df in frames.items():
            out[s] = df[["close", "adj_close", "splits"]].astype("float32")
        print(f"prices {min(i + 200, len(syms))}/{len(syms)} ({len(out)} with data)")
    PRICES.write_bytes(pickle.dumps(out))
    return out


def _rcept_date(d: dict) -> dt.date | None:
    r = (d or {}).get("rcept_no") or ""
    try:
        return dt.date(int(r[:4]), int(r[4:6]), int(r[6:8]))
    except ValueError:
        return None


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 30:
        return None
    ra, rb = pd.Series(a[ok]).rank().to_numpy(), pd.Series(b[ok]).rank().to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1])


def run(refresh_prices: bool = False) -> dict:
    ensure_annuals()
    px = load_prices(refresh_prices)
    live = dart_fin.listed()
    corps = dart_fin.corp_map()
    cc_of = {s: c["corp_code"] for s, c in corps.items() if s in live}
    grouped = dart_fin._group(dart_fin._rows(list(cc_of.values()), ("main",)))  # noqa: SLF001
    today = dt.date.today()
    keys = [k for _, (_, _, items) in kf.PILLARS.items() for k, *_ in items] + list(kf.PILLARS) + ["total"]
    panel: list[dict] = []
    for t in range(FIRST_FY + 2, today.year):
        start, end = pd.Timestamp(t, 5, 1), pd.Timestamp(t + 1, 5, 1)
        if end.date() > today:
            continue
        cross = []
        for code, cc in cc_of.items():
            main = {k: v for k, v in (grouped.get(cc, {}).get("main") or {}).items()
                    if k[1] == "11011" and k[0] <= t - 1 and v and (_rcept_date(v) or dt.date(k[0] + 1, 3, 31)) < start.date()}
            if (t - 1, "11011") not in main:
                continue
            sym = dart_fin.symbol_of(code, live[code]["market"])
            df = px.get(sym)
            if df is None:
                continue
            a = df.loc[df.index >= start]
            b = df.loc[df.index >= end]
            if a.empty or b.empty or (a.index[0] - start).days > 10 or (b.index[0] - end).days > 10:
                continue
            c0, c1 = float(a["adj_close"].iloc[0]), float(b["adj_close"].iloc[0])
            close_now = live[code].get("close")
            cap_now = live[code].get("company_cap")
            if not (c0 > 0 and c1 > 0 and close_now and cap_now):
                continue
            shares_now = cap_now / close_now
            split_since = float(df.loc[df.index >= start, "splits"].fillna(0).sum()) > 0
            capital_then = kf._g(main, (t - 1, "11011"), "bs", "capital", "v")  # noqa: SLF001
            last_fy = max(y for (y, _r) in grouped[cc]["main"] if _r == "11011")
            capital_now = kf._g(grouped[cc]["main"], (last_fy, "11011"), "bs", "capital", "v")  # noqa: SLF001
            scale = capital_then / capital_now if (not split_since and capital_then and capital_now) else 1.0
            mcap = float(a["close"].iloc[0]) * shares_now * scale
            m = kf.metrics(main, market_cap=mcap, financial=dart_fin.is_financial(code, live[code].get("name")))
            m.update(code=code, market=live[code]["market"], ret=c1 / c0 - 1)
            cross.append(m)
        if len(cross) < 100:
            continue
        dist = kf.distributions(cross)
        for mkt in ("KOSPI", "KOSDAQ"):
            med = float(np.median([m["ret"] for m in cross if m["market"] == mkt]))
            for m in cross:
                if m["market"] != mkt:
                    continue
                sc = kf.score(m, dist)
                row = {"year": t, "market": mkt, "code": m["code"], "excess": m["ret"] - med, "total": sc["score"]}
                for pk, p in sc["pillars"].items():
                    row[pk] = p["score"]
                    for it in p["items"]:
                        row[it["key"]] = it["score"]
                panel.append(row)
        print(f"{t}: {len(cross)} companies")
    df = pd.DataFrame(panel)
    res: dict = {"built_at": dt.datetime.now().isoformat(timespec="seconds"), "years": sorted(df["year"].unique().tolist()),
                 "n": int(len(df)), "markets": {}}
    for mkt in ("KOSPI", "KOSDAQ", "ALL"):
        sub = df if mkt == "ALL" else df[df["market"] == mkt]
        out = {}
        for k in keys:
            if k not in sub:
                continue
            ics, spreads = [], []
            for _, g in sub.groupby("year"):
                x, y = g[k].to_numpy(float), g["excess"].to_numpy(float)
                ic = spearman(x, y)
                if ic is None:
                    continue
                ics.append(ic)
                ok = np.isfinite(x)
                q = pd.qcut(pd.Series(x[ok]).rank(method="first"), 5, labels=False)
                yy = pd.Series(y[ok])
                spreads.append(float(yy[q == 4].median() - yy[q == 0].median()))
            if len(ics) < 3:
                continue
            ics_a = np.array(ics)
            out[k] = {"ic": round(float(ics_a.mean()), 4), "t": round(float(ics_a.mean() / (ics_a.std(ddof=1) / np.sqrt(len(ics_a)))), 2)
                      if ics_a.std(ddof=1) > 0 else None, "pos_years": round(float((ics_a > 0).mean()), 2), "years": len(ics),
                      "q5_q1_pct": round(float(np.mean(spreads)) * 100, 2)}
        res["markets"][mkt] = out
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-prices", action="store_true")
    r = run(ap.parse_args().refresh_prices)
    for mkt, rows in r["markets"].items():
        print(f"\n== {mkt}  (years {r['years'][0]}-{r['years'][-1]})")
        print(f"{'metric':<16}{'IC':>8}{'t':>7}{'IC>0':>7}{'Q5-Q1%':>9}")
        for k, v in rows.items():
            print(f"{k:<16}{v['ic']:>8.3f}{(v['t'] or 0):>7.2f}{v['pos_years']:>7.0%}{v['q5_q1_pct']:>9.2f}")
