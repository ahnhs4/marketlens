"""Universe scanning (S&P 500, Nasdaq-100, KOSPI top 100, KOSDAQ top 50, watchlists).

A background job batch-downloads 2y of daily bars for every member, runs the full
technical evaluation, the macro score and stores a daily score snapshot. The
screener, technical/combined recommendations and market breadth all read the
latest scan; endpoints never trigger a full scan synchronously.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import threading
import time

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import select

from ..analysis import indicators, macro_scoring, user_rules
from ..analysis.timing_evidence import market_of
from ..cache import cache, cached
from ..config import APP_USER_AGENT, BACKEND_DIR
from ..db import SessionLocal
from ..models import Fundamental, Signal, WatchlistItem
from ..providers.yahoo import yahoo
from . import macro, prices
from .store import clean, upsert, utcnow
from .symbols import index

log = logging.getLogger(__name__)
UNIVERSE_FILE = BACKEND_DIR / "data" / "universe.json"
RESULTS_FILE = BACKEND_DIR / "data" / "scan_results.json"
_UA = {"User-Agent": APP_USER_AGENT}

GICS_TO_GROUP = {"Information Technology": "Technology", "Health Care": "Health Care", "Financials": "Financials",
                 "Consumer Discretionary": "Consumer Discretionary", "Communication Services": "Communication Services",
                 "Industrials": "Industrials", "Consumer Staples": "Consumer Staples", "Energy": "Energy",
                 "Utilities": "Utilities", "Real Estate": "Real Estate", "Materials": "Materials"}
SUBIND_TO_GROUP = {"Semiconductors": "Semiconductors", "Semiconductor Materials & Equipment": "Semiconductors",
                   "Application Software": "Software", "Systems Software": "Software", "Regional Banks": "Regional Banks",
                   "Biotechnology": "Biotech", "Aerospace & Defense": "Aerospace & Defense", "Gold": "Gold Miners",
                   "Homebuilding": "Homebuilders"}
KR_INDUSTRY_KEYWORDS = [("반도체", "Semiconductors"), ("소프트웨어", "Software"), ("자동차", "Consumer Discretionary"),
                        ("은행", "Financials"), ("보험", "Financials"), ("증권", "Financials"), ("금융", "Financials"),
                        ("지주", "Financials"), ("화학", "Materials"), ("철강", "Materials"), ("금속", "Materials"),
                        ("의약", "Health Care"), ("제약", "Health Care"), ("바이오", "Biotech"), ("의료", "Health Care"),
                        ("전기, 가스", "Utilities"), ("전력", "Utilities"), ("건설", "Industrials"), ("조선", "Industrials"),
                        ("선박", "Industrials"), ("항공", "Aerospace & Defense"), ("무기", "Aerospace & Defense"),
                        ("통신", "Communication Services"), ("출판", "Communication Services"), ("방송", "Communication Services"),
                        ("식료품", "Consumer Staples"), ("음료", "Consumer Staples"), ("화장품", "Consumer Staples"),
                        ("전자부품", "Technology"), ("전기장비", "Industrials"), ("통신 및 방송 장비", "Technology"),
                        ("석유", "Energy"), ("기계", "Industrials"), ("운송", "Industrials")]

_state = {"state": "idle", "started_at": None, "finished_at": None, "processed": 0, "total": 0, "errors": 0,
          "message": None}
_results: dict[str, dict] = {}
_breadth: dict = {}
_lock = threading.Lock()


# ------------------------------------------------------------- constituents
def _wiki_table(url: str, key_cols: tuple[str, ...]) -> pd.DataFrame | None:
    r = httpx.get(url, headers=_UA, timeout=30, follow_redirects=True)
    r.raise_for_status()
    for t in pd.read_html(io.StringIO(r.text)):
        if any(c in t.columns for c in key_cols) and len(t) > 50:
            return t
    return None


def _load_constituents() -> dict:
    out: dict = {"SP500": [], "NDX": [], "KOSPI": [], "KOSDAQ": [], "meta": {}, "fundamentals": {}}
    try:
        t = _wiki_table("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol",))
        for _, r in t.iterrows():
            sym = str(r["Symbol"]).replace(".", "-")
            out["SP500"].append(sym)
            grp = SUBIND_TO_GROUP.get(str(r.get("GICS Sub-Industry")), GICS_TO_GROUP.get(str(r.get("GICS Sector"))))
            out["meta"][sym] = {"name": r["Security"], "sector": r.get("GICS Sector"),
                                "industry": r.get("GICS Sub-Industry"), "group": grp, "market": "US"}
    except Exception as e:
        log.warning("S&P 500 constituents unavailable: %s", e)
    try:  # Wikipedia no longer carries the component table; approximate with the top-100 Nasdaq listings
        for q in yahoo.screen_us_nasdaq(110):
            sym = q["symbol"].replace(".", "-")
            if len(out["NDX"]) >= 100:
                break
            out["NDX"].append(sym)
            out["meta"].setdefault(sym, {"name": q["name"], "market": "US"})
        out["ndx_note"] = "나스닥100은 나스닥 상장 시가총액 상위 100종목으로 근사"
    except Exception as e:
        log.warning("Nasdaq-100 approximation unavailable: %s", e)
    for key, exch, n in (("KOSPI", "KSC", 100), ("KOSDAQ", "KOE", 50)):
        try:
            for q in yahoo.screen_kr(exch, size=n):
                sym = q["symbol"]
                out[key].append(sym)
                row = index.get(sym) or {}
                ind_ko = row.get("industry") or ""
                grp = next((g for kw, g in KR_INDUSTRY_KEYWORDS if kw in ind_ko), None)
                out["meta"][sym] = {"name": q["name"], "name_ko": row.get("name_ko"), "industry": ind_ko or None,
                                    "group": grp, "market": "KR", "market_cap": q.get("market_cap")}
        except Exception as e:
            log.warning("%s list unavailable: %s", key, e)
    # Batch fundamentals-lite for large US caps (market cap, P/E, forward P/E ...)
    try:
        from yfinance import EquityQuery
        import yfinance as yf

        q = EquityQuery("and", [EquityQuery("eq", ["region", "us"]), EquityQuery("gt", ["intradaymarketcap", 5_000_000_000])])
        for off in (0, 250, 500, 750):
            r = yf.screen(q, sortField="intradaymarketcap", sortAsc=False, size=250, offset=off)
            for x in (r or {}).get("quotes", []):
                out["fundamentals"][x["symbol"]] = {
                    "market_cap": x.get("marketCap"), "pe": x.get("trailingPE"), "forward_pe": x.get("forwardPE"),
                    "eps_ttm": x.get("epsTrailingTwelveMonths"), "pb": x.get("priceToBook"),
                    "dividend_yield": x.get("dividendYield"), "earnings_ts": x.get("earningsTimestamp")}
            time.sleep(0.5)
    except Exception as e:
        log.info("US screen fundamentals unavailable: %s", e)
    total = len(out["SP500"]) + len(out["NDX"]) + len(out["KOSPI"])
    if total > 200:
        try:
            UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
            UNIVERSE_FILE.write_text(json.dumps(clean(out)))
        except Exception:
            pass
        return out
    if UNIVERSE_FILE.exists():  # graceful degradation: last good copy
        log.warning("using cached universe file")
        return json.loads(UNIVERSE_FILE.read_text())
    return out


def constituents() -> dict:
    return cached("universe:constituents", 24 * 3600, _load_constituents)


def watchlist_symbols() -> list[str]:
    with SessionLocal() as db:
        return sorted({s for (s,) in db.execute(select(WatchlistItem.symbol)).all()})


def members(universe: str) -> list[str]:
    c = constituents()
    if universe == "ALL":
        return sorted(set(c["SP500"]) | set(c["NDX"]) | set(c["KOSPI"]) | set(c["KOSDAQ"]) | set(watchlist_symbols()))
    if universe == "WATCHLIST":
        return watchlist_symbols()
    return list(c.get(universe, []))


# --------------------------------------------------------------------- scan
def status() -> dict:
    return dict(_state)


def _group_for(sym: str, meta: dict) -> str | None:
    if meta.get("group"):
        return meta["group"]
    row = index.get(sym) or {}
    return macro_scoring.map_group(row.get("sector"), row.get("industry"))


def compute_breadth(frames: dict[str, pd.DataFrame], symbols: list[str]) -> dict:
    above50 = above200 = nh = nl = adv = dec = n = 0
    for s in symbols:
        d = frames.get(s)
        if d is None or len(d) < 210:
            continue
        c = d["close"]
        n += 1
        above50 += c.iloc[-1] > c.iloc[-50:].mean()
        above200 += c.iloc[-1] > c.iloc[-200:].mean()
        hi, lo = d["high"].iloc[-252:].max(), d["low"].iloc[-252:].min()
        nh += d["high"].iloc[-1] >= hi
        nl += d["low"].iloc[-1] <= lo
        adv += c.iloc[-1] > c.iloc[-2]
        dec += c.iloc[-1] < c.iloc[-2]
    if n == 0:
        return {}
    p50, p200 = above50 / n * 100, above200 / n * 100
    ad = adv / max(adv + dec, 1) * 100
    hl = nh / max(nh + nl, 1) * 100 if (nh + nl) else 50
    score = 0.35 * p200 + 0.25 * p50 + 0.20 * ad + 0.20 * hl
    return {"members": n, "pct_above_sma50": p50, "pct_above_sma200": p200, "new_highs": int(nh), "new_lows": int(nl),
            "advancers": int(adv), "decliners": int(dec), "ad_ratio": adv / max(dec, 1), "score": score,
            "label": "Healthy" if score >= 60 else "Weak" if score < 40 else "Neutral",
            "formula": "0.35×200일선 위 비율 + 0.25×50일선 위 비율 + 0.20×상승 종목 비율 + 0.20×신고가 비중",
            "as_of": str(max(frames[s].index[-1] for s in symbols if s in frames).date())}


def scan(universe: str = "ALL") -> None:
    """Full scan; runs in a background thread. Safe to call repeatedly (no-ops if already running)."""
    from .stock import signal_for  # local import to avoid cycles

    with _lock:
        if _state["state"] == "running":
            return
        _state.update(state="running", started_at=utcnow().isoformat(), processed=0, errors=0, message=None)
    try:
        c = constituents()
        syms = members(universe)
        _state["total"] = len(syms)
        frames = yahoo.history_many(syms, period="2y")
        prices.put_many_daily(frames, years=2)
        for key, lst in (("SP500", c["SP500"]), ("KOSPI", c["KOSPI"])):
            b = compute_breadth(frames, lst)
            if b:
                _breadth[key] = b
        reg = macro.regime(_breadth.get("SP500", {}).get("pct_above_sma200"))
        regime_label = reg["label"]
        ft = reg["factor_trends"]
        rs = group_rs()
        group_cache = {g: macro_scoring.group_score(g, ft, rs.get(g)) for g in macro_scoring.EXPOSURES}
        ff = macro.factor_frame(2)
        moves = macro.factor_moves(60)
        funda = _stored_fundamentals()
        try:   # Korean companies: OpenDART metrics replace Yahoo's patchy KR fundamentals
            from . import dart_fin
            dartf = dart_fin.scan_fields() if dart_fin.enabled() else {}
        except Exception as e:
            log.info("DART fields unavailable for scan: %s", e)
            dartf = {}
        try:
            usdkrw = prices.get_quote("KRW=X").price
        except Exception:
            usdkrw = None
        snaps = []
        for s in syms:
            d = frames.get(s)
            _state["processed"] += 1
            if d is None or len(d) < 210:
                continue
            try:
                ind = indicators.compute_all(d)
                sig, ma, ri, sr, pt = signal_for(ind, regime_label, market=market_of(s))
                meta = c["meta"].get(s, {})
                grp = _group_for(s, meta)
                gres = group_cache.get(grp) if grp else None
                cres = group_cache.get("Korea") if s.endswith((".KS", ".KQ")) else None
                betas = macro_scoring.factor_betas(d["close"], ff) if len(ff) else None
                mres = macro_scoring.stock_macro_score(gres, cres, betas, moves)
                last = ind.iloc[-1]
                fl = {**(c.get("fundamentals", {}).get(s) or {}), **(funda.get(s) or {})}
                if s.endswith((".KS", ".KQ")):
                    fl.update({k: v for k, v in (dartf.get(s.split(".")[0]) or {}).items() if v is not None})
                if meta.get("market_cap") and not fl.get("market_cap"):
                    fl["market_cap"] = meta["market_cap"]
                # Screener compares sizes across markets in USD.
                is_kr = s.endswith((".KS", ".KQ"))
                mc = fl.get("market_cap")
                fl["market_cap_usd"] = (mc / usdkrw if (is_kr and mc and usdkrw) else (None if is_kr else mc))
                hz = sig["horizons"]
                best_h = max(hz, key=lambda k: hz[k]["score"])
                row = {
                    "symbol": s, "name": meta.get("name") or (index.get(s) or {}).get("name") or s,
                    "name_ko": meta.get("name_ko"), "market": "KR" if s.endswith((".KS", ".KQ")) else "US",
                    "group": grp, "universes": [u for u in ("SP500", "NDX", "KOSPI", "KOSDAQ") if s in c.get(u, [])],
                    "price": float(last["close"]), "change_pct": float(last["ret1"] * 100) if pd.notna(last["ret1"]) else None,
                    "technical": sig["score"], "signal": sig["signal"], "macro": mres.get("score"),
                    "rsi": _f(last["rsi"]), "mom20": _f(last["mom20"]), "mom60": _f(last["mom60"]),
                    "vol_ratio": _f(last["vol_ratio"]), "atr_pct": _f(last["atr_pct"]), "volatility": _f(last["vol20"]),
                    "above_sma200": bool(pd.notna(last["sma200"]) and last["close"] > last["sma200"]),
                    "sma20_gt_sma60": bool(pd.notna(last["sma60"]) and last["sma20"] > last["sma60"]),
                    "dist_sma200": _f(last["close"] / last["sma200"] - 1) if pd.notna(last["sma200"]) else None,
                    "dist_52w_high": _f(last["close"] / last["hi52"] - 1) if pd.notna(last["hi52"]) else None,
                    "near_breakout": bool(pd.notna(last["hi55_prev"]) and -0.03 <= last["close"] / last["hi55_prev"] - 1 <= 0),
                    "trend": ma["structure"], "patterns": [f"{p['pattern']} ({p['status']})" for p in pt["active"]][:3],
                    "horizons": {k: v["score"] for k, v in hz.items()}, "best_horizon": best_h,
                    "bullish": sig["bullish_factors"][:3], "bearish": sig["bearish_factors"][:3],
                    "macro_reasons": mres.get("reasons", [])[:2], "macro_risks": mres.get("risks", [])[:2],
                    "components": {x["key"]: x["score"] for x in sig["components"]},
                    "stoch_k": {hz: _f(last.get(f"stoch_{hz}_k")) for hz in ("short", "medium", "long")},
                    "stoch_ml_overbought": user_rules.stoch_ml_overbought(last),
                    "rsi_ml_oversold": user_rules.rsi_ml_oversold(ind),
                    "support_pct": sr["nearest_support_pct"], "resistance_pct": sr["nearest_resistance_pct"],
                    "dollar_volume": _f(last["close"] * ind["volume"].iloc[-20:].mean()),
                    "as_of": str(ind.index[-1].date()), **{f"f_{k}": v for k, v in fl.items()},
                }
                _results[s] = clean(row)
                snaps.append({"symbol": s, "as_of": ind.index[-1].date(), "technical": sig["score"], "macro": mres.get("score"),
                              "fundamental": None, "overall": None, "signal": sig["signal"],
                              "horizons": row["horizons"], "components": row["components"], "created_at": utcnow()})
            except Exception as e:
                _state["errors"] += 1
                log.debug("scan failed for %s: %s", s, e)
        _store_snapshots(snaps)
        cache.set("universe:results_at", utcnow().isoformat(), 7 * 86400)
        try:  # survive restarts: the next boot serves these until its own scan finishes
            RESULTS_FILE.write_text(json.dumps({"finished_at": utcnow().isoformat(), "results": _results,
                                                "breadth": clean(_breadth)}))
        except Exception as e:
            log.debug("results persist failed: %s", e)
        _state.update(state="done", finished_at=utcnow().isoformat(),
                      message=f"{len(_results)}개 종목 분석 완료, 오류 {_state['errors']}건")
    except Exception as e:
        log.exception("universe scan failed")
        _state.update(state="error", finished_at=utcnow().isoformat(), message=str(e)[:300])


def _store_snapshots(snaps: list[dict]) -> None:
    if not snaps:
        return
    try:
        with SessionLocal() as db:
            # Keep richer rows written by the stock page (overall/fundamental) - only fill missing ones.
            existing = {(r.symbol, r.as_of) for r in db.execute(select(Signal.symbol, Signal.as_of).where(
                Signal.as_of.in_({s["as_of"] for s in snaps}))).all()}
            fresh = [s for s in snaps if (s["symbol"], s["as_of"]) not in existing]
            upsert(db, Signal, fresh, ["symbol", "as_of"])
            db.commit()
    except Exception as e:
        log.warning("snapshot store failed: %s", e)


def _stored_fundamentals() -> dict[str, dict]:
    since = dt.date.today() - dt.timedelta(days=10)
    with SessionLocal() as db:
        rows = db.execute(select(Fundamental).where(Fundamental.as_of >= since).order_by(Fundamental.as_of)).scalars().all()
    return {r.symbol: r.data for r in rows}


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) or np.isinf(v) else v


def _load_persisted() -> None:
    if _results or not RESULTS_FILE.exists():
        return
    try:
        data = json.loads(RESULTS_FILE.read_text())
        _results.update(data.get("results", {}))
        _breadth.update(data.get("breadth", {}))
        if _state["state"] == "idle":
            _state.update(finished_at=data.get("finished_at"), message="이전 스캔 결과를 불러옴")
        log.info("loaded %d persisted scan results", len(_results))
    except Exception as e:
        log.warning("could not load persisted scan results: %s", e)


def results() -> list[dict]:
    _load_persisted()
    return list(_results.values())


def latest_breadth(key: str = "SP500") -> dict | None:
    _load_persisted()
    return _breadth.get(key) or None


def breadth_all() -> dict:
    return clean({"breadth": _breadth, "scan": status()})


def group_rs() -> dict[str, float]:
    def build():
        syms = [p for _, _, p in macro_scoring.GROUP_PROXIES] + ["SPY"]
        frames = yahoo.history_parallel(syms, period="1y")
        spy = frames.get("SPY")
        out = {}
        for g, _, p in macro_scoring.GROUP_PROXIES:
            d = frames.get(p)
            if d is not None and spy is not None:
                v = macro_scoring.relative_strength(d["close"], spy["close"])
                if v is not None:
                    out[g] = v
        out["_perf"] = {g: float(frames[p]["close"].iloc[-1] / frames[p]["close"].iloc[-64] - 1) * 100
                        for g, _, p in macro_scoring.GROUP_PROXIES if p in frames and len(frames[p]) > 64}
        return out
    return cached("universe:group_rs", 1800, build)


def enrich_fundamentals(limit: int = 120) -> int:
    """Slowly backfill per-symbol fundamentals (growth, margins) for screener filters."""
    from .stock import profile

    have = _stored_fundamentals()
    todo = [s for s in members("ALL") if s not in have][:limit]
    done = 0
    for s in todo:
        try:
            profile(s)
            done += 1
        except Exception:
            pass
        time.sleep(0.6)  # gentle on the upstream API
    return done


def run_in_background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()

