"""Which of the popular "high win rate" auto-trading rules actually hold up - every rule in analysis/rules.py
run with its published default settings (no tuning) on broad data, after costs.

Data:
  * Daily rules: every S&P 500 member with stored history (about 2022-06 ~ today), plus SPY / QQQ / IWM / DIA
    over 10 years. Most of these rules were published before 2022, so the member test is out-of-sample.
  * Intraday rules: 60 sessions of 5-minute (ORB, VWAP) / 15-minute bars for 30 large, liquid US names.
Costs: none / 0.07% per side (event or API rate) / 0.25% per side (standard online rate); 0.05% slippage
each way on top of fees. Ranking uses 0.07%.
A rule is a candidate only if, after costs, the average trade is positive, both halves of the period are
positive, most symbols made money, and there are enough trades. Candidates are ranked by win rate and by
annual return (average of the two ranks). Caveats: today's index members (survivor bias), no taxes,
fixed dollar amount per trade, past results.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from ..analysis import rules
from ..config import BACKEND_DIR
from ..db import SessionLocal
from ..models import DailyPrice
from . import prices, universe
from .store import clean, utcnow

log = logging.getLogger(__name__)
FILE = Path(BACKEND_DIR) / "data" / "strategy_study.json"
COSTS = {"gross": (0.0, 0.0), "event": (7.0, 5.0), "standard": (25.0, 5.0)}
RANK_COST = "event"
ETFS = ["SPY", "QQQ", "IWM", "DIA"]
INTRADAY_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V", "LLY", "UNH", "XOM",
                    "MA", "COST", "HD", "PG", "NFLX", "AMD", "CRM", "ORCL", "ADBE", "WMT", "BAC", "KO", "PEP", "MRK", "ABBV"]
INTRADAY_INTERVAL = {"orb": "5m", "vwap_reclaim": "5m", "trend_breakout": "15m", "rsi2_pullback": "15m"}
MIN_TRADES = {"daily": 300, "intraday": 150}
_state = {"state": "idle", "message": None, "progress": 0.0, "started_at": None, "finished_at": None}
_lock = threading.Lock()


def _members_daily(start: dt.date) -> dict[str, pd.DataFrame]:
    syms = list(universe.constituents().get("SP500") or [])
    rows = []
    with SessionLocal() as db:
        for i in range(0, len(syms), 900):
            rows += db.execute(select(DailyPrice.symbol, DailyPrice.date, DailyPrice.open, DailyPrice.high, DailyPrice.low,
                                      DailyPrice.close, DailyPrice.adj_close, DailyPrice.volume)
                               .where(DailyPrice.symbol.in_(syms[i:i + 900]), DailyPrice.date >= start)).all()
    df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    out = {}
    for s, g in df.groupby("symbol"):
        g = g.set_index("date").sort_index()
        if len(g) >= 750 and g["adj_close"].notna().all():
            out[s] = rules.adjusted_daily(g)
    return out


def _summ(per_symbol: list[dict], cost: str, mid: pd.Timestamp) -> dict:
    fee, slip = COSTS[cost]
    trades = [t for x in per_symbol for t in x["trades"]]
    if not trades:
        return {"trades": 0}
    r = np.array([rules.net_return(t["entry"], t["exit"], fee, slip) for t in trades])
    first = np.array([pd.Timestamp(t["entry_time"]) < mid for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    sym_ann = []
    for x in per_symbol:
        if x["years"] > 0:
            rr = [rules.net_return(t["entry"], t["exit"], fee, slip) for t in x["trades"]]
            sym_ann.append(sum(rr) * 100 / x["years"])
    sym_pl = [sum(rules.net_return(t["entry"], t["exit"], fee, slip) for t in x["trades"]) for x in per_symbol if x["trades"]]
    return {"trades": int(len(r)), "win_rate": float((r > 0).mean() * 100), "avg_ret_pct": float(r.mean() * 100),
            "avg_win_pct": float(wins.mean() * 100) if len(wins) else None, "avg_loss_pct": float(losses.mean() * 100) if len(losses) else None,
            "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
            "ann_ret_pct": float(np.mean(sym_ann)) if sym_ann else None,
            "first_half_avg_pct": float(r[first].mean() * 100) if first.any() else None,
            "second_half_avg_pct": float(r[~first].mean() * 100) if (~first).any() else None,
            "pct_symbols_positive": float(np.mean([p > 0 for p in sym_pl]) * 100) if sym_pl else None}


def _study_set(frames: dict[str, pd.DataFrame], kind: str) -> dict:
    per = []
    for sym, df in frames.items():
        trades, n_bars, years, bh = rules.run(df, kind, {})
        if trades is None:
            continue
        held = sum(t["bars"] + 1 for t in trades)
        dd = 0.0
        if trades:
            curve = np.cumsum([rules.net_return(t["entry"], t["exit"], *COSTS[RANK_COST]) for t in trades])
            dd = float((curve - np.maximum.accumulate(np.r_[0.0, curve])[1:]).min() * 100)
        bh_ann = ((1 + bh / 100) ** (1 / years) - 1) * 100 if years > 0 and bh is not None and bh > -100 else None
        per.append({"symbol": sym, "trades": trades, "years": years, "exposure": held / n_bars * 100 if n_bars else 0,
                    "dd": dd, "bh_ann": bh_ann, "bh": bh})
    if not per:
        return {"symbols": 0}
    starts = [pd.Timestamp(t["entry_time"]) for x in per for t in x["trades"]]
    mid = pd.Series(starts).median() if starts else pd.Timestamp.now(tz="UTC")
    out = {c: _summ(per, c, mid) for c in COSTS}
    exposure = float(np.mean([x["exposure"] for x in per]))
    for c in COSTS:  # return per year actually invested (an upper bound: it assumes the money is always redeployed)
        a = out[c].get("ann_ret_pct")
        out[c]["ann_ret_invested_pct"] = a / (exposure / 100) if a is not None and exposure > 0 else None
    out.update({"symbols": len(per), "years": float(np.median([x["years"] for x in per])),
                "exposure_pct": float(np.mean([x["exposure"] for x in per])),
                "median_dd_pct": float(np.median([x["dd"] for x in per])),
                "buy_hold_ann_pct": float(np.nanmean([x["bh_ann"] for x in per if x["bh_ann"] is not None])) if any(x["bh_ann"] is not None for x in per) else None,
                "trades_per_symbol_year": float(np.mean([len(x["trades"]) / x["years"] for x in per if x["years"] > 0])),
                "open_at_end": sum(1 for x in per for t in x["trades"] if t["why"].startswith("기간 끝")),
                "best_symbols": [x["symbol"] for x in sorted(per, key=lambda x: -sum(rules.net_return(t["entry"], t["exit"], *COSTS[RANK_COST]) for t in x["trades"]))[:5]]})
    return out


def _eligible(r: dict, timeframe: str) -> tuple[bool, str]:
    m = r.get(RANK_COST) or {}
    if not m.get("trades") or m["trades"] < MIN_TRADES[timeframe]:
        return False, f"거래 수 부족 ({m.get('trades', 0)}건)"
    if m["avg_ret_pct"] <= 0:
        return False, f"비용 후 평균 거래 {m['avg_ret_pct']:+.2f}%"
    if (m.get("first_half_avg_pct") or 0) <= 0 or (m.get("second_half_avg_pct") or 0) <= 0:
        return False, f"앞·뒤 기간 중 한쪽이 손실 ({m.get('first_half_avg_pct', 0):+.2f}% / {m.get('second_half_avg_pct', 0):+.2f}%)"
    if (m.get("pct_symbols_positive") or 0) < 55:
        return False, f"이익 난 종목 {m.get('pct_symbols_positive', 0):.0f}%뿐"
    return True, "통과"


def run_study() -> None:
    with _lock:
        if _state["state"] == "running":
            return
        _state.update(state="running", progress=0.0, started_at=utcnow().isoformat(), message="데이터 불러오는 중")
    try:
        with rules.quiet():
            members = _members_daily(dt.date.today() - dt.timedelta(days=int(365.25 * 4.5)))
            etfs = {s: rules.adjusted_daily(prices.get_daily(s, years=10).df) for s in ETFS}
            kinds = list(rules.KINDS.values())
            results = []
            intraday_frames: dict[str, dict[str, pd.DataFrame]] = {}
            for i, k in enumerate(kinds):
                _state.update(progress=i / len(kinds), message=f"{k.name} 백테스트 중")
                if k.timeframe == "daily":
                    main, extra = _study_set(members, k.id), _study_set(etfs, k.id)
                    data = f"S&P 500 구성종목 {main.get('symbols', 0)}개 · 일봉 약 {main.get('years', 0):.1f}년"
                else:
                    iv = INTRADAY_INTERVAL[k.id]
                    if iv not in intraday_frames:
                        fr = {}
                        for s in INTRADAY_SYMBOLS:
                            try:
                                fr[s] = prices.get_intraday(s, iv, rules.HISTORY[iv])
                            except Exception as e:
                                log.info("study intraday %s failed: %s", s, e)
                        intraday_frames[iv] = fr
                    main, extra = _study_set(intraday_frames[iv], k.id), None
                    data = f"대형주 · ETF {main.get('symbols', 0)}개 · {iv}봉 최근 60거래일"
                ok, why = _eligible(main, k.timeframe)
                results.append({"kind": k.id, "name": k.name, "family": k.family, "timeframe": k.timeframe,
                                "interval": "1d" if k.timeframe == "daily" else INTRADAY_INTERVAL[k.id], "description": k.description,
                                "source": {"title": k.source[0], "url": k.source[1]} if k.source else None,
                                "data": data, "main": main, "etf_10y": extra, "eligible": ok, "verdict": why})
        ranked = [r for r in results if r["eligible"]]
        for key in ("win_rate", "ann_ret_pct"):
            order = sorted(ranked, key=lambda r: -(r["main"][RANK_COST].get(key) or -1e9))
            for pos, r in enumerate(order, 1):
                r.setdefault("ranks", {})[key] = pos
        for r in ranked:
            r["score"] = (r["ranks"]["win_rate"] + r["ranks"]["ann_ret_pct"]) / 2
        picks = [r["kind"] for r in sorted(ranked, key=lambda r: (r["score"], -r["main"][RANK_COST]["ann_ret_pct"]))[:4]]
        out = clean({"generated_at": utcnow().isoformat(), "rank_cost": RANK_COST,
                     "costs": {k: {"fee_pct": v[0] / 100, "slippage_pct": v[1] / 100} for k, v in COSTS.items()},
                     "picks": picks, "results": results, "method": __doc__})
        FILE.write_text(json.dumps(out, ensure_ascii=False))
        _state.update(state="done", progress=1.0, finished_at=utcnow().isoformat(), message=f"{len(results)}개 전략 완료 · 후보 {len(ranked)}개")
    except Exception as e:
        log.exception("strategy study failed")
        _state.update(state="error", finished_at=utcnow().isoformat(), message=str(e)[:200])


def start() -> None:
    if _state["state"] != "running":
        threading.Thread(target=run_study, name="strategy-study", daemon=True).start()


def load() -> dict:
    data = json.loads(FILE.read_text()) if FILE.exists() else None
    return {"status": dict(_state), "study": data}
