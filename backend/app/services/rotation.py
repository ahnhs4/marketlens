"""Monthly rotation backtest: hold the top N of the technical ranking and re-pick every month.

Point-in-time: at each month-end close the ranking is rebuilt from the data up to that day only (the same
technical score and the same exclusions as the live ranking: medium+long stochastic overbought, RSI
oversold), and the trades fill at the NEXT session's open. Costs: commission + slippage on every trade,
Korean sell tax on sells, and for US stocks the 22% capital-gains tax on yearly realised gains above
2.5M KRW (accrued daily, paid at the turn of the year).

Known limits, reported with every result: the universe is today's member list (survivorship bias), the
stored price history decides the first month, FX is held constant, indices are price indices.

Scoring every member at every month-end is slow (~25 ms each), so the scores are computed once in a
background job and kept in data/rotation_scores_<universe>.json; later runs only add new months.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from ..analysis import backtest as bt
from ..analysis import indicators, rules, user_rules
from ..cache import cache, cached
from ..config import BACKEND_DIR
from ..db import SessionLocal
from ..models import DailyPrice
from . import prices, universe
from .store import clean, utcnow

log = logging.getLogger(__name__)

UNIVERSES = {
    "SP500": {"name": "S&P 500", "market": "US", "bench": "S&P 500", "commission_bps": 7.0, "sell_tax_bps": 0.0, "gains_tax": True},
    "KOSPI": {"name": "코스피 시총 상위 100", "market": "KR", "bench": "코스피", "commission_bps": 1.5, "sell_tax_bps": 20.0, "gains_tax": False},
}
BENCH = {"S&P 500": "^GSPC", "코스피": "^KS11"}
WINDOW = 504            # bars of history each score sees (the live scan uses 2 years)
MIN_BARS = 252
HISTORY_YEARS = 5
GAINS_TAX_RATE, GAINS_DEDUCTION_KRW = 0.22, 2_500_000
WORKERS = 4

_lock = threading.Lock()
_state: dict[str, dict] = {u: {"state": "idle", "message": None, "progress": 0.0, "started_at": None, "finished_at": None}
                           for u in UNIVERSES}


def _file(u: str) -> Path:
    return Path(BACKEND_DIR) / "data" / f"rotation_scores_{u}.json"


# --------------------------------------------------------------------- data
def _frames(u: str) -> dict[str, pd.DataFrame]:
    """Dividend-adjusted daily OHLCV of today's members, from the local price store."""
    def load():
        syms = universe.members(u)
        rows = []
        with SessionLocal() as db:
            for i in range(0, len(syms), 900):
                rows += db.execute(select(DailyPrice.symbol, DailyPrice.date, DailyPrice.open, DailyPrice.high, DailyPrice.low,
                                          DailyPrice.close, DailyPrice.adj_close, DailyPrice.volume)
                                   .where(DailyPrice.symbol.in_(syms[i:i + 900]))).all()
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        out = {}
        for s, g in df.groupby("symbol"):
            g = rules.adjusted_daily(g.set_index("date").sort_index())
            if len(g) >= MIN_BARS + 21:
                out[s] = g
        return out
    return cached(f"rotation:frames:{u}", 6 * 3600, load)


def _ensure_history(u: str) -> int:
    """Download HISTORY_YEARS of daily bars for members whose stored history is shorter (idempotent)."""
    syms = universe.members(u)
    cutoff = dt.date.today() - dt.timedelta(days=int(365.25 * (HISTORY_YEARS - 1.2)))
    with SessionLocal() as db:
        have = {s for s, d in db.execute(select(DailyPrice.symbol, func.min(DailyPrice.date))
                                         .where(DailyPrice.symbol.in_(syms)).group_by(DailyPrice.symbol)).all()
                if d is not None and d <= cutoff}
    missing = [s for s in syms if s not in have]
    if missing:
        from ..providers.yahoo import yahoo
        frames = yahoo.history_many(missing, period=f"{HISTORY_YEARS}y")
        prices.put_many_daily(frames, years=HISTORY_YEARS)
        cache.delete(f"rotation:frames:{u}")
    return len(missing)


def _month_ends(frames: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    """Last session of every COMPLETED month on the members' common calendar."""
    counts: dict[pd.Timestamp, int] = {}
    for g in frames.values():
        for d in g.index:
            counts[d] = counts.get(d, 0) + 1
    # a real session is one most members traded on (drops stray holiday prints)
    days = pd.DatetimeIndex(sorted(d for d, n in counts.items() if n >= 0.5 * len(frames)))
    if not len(days):
        return []
    s = pd.Series(days, index=days)
    ends = s.groupby([days.year, days.month]).max().tolist()
    return [pd.Timestamp(e) for e in ends[:-1]]       # the last group is the month still in progress


# ------------------------------------------------------------------- scores
def _init_worker() -> None:
    try:
        from . import scoring_style
        scoring_style.install()
    except Exception:
        pass


def _score_symbol(args) -> tuple[str, dict[str, list]]:
    """Technical score of one stock at each date, each seeing only the WINDOW bars up to that date."""
    from .stock import signal_for
    symbol, market, df, dates = args
    out: dict[str, list] = {}
    for d in dates:
        sub = df.loc[:d]
        if not len(sub) or sub.index[-1] != d:
            continue                                   # no bar that day (halted / not listed yet)
        sub = sub.iloc[-WINDOW:]
        if len(sub) < MIN_BARS:
            continue
        try:
            ind = indicators.compute_all(sub)
            sig = signal_for(ind, None, market=market)[0]
            excluded = user_rules.stoch_ml_overbought(ind.iloc[-1]) or bool(user_rules.rsi_ml_oversold(ind))
            if sig.get("score") is not None and np.isfinite(sig["score"]):
                out[str(d.date())] = [round(float(sig["score"]), 2), int(excluded)]
        except Exception:
            continue
    return symbol, out


def _load_scores(u: str) -> dict:
    f = _file(u)
    if not f.exists():
        return {"dates": {}, "window": WINDOW}
    try:
        data = json.loads(f.read_text())
        return data if data.get("window") == WINDOW else {"dates": {}, "window": WINDOW}
    except Exception:
        return {"dates": {}, "window": WINDOW}


def _save_scores(u: str, data: dict) -> None:
    f = _file(u)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(f)


def _missing(u: str, frames: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    done = _load_scores(u)["dates"]
    return [d for d in _month_ends(frames) if str(d.date()) not in done]


def _run(u: str) -> None:
    st = _state[u]
    try:
        cfg = UNIVERSES[u]
        st.update(message="가격 이력 확인 중")
        filled = _ensure_history(u)
        frames = _frames(u)
        todo = _missing(u, frames)
        if todo:
            st.update(message=f"{len(frames)}개 종목 × {len(todo)}개월 점수 계산 중" + (f" (이력 {filled}개 종목 보충)" if filled else ""))
            data = _load_scores(u)
            new: dict[str, dict] = {str(d.date()): {} for d in todo}
            tasks = [(s, cfg["market"], g, todo) for s, g in frames.items()]

            def absorb(sym, res):
                for d, v in res.items():
                    new[d][sym] = v

            try:
                import multiprocessing as mp
                with ProcessPoolExecutor(max_workers=WORKERS, mp_context=mp.get_context("spawn"), initializer=_init_worker) as ex:
                    futs = [ex.submit(_score_symbol, t) for t in tasks]
                    for i, f in enumerate(as_completed(futs), 1):
                        absorb(*f.result())
                        st["progress"] = i / len(futs)
            except Exception:
                log.warning("rotation: process pool failed, scoring in-process", exc_info=True)
                new = {str(d.date()): {} for d in todo}
                for i, t in enumerate(tasks, 1):
                    absorb(*_score_symbol(t))
                    st["progress"] = i / len(tasks)
            data["dates"].update(new)
            data["updated_at"] = utcnow().isoformat()
            _save_scores(u, data)
        cache.delete_prefix(f"rotation:result:{u}")
        st.update(state="done", progress=1.0, finished_at=utcnow().isoformat(),
                  message=f"{len(todo)}개월 추가 계산 완료" if todo else "이미 최신입니다")
    except Exception as e:
        log.exception("rotation scoring failed")
        st.update(state="error", finished_at=utcnow().isoformat(), message=str(e)[:200])


def start(u: str) -> dict:
    with _lock:
        if _state[u]["state"] != "running":
            _state[u].update(state="running", progress=0.0, started_at=utcnow().isoformat(), finished_at=None, message="준비 중")
            threading.Thread(target=_run, args=(u,), name=f"rotation-{u}", daemon=True).start()
    return dict(_state[u])


def status(u: str) -> dict:
    return dict(_state[u])


# --------------------------------------------------------------- simulation
def _wide(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    o = pd.DataFrame({s: g["open"] for s, g in frames.items()}).sort_index()
    c = pd.DataFrame({s: g["close"] for s, g in frames.items()}).sort_index()
    return o, c


def _simulate(o: pd.DataFrame, c: pd.DataFrame, picks_by_trade_day: dict[pd.Timestamp, list[str]], *, capital: float,
              commission: float, slippage: float, sell_tax: float, gains_tax: bool, rebalance: bool = True) -> dict:
    """Daily equity of an equal-weight basket re-picked on each trade day (fills at that day's open)."""
    days = c.index[c.index >= min(picks_by_trade_day)]
    cf = c.ffill()
    cash = capital
    hold: dict[str, list[float]] = {}        # symbol -> [shares, average cost per share incl. buy costs]
    realised = 0.0                            # this calendar year's net realised gain
    paid = {"commission": 0.0, "slippage": 0.0, "sell_tax": 0.0, "gains_tax": 0.0}
    equity, turnovers = [], []
    year = days[0].year

    def liability() -> float:
        return GAINS_TAX_RATE * max(0.0, realised - GAINS_DEDUCTION_KRW) if gains_tax else 0.0

    for d in days:
        if d.year != year:                    # settle last year's tax, start a new tax year
            tax = liability()
            cash -= tax
            paid["gains_tax"] += tax
            realised, year = 0.0, d.year
        if d in picks_by_trade_day and (rebalance or not hold):
            picks = [s for s in picks_by_trade_day[d] if pd.notna(o.at[d, s])]
            px = {s: (o.at[d, s] if pd.notna(o.at[d, s]) else cf.at[d, s]) for s in set(hold) | set(picks)}
            value = cash + sum(sh * px[s] for s, (sh, _) in hold.items())
            if picks and value > 0:
                target = {s: value / len(picks) for s in picks}
                cur = {s: sh * px[s] for s, (sh, _) in hold.items()}
                traded = sum(abs(target.get(s, 0.0) - cur.get(s, 0.0)) for s in set(target) | set(cur))
                # one pass: shrink the targets by what the trades themselves will cost
                value -= traded * (commission + slippage) + sum(max(0.0, cur.get(s, 0.0) - target.get(s, 0.0)) for s in cur) * sell_tax
                target = {s: value / len(picks) for s in picks}
                turnovers.append(traded / (2 * max(value, 1e-9)))
                for s in list(hold):                                   # sells first
                    sh, cost = hold[s]
                    sell_val = cur[s] - target.get(s, 0.0)
                    if sell_val <= 1e-9:
                        continue
                    sold = min(sh, sell_val / px[s])
                    net = sold * px[s] * (1 - commission - slippage - sell_tax)
                    paid["commission"] += sold * px[s] * commission
                    paid["slippage"] += sold * px[s] * slippage
                    paid["sell_tax"] += sold * px[s] * sell_tax
                    realised += net - sold * cost
                    cash += net
                    if sh - sold <= 1e-9:
                        del hold[s]
                    else:
                        hold[s][0] = sh - sold
                for s in picks:                                        # then buys, never more than the cash at hand
                    buy_val = target[s] - (hold[s][0] * px[s] if s in hold else 0.0)
                    if buy_val <= 1e-9:
                        continue
                    spend = min(buy_val * (1 + commission + slippage), max(cash, 0.0))
                    got = spend / (px[s] * (1 + commission + slippage))
                    paid["commission"] += got * px[s] * commission
                    paid["slippage"] += got * px[s] * slippage
                    cash -= spend
                    if s in hold:
                        sh, cost = hold[s]
                        hold[s] = [sh + got, (sh * cost + spend) / (sh + got)]
                    else:
                        hold[s] = [got, spend / got] if got > 0 else [0.0, 0.0]
        equity.append(cash + sum(sh * cf.at[d, s] for s, (sh, _) in hold.items() if pd.notna(cf.at[d, s])) - liability())
    eq = pd.Series(equity, index=days)
    return {"equity": eq, "paid": paid, "tax_accrued": liability(),
            "avg_turnover": float(np.mean(turnovers[1:])) if len(turnovers) > 1 else None,
            "holdings": sorted(hold)}


def _yearly(curves: dict[str, pd.Series]) -> list[dict]:
    years = sorted({d.year for s in curves.values() for d in s.index})
    out = []
    for y in years:
        row = {"year": y}
        for k, s in curves.items():
            part = s[s.index.year == y]
            prev = s[s.index.year < y]
            base = prev.iloc[-1] if len(prev) else (part.iloc[0] if len(part) else None)
            row[k] = float(part.iloc[-1] / base - 1) if len(part) and base else None
        out.append(row)
    return out


def _points(s: pd.Series, step: int) -> list[dict]:
    idx = list(range(0, len(s), step))
    if idx and idx[-1] != len(s) - 1:
        idx.append(len(s) - 1)
    return [{"date": str(s.index[i].date()), "value": round(float(s.iloc[i]), 2)} for i in idx]


def result(u: str, top_n: int = 10, *, commission_bps: float | None = None, slippage_bps: float = 5.0,
           sell_tax_bps: float | None = None, gains_tax: bool | None = None, capital: float = 10_000_000) -> dict:
    cfg = UNIVERSES[u]
    commission_bps = cfg["commission_bps"] if commission_bps is None else commission_bps
    sell_tax_bps = cfg["sell_tax_bps"] if sell_tax_bps is None else sell_tax_bps
    gains_tax = cfg["gains_tax"] if gains_tax is None else gains_tax
    key = f"rotation:result:{u}:{top_n}:{commission_bps}:{slippage_bps}:{sell_tax_bps}:{int(gains_tax)}:{capital}"

    def build():
        frames = _frames(u)
        scores = _load_scores(u)["dates"]
        missing = _missing(u, frames) if frames else []
        if not frames or missing:
            return None
        o, c = _wide(frames)
        meta = universe.constituents().get("meta", {})
        ends = [d for d in _month_ends(frames) if str(d.date()) in scores]
        picks_by_trade_day: dict[pd.Timestamp, list[str]] = {}
        rebalances = []
        for t in ends:
            day = scores[str(t.date())]
            ranked = sorted(((s, v[0]) for s, v in day.items() if not v[1] and s in c.columns), key=lambda x: -x[1])
            later = c.index[c.index > t]
            # months where only a handful of members had enough history would be a different (biased) universe
            if len(day) < 0.5 * len(frames) or len(ranked) < top_n or not len(later):
                continue
            trade_day = later[0]
            picks = ranked[:top_n]
            picks_by_trade_day[trade_day] = [s for s, _ in picks]
            rebalances.append({"signal_date": str(t.date()), "trade_date": str(trade_day.date()),
                               "scored": len(day), "excluded": sum(1 for v in day.values() if v[1]),
                               "picks": [{"symbol": s, "name": (meta.get(s) or {}).get("name_ko") or (meta.get(s) or {}).get("name") or s,
                                          "score": sc} for s, sc in picks]})
        if len(picks_by_trade_day) < 6:
            return {"status": "insufficient", "message": "리밸런싱할 수 있는 달이 6개월 미만입니다 (가격 이력 부족)"}
        cost = dict(capital=capital, commission=commission_bps / 1e4, slippage=slippage_bps / 1e4, sell_tax=sell_tax_bps / 1e4)
        net = _simulate(o, c, picks_by_trade_day, gains_tax=gains_tax, **cost)
        gross = _simulate(o, c, picks_by_trade_day, capital=capital, commission=0.0, slippage=0.0, sell_tax=0.0, gains_tax=False)
        first = min(picks_by_trade_day)
        hold = _simulate(o, c, {first: picks_by_trade_day[first]}, gains_tax=False, rebalance=False, **cost)
        eq = net["equity"]
        days = eq.index
        # equal-weight universe: the average member, rebalanced daily, no costs (what "any stock" did)
        r = c.loc[days].ffill().pct_change()
        r.iloc[0] = (c.loc[days[0]] / o.loc[days[0]] - 1)
        ew = capital * (1 + r.mean(axis=1, skipna=True).fillna(0)).cumprod()
        curves = {"strategy": eq, "gross": gross["equity"], "hold_first": hold["equity"], "equal_weight": ew}
        names = {"strategy": f"상위 {top_n} 매달 교체 (비용·세금 후)", "gross": "교체 전략 (비용·세금 전)",
                 "hold_first": f"첫 상위 {top_n} 그대로 보유", "equal_weight": f"{cfg['name']} 동일가중"}
        for name, b in BENCH.items():
            try:
                bc = prices.get_daily(b, years=HISTORY_YEARS + 1).df["close"]
                seg = bc.reindex(bc.index.union(days)).ffill().reindex(days).dropna()
                if len(seg) > 20:
                    k = "sp500" if b == "^GSPC" else "kospi"
                    curves[k] = seg / seg.iloc[0] * capital
                    names[k] = f"{name} 지수"
            except Exception:
                log.warning("rotation: benchmark %s unavailable", b, exc_info=True)
        # what each month's basket did until the next rebalance
        tds = sorted(picks_by_trade_day)
        for i, rb in enumerate(rebalances):
            a = tds[i]
            b = tds[i + 1] if i + 1 < len(tds) else days[-1]
            prev = eq[eq.index < a]
            base = prev.iloc[-1] if len(prev) else capital
            upto = eq[eq.index < b] if i + 1 < len(tds) else eq
            rb["return"] = float(upto.iloc[-1] / base - 1) if len(upto) else None
            rb["open"] = i + 1 == len(tds)
        step = max(1, len(eq) // 400)
        m = {k: bt.metrics(s) for k, s in curves.items()}
        paid = net["paid"]
        notes = [
            f"유니버스는 오늘 기준 {cfg['name']} 구성 종목 {len(frames)}개입니다. 과거에 빠진 종목이 없어 실제보다 좋게 나올 수 있습니다 (생존 편향).",
            f"저장된 가격 이력 때문에 첫 리밸런싱은 {rebalances[0]['trade_date']}입니다. {len(rebalances)}개월은 통계적으로 짧은 기간입니다.",
            "순위는 매달 말 종가까지의 데이터만으로 다시 계산했고 (기술 점수, 과매수·과매도 제외 규칙 동일), 다음 거래일 시가에 체결했습니다. 매크로 국면 가점은 과거 값을 복원할 수 없어 뺐습니다.",
            f"비용: 수수료 {commission_bps:g}bp + 슬리피지 {slippage_bps:g}bp (매매마다)" + (f", 매도 시 거래세 {sell_tax_bps:g}bp" if sell_tax_bps else "")
            + (f", 해외주식 양도세 22% (연 실현이익 250만원 공제, 초기자금 {capital:,.0f}원 기준)" if gains_tax else "") + ". 환율 변동은 반영하지 않았습니다.",
            "주가는 배당을 반영한 수정주가이고, S&P 500·코스피는 배당이 빠진 가격 지수입니다. '그대로 보유'는 팔지 않으므로 세금이 0입니다.",
        ]
        current = rebalances[-1]
        return {"status": "ready", "universe": u, "universe_name": cfg["name"], "top_n": top_n,
                "params": {"commission_bps": commission_bps, "slippage_bps": slippage_bps, "sell_tax_bps": sell_tax_bps,
                           "gains_tax": gains_tax, "capital": capital},
                "metrics": m, "names": names,
                "equity": {k: _points(s, step) for k, s in curves.items()},
                "yearly": _yearly(curves),
                "costs": {**paid, "tax_accrued": net["tax_accrued"], "total": sum(paid.values()),
                          "drag": (m["gross"]["cagr"] - m["strategy"]["cagr"]) if m.get("gross") and m.get("strategy") else None},
                "avg_turnover": net["avg_turnover"],
                "rebalances": rebalances[::-1][:36], "months": len(rebalances),
                "current": {"trade_date": current["trade_date"], "picks": current["picks"]},
                "data": {"members": len(frames), "start": str(days[0].date()), "end": str(days[-1].date()),
                         "scores_updated_at": _load_scores(u).get("updated_at")},
                "notes": notes, "updated_at": utcnow().isoformat(),
                "disclaimer": "과거 데이터에 규칙을 적용한 결과이며 미래 수익을 보장하지 않습니다."}

    out = cached(key, 1800, build)
    if out is None:
        cache.delete(key)
        frames = _frames(u)
        todo = _missing(u, frames) if frames else []
        return clean({"status": "needs_prepare", "universe": u, "universe_name": cfg["name"], "job": status(u),
                      "missing_months": len(todo), "members": len(frames),
                      "message": "순위를 과거 시점별로 다시 계산해야 합니다 (처음 한 번, 몇 분 걸림)."})
    return clean(out)
