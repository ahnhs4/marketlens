"""추세추종 플로우 service: market-wide daily history, share counts and the four-step run.

KR: every KOSPI/KOSDAQ company from the KRX KIND list (SPACs excluded) - daily bars are
downloaded once in the background (2 years) and topped up afterwards. US: the S&P 500 +
Nasdaq-100 universe that the regular scan keeps current. Share counts (for the 희소 가치
conditions) are fetched only for names that already passed the price-based conditions.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from ..analysis import trend_flow as tf
from ..cache import cache, cached
from ..db import SessionLocal
from ..i18n_ko import GROUP_KO
from ..models import DailyPrice, Fundamental, Stock
from ..providers.yahoo import yahoo
from . import prices, universe
from .store import clean, utcnow

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 430            # ~290 sessions: 52 weeks + 150-day MA + a month of slope
_hist = {"state": "idle", "done": 0, "total": 0, "message": None, "started_at": None, "finished_at": None}
_hist_lock = threading.Lock()
EXCLUDE_WORDS = ("스팩", "기업인수목적")
INDEX_OF = {".KS": "^KS11", ".KQ": "^KQ11"}


# ------------------------------------------------------------------ universe
def kr_universe() -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(select(Stock.symbol, Stock.name_ko, Stock.name, Stock.exchange, Stock.industry)
                          .where(Stock.market == "KR")).all()
    out = []
    for sym, ko, name, exch, ind in rows:
        nm = ko or name or sym
        if not sym.endswith((".KS", ".KQ")) or any(w in nm for w in EXCLUDE_WORDS):
            continue
        out.append({"symbol": sym, "name": nm, "exchange": exch, "theme": ind or "기타"})
    return out


def us_universe() -> list[dict]:
    c = universe.constituents()
    syms = sorted(set(c.get("SP500") or []) | set(c.get("NDX") or []))
    res = universe.results()
    rows = res if isinstance(res, dict) else {r["symbol"]: r for r in res or []}
    out = []
    for s in syms:
        r = rows.get(s) or {}
        g = r.get("group")
        out.append({"symbol": s, "name": r.get("name") or s, "exchange": "US", "theme": GROUP_KO.get(g, g) if g else "기타"})
    return out


# ------------------------------------------------------------------ history
def _coverage(symbols: list[str]) -> dict[str, tuple[dt.date, dt.date, int]]:
    out = {}
    with SessionLocal() as db:
        for i in range(0, len(symbols), 900):
            part = symbols[i:i + 900]
            for s, a, b, n in db.execute(select(DailyPrice.symbol, func.min(DailyPrice.date), func.max(DailyPrice.date),
                                                func.count()).where(DailyPrice.symbol.in_(part)).group_by(DailyPrice.symbol)).all():
                out[s] = (a, b, n)
    return out


def history_status(market: str = "KR") -> dict:
    syms = [u["symbol"] for u in (kr_universe() if market == "KR" else us_universe())]
    cov = _coverage(syms)
    ready = sum(1 for s in syms if s in cov and cov[s][2] >= 260)
    return {**_hist, "market": market, "symbols": len(syms), "ready_symbols": ready,
            "ready_pct": round(ready / len(syms) * 100, 1) if syms else 0.0}


def ensure_kr_history() -> None:
    """Background: 2 years of daily bars for every KR symbol lacking them; top up stale ones (idempotent)."""
    with _hist_lock:
        if _hist["state"] == "running":
            return
        _hist.update(state="running", started_at=utcnow().isoformat(), done=0, total=0, message="한국 전 종목 일봉 확인 중")
    try:
        syms = [u["symbol"] for u in kr_universe()]
        cov = _coverage(syms)
        try:
            last_session = prices.get_daily("^KS11", years=2).df.index[-1].date()
        except Exception:
            last_session = dt.date.today()
        full = [s for s in syms if s not in cov or cov[s][2] < 260]
        stale = [s for s in syms if s in cov and cov[s][2] >= 260 and cov[s][1] < last_session]
        _hist["total"] = len(full) + len(stale)
        for batch, period in ((full, "2y"), (stale, "1mo")):
            for i in range(0, len(batch), 200):
                part = batch[i:i + 200]
                frames = yahoo.history_many(part, period=period)
                prices.put_many_daily(frames, years=2)
                _hist["done"] += len(part)
                _hist["message"] = f"일봉 내려받는 중 {_hist['done']:,}/{_hist['total']:,}"
                time.sleep(1.0)  # be gentle with the upstream API
        _hist.update(state="done", finished_at=utcnow().isoformat(),
                     message=f"전체 {len(syms):,}종목 · 새로 받음 {len(full):,} · 갱신 {len(stale):,}")
        cache.delete_prefix("trend:")
    except Exception as e:
        log.exception("trend flow history failed")
        _hist.update(state="error", finished_at=utcnow().isoformat(), message=str(e)[:200])


def start_history_fill() -> None:
    if _hist["state"] != "running":
        threading.Thread(target=ensure_kr_history, name="trend-history", daemon=True).start()


def _frames(symbols: list[str]) -> dict[str, pd.DataFrame]:
    start = dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)
    rows = []
    with SessionLocal() as db:
        for i in range(0, len(symbols), 900):
            part = symbols[i:i + 900]
            rows += db.execute(select(DailyPrice.symbol, DailyPrice.date, DailyPrice.high, DailyPrice.low, DailyPrice.close,
                                      DailyPrice.volume).where(DailyPrice.symbol.in_(part), DailyPrice.date >= start)).all()
    df = pd.DataFrame(rows, columns=["symbol", "date", "high", "low", "close", "volume"])
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["date"])
    return {c: df.pivot_table(index="date", columns="symbol", values=c, aggfunc="last").sort_index()
            for c in ("high", "low", "close", "volume")}


# ------------------------------------------------------------------ shares
def _shares(symbol: str) -> tuple[float | None, float | None]:
    def load():
        with SessionLocal() as db:
            row = db.execute(select(Fundamental.data).where(Fundamental.symbol == symbol)
                             .order_by(Fundamental.as_of.desc()).limit(1)).first()
        d = (row[0] if row else None) or {}
        if d.get("shares_outstanding") and "float_shares" in d:
            return d.get("shares_outstanding"), d.get("float_shares")
        from .stock import profile
        try:
            f = profile(symbol).get("fundamentals") or {}
        except Exception:
            return None, None
        return f.get("shares_outstanding"), f.get("float_shares")
    return cached(f"trend:shares:{symbol}", 7 * 86400, load)


# ------------------------------------------------------------------ run
def _arr(w: dict[str, pd.DataFrame], s: str) -> tuple[np.ndarray, ...] | None:
    c = w["close"][s]
    ok = c.notna()
    if ok.sum() < 200:
        return None
    idx = c.index[ok]
    return (idx, c[ok].to_numpy(float), w["high"][s][ok].to_numpy(float), w["low"][s][ok].to_numpy(float),
            w["volume"][s][ok].fillna(0).to_numpy(float))


def _sparkline(c: np.ndarray, n: int = 120) -> list[float]:
    x = c[-n:]
    return [round(float(v), 4) for v in x]


def run(market: str, enabled: list[str]) -> dict:
    uni = kr_universe() if market == "KR" else us_universe()
    meta = {u["symbol"]: u for u in uni}
    w = _frames(list(meta))
    if not w:
        return {"market": market, "ready": False, "history": history_status(market), "funnel": [], "candidates": []}
    as_of = w["close"].index[-1]
    enabled = [k for k in enabled if k in tf.COND_BY_KEY]
    price_conds = [k for k in enabled if not tf.COND_BY_KEY[k].needs_shares]
    share_conds = [k for k in enabled if tf.COND_BY_KEY[k].needs_shares]

    rows, skipped = [], {"short_history": 0, "stale": 0}
    singles = {k: 0 for k in tf.COND_BY_KEY}
    for s in meta:
        if s not in w["close"]:
            skipped["short_history"] += 1
            continue
        a = _arr(w, s)
        if a is None:
            skipped["short_history"] += 1
            continue
        idx, c, h, lo, v = a
        if (as_of - idx[-1]).days > 5:
            skipped["stale"] += 1
            continue
        r = tf.screen_one(c, h, lo, v, market)
        if r is None:
            skipped["short_history"] += 1
            continue
        for k, ok in r["checks"].items():
            singles[k] += ok
        failed = [k for k in price_conds if not r["checks"].get(k)]
        rows.append({"symbol": s, **{k: meta[s][k] for k in ("name", "exchange", "theme")}, "close": r["close"],
                     "change_pct": float((c[-1] / c[-2] - 1) * 100) if len(c) > 1 else None, "value": r["value"],
                     "checks": dict(r["checks"]), "failed": failed, "range5": r["range5"],
                     "_a": a})
    screened = len(rows)

    # shares only for names that passed every price-based condition
    pre = [r for r in rows if not r["failed"]]
    if share_conds and pre:
        with ThreadPoolExecutor(max_workers=6) as ex:
            got = dict(zip([r["symbol"] for r in pre], ex.map(_shares, [r["symbol"] for r in pre])))
        for r in pre:
            listed, floating = got.get(r["symbol"], (None, None))
            r["shares"] = {"listed": listed, "float": floating}
            sc = tf.share_checks(listed, floating, market)
            r["checks"].update(sc)
            r["failed"] += [k for k in share_conds if sc.get(k) is not True]
            r["share_unknown"] = [k for k in share_conds if sc.get(k) is None]
    stage1 = [r for r in rows if not r["failed"]]
    near = sorted([r for r in rows if len(r["failed"]) == 1], key=lambda r: -(r["value"] or 0))[:40]

    # ② strength vs the stock's own index
    idx_cache: dict[str, pd.Series] = {}

    def index_for(sym: str) -> pd.Series | None:
        key = "^GSPC" if market == "US" else INDEX_OF.get(sym[-3:], "^KS11")
        if key not in idx_cache:
            try:
                idx_cache[key] = prices.get_daily(key, years=2).df["close"]
            except Exception:
                idx_cache[key] = None
        return idx_cache[key]

    for r in stage1:
        idx, c, h, lo, v = r["_a"]
        ix = index_for(r["symbol"])
        if ix is None:
            r["strength"] = None
            continue
        al = ix.reindex(idx).ffill().to_numpy(float)
        r["strength"] = tf.strength(c, al)
    stage2 = [r for r in stage1 if r.get("strength") and r["strength"]["passed"]]

    # ③ entry point
    for r in stage2:
        idx, c, h, lo, v = r["_a"]
        r["entry"] = tf.entry(c, h, lo, v)
        r["entry"]["state_ko"] = tf.STATE_KO[r["entry"]["state"]]
    stage3 = [r for r in stage2 if r["entry"]["passed"]]

    # ④ one per theme - the strongest relative performer leads
    by_theme: dict[str, list[dict]] = {}
    for r in stage3:
        by_theme.setdefault(r["theme"], []).append(r)
    leaders = []
    for theme, lst in by_theme.items():
        lst.sort(key=lambda r: -r["strength"]["score"])
        for i, r in enumerate(lst):
            r["theme_rank"] = i + 1
            r["theme_peers"] = [x["symbol"] for x in lst if x["symbol"] != r["symbol"]]
        leaders.append(lst[0])
    leaders.sort(key=lambda r: -r["strength"]["score"])
    for r in leaders:
        idx, c, h, lo, v = r["_a"]
        e = r["entry"]
        r["plan"] = tf.trade_plan(e["pivot"], e["stop"], float(c[-1]), e["state"] == "breakout")
        r["sparkline"] = _sparkline(c)
        r["dates"] = [str(idx[-len(r["sparkline"])].date()), str(idx[-1].date())]

    def public(r: dict, stage: int) -> dict:
        out = {k: v for k, v in r.items() if not k.startswith("_")}
        out["stage"] = stage
        return out

    ids4 = {r["symbol"] for r in leaders}
    ids3 = {r["symbol"] for r in stage3}
    ids2 = {r["symbol"] for r in stage2}

    def reached(r: dict) -> int:
        s = r["symbol"]
        return 4 if s in ids4 else 3 if s in ids3 else 2 if s in ids2 else 1

    for r in stage1:
        if r["symbol"] in ids4:
            r["result"] = "최종 후보"
        elif r["symbol"] in ids3:
            r["result"] = f"같은 업종({r['theme']}) {r.get('theme_rank', 2)}등 - 1등만 남김"
        elif r["symbol"] in ids2:
            r["result"] = r["entry"]["state_ko"]
        elif r.get("strength") is None:
            r["result"] = "지수 데이터 없음"
        else:
            st = r["strength"]
            why = []
            if st["excess_60d"] <= 0:
                why.append(f"60일 초과수익 {st['excess_60d']:+.1f}%p")
            if st["downside_ratio"] is not None and st["downside_ratio"] > tf.DOWNSIDE_MAX:
                why.append(f"지수 하락일 낙폭 {st['downside_ratio']:.1f}배")
            r["result"] = "시장보다 약함: " + ", ".join(why)

    ms = tf.market_state(prices.get_daily("^KS11" if market == "KR" else "^GSPC", years=2).df["close"])
    labels = {c["key"]: c for c in tf.cond_labels(market)}
    return clean({
        "market": market, "as_of": str(as_of.date()), "ready": True, "enabled": enabled,
        "market_state": ms, "modes": tf.MODES,
        "conditions": [{**labels[k], "enabled": k in enabled, "pass_count": singles.get(k) if not tf.COND_BY_KEY[k].needs_shares else None}
                       for k in labels],
        "funnel": [{"step": 0, "label": "전체 종목", "count": len(meta), "note": f"이력 부족 {skipped['short_history']:,} · 거래 정지/지연 {skipped['stale']:,} 제외 → {screened:,}"},
                   {"step": 1, "label": "조건검색", "count": len(stage1)},
                   {"step": 2, "label": "시장보다 강한가", "count": len(stage2)},
                   {"step": 3, "label": "타점이 있는가", "count": len(stage3)},
                   {"step": 4, "label": "같은 테마에서 하나", "count": len(leaders)}],
        "stage1": [public(r, reached(r)) for r in sorted(stage1, key=lambda r: -(r.get("strength") or {}).get("score", -999))],
        "near_miss": [{**{k: r[k] for k in ("symbol", "name", "theme", "close", "change_pct", "value")},
                       "failed": r["failed"]} for r in near],
        "candidates": [public(r, 4) for r in leaders],
        "history": history_status(market),
        "updated_at": utcnow().isoformat()})


def flow(market: str = "KR", mode: str | None = None, conds: list[str] | None = None) -> dict:
    if market == "KR":
        st = history_status("KR")
        if st["ready_pct"] < 90 and _hist["state"] != "running":
            start_history_fill()
    if conds is None:
        if mode is None:
            try:
                mode = tf.market_state(prices.get_daily("^KS11" if market == "KR" else "^GSPC", years=2).df["close"])["state"]
            except Exception:
                mode = "normal"
        conds = tf.MODES.get(mode, tf.MODES["normal"])
    key = f"trend:flow:{market}:{','.join(sorted(conds))}:{_hist['finished_at']}"
    res = cached(key, 600, lambda: run(market, list(conds)))
    return {**res, "mode": mode, "history": history_status(market)}
