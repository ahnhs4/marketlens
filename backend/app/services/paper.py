"""Paper trading (stage 1 of the automated-trading plan): rule strategies trade a simulated USD account
on live quotes. No broker is contacted and no real order is ever sent.

Each cycle (every minute from the scheduler, or "지금 실행"):
  1. New day (New York): remember the day's starting equity; a daily-loss halt from yesterday ends.
  2. Equity down more than the daily loss limit -> halt: no new entries today, exits still run.
  3. For every enabled strategy and symbol, during the regular US session only:
     stop hit or last bar before the close -> sell; a newly completed bar -> evaluate the rules
     (same code as the backtest) and buy / sell at the live quote ± slippage, fee on each side.
The emergency stop (paused) blocks every automated order until it is lifted.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..analysis import rules
from ..db import SessionLocal
from ..models import PaperAccount, PaperEquity, PaperEvent, PaperPosition, PaperStrategy, PaperTrade, User
from . import clock, prices
from .store import clean

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
_cycle_lock = threading.Lock()


# ------------------------------------------------------------------ helpers
def account(db: Session, user: User) -> PaperAccount:
    a = db.execute(select(PaperAccount).where(PaperAccount.user_id == user.id)).scalar_one_or_none()
    if a is None:
        a = PaperAccount(user_id=user.id, start_cash=10_000.0, cash=10_000.0, fee_bps=25.0, slippage_bps=5.0, daily_loss_limit_pct=3.0)
        db.add(a)
        db.commit()
    return a


def _price(symbol: str) -> float | None:
    try:
        return float(prices.get_quote(symbol).price)
    except Exception:
        return None


def _event(db: Session, a: PaperAccount, kind: str, message: str, strategy_id: int | None = None, symbol: str | None = None) -> None:
    db.add(PaperEvent(account_id=a.id, strategy_id=strategy_id, symbol=symbol, kind=kind, message=message[:400]))


def positions(db: Session, a: PaperAccount) -> list[PaperPosition]:
    return list(db.execute(select(PaperPosition).where(PaperPosition.account_id == a.id)).scalars())


def equity(db: Session, a: PaperAccount, px: dict[str, float | None] | None = None) -> float:
    px = px if px is not None else {}
    total = a.cash
    for p in positions(db, a):
        price = px.get(p.symbol) if p.symbol in px else _price(p.symbol)
        total += p.qty * (price if price is not None else p.avg_price)
    return total


def buy(db: Session, a: PaperAccount, symbol: str, notional: float, price: float, reason: str,
        strategy_id: int | None = None, stop: float | None = None) -> PaperTrade | None:
    """Spend up to `notional` (and never more than the cash) at `price` + slippage, fee included."""
    fill = price * (1 + a.slippage_bps / 1e4)
    spend = min(notional, a.cash)
    if spend < 1:
        return None
    qty = spend / (fill * (1 + a.fee_bps / 1e4))
    fee = qty * fill * a.fee_bps / 1e4
    a.cash -= qty * fill + fee
    pos = db.execute(select(PaperPosition).where(PaperPosition.account_id == a.id, PaperPosition.symbol == symbol,
                                                 PaperPosition.strategy_id.is_(strategy_id) if strategy_id is None
                                                 else PaperPosition.strategy_id == strategy_id)).scalar_one_or_none()
    cost = qty * fill + fee
    if pos:
        pos.avg_price = (pos.avg_price * pos.qty + cost) / (pos.qty + qty)
        pos.qty += qty
        pos.stop_price = stop or pos.stop_price
    else:
        db.add(PaperPosition(account_id=a.id, strategy_id=strategy_id, symbol=symbol, qty=qty, avg_price=cost / qty, stop_price=stop))
    t = PaperTrade(account_id=a.id, strategy_id=strategy_id, symbol=symbol, side="buy", qty=qty, price=fill, fee=fee,
                   realized_pl=None, reason=reason[:300])
    db.add(t)
    return t


def sell(db: Session, a: PaperAccount, pos: PaperPosition, price: float, reason: str, qty: float | None = None) -> PaperTrade:
    qty = min(qty or pos.qty, pos.qty)
    fill = price * (1 - a.slippage_bps / 1e4)
    fee = qty * fill * a.fee_bps / 1e4
    proceeds = qty * fill - fee
    a.cash += proceeds
    t = PaperTrade(account_id=a.id, strategy_id=pos.strategy_id, symbol=pos.symbol, side="sell", qty=qty, price=fill, fee=fee,
                   realized_pl=proceeds - qty * pos.avg_price, reason=reason[:300])
    db.add(t)
    pos.qty -= qty
    if pos.qty <= 1e-9:
        db.delete(pos)
    return t


def snapshot(db: Session, a: PaperAccount, px: dict | None = None) -> None:
    db.add(PaperEquity(account_id=a.id, equity=equity(db, a, px), cash=a.cash))


# ------------------------------------------------------------------ live engine
def _bars(symbol: str, interval: str, now: pd.Timestamp) -> pd.DataFrame:
    """Completed bars at actual traded prices (live quotes and stops are compared against these)."""
    if interval == "1d":
        df = prices.get_daily(symbol, years=2).df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    else:
        df = rules.regular(prices.get_intraday(symbol, interval, rules.LIVE_HISTORY[interval]))
    return rules.completed(df, interval, now)


def run_cycle(now: dt.datetime | None = None) -> dict:
    """One pass over every account that has an enabled strategy (or open automated positions)."""
    if not _cycle_lock.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        with SessionLocal() as db:
            ids = {s.account_id for s in db.execute(select(PaperStrategy).where(PaperStrategy.enabled.is_(True))).scalars()}
            ids |= {p.account_id for p in db.execute(select(PaperPosition).where(PaperPosition.strategy_id.is_not(None))).scalars()}
            out = {}
            for aid in ids:
                a = db.get(PaperAccount, aid)
                try:
                    out[aid] = run_account(db, a, now)
                    db.commit()
                except Exception as e:
                    db.rollback()
                    log.exception("paper cycle failed for account %s", aid)
                    out[aid] = {"error": str(e)[:200]}
            return out
    finally:
        _cycle_lock.release()


def run_account(db: Session, a: PaperAccount, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    ts = pd.Timestamp(now).tz_convert("UTC")
    st = clock.status("US", now)
    if a.paused:
        return {"state": "paused"}
    if st["state"] != "open":
        return {"state": "market closed"}
    today = ts.tz_convert(ET).date()
    strategies = list(db.execute(select(PaperStrategy).where(PaperStrategy.account_id == a.id)).scalars())
    held = [p for p in positions(db, a) if p.strategy_id is not None]
    syms = sorted({s for st_ in strategies if st_.enabled for s in st_.symbols} | {p.symbol for p in held})
    px = {s: _price(s) for s in syms}
    eq = equity(db, a, px)
    if a.day != today:
        a.day, a.day_start_equity = today, eq
        if a.halted_day and a.halted_day != today:
            a.halted_day = None
            _event(db, a, "risk", "새 거래일 - 일일 손실 한도 정지 해제")
    if a.halted_day != today and a.day_start_equity and eq / a.day_start_equity - 1 <= -a.daily_loss_limit_pct / 100:
        a.halted_day = today
        _event(db, a, "risk", f"일일 손실 한도 도달 ({(eq / a.day_start_equity - 1) * 100:.2f}% ≤ −{a.daily_loss_limit_pct:.1f}%) - 오늘은 신규 매수 중단, 청산만 진행")
    halted = a.halted_day == today
    close = pd.Timestamp(st["session_close"]) if st.get("session_close") else None
    acted = 0

    def exit_(pos: PaperPosition, price: float, why: str, label: str, sid: int, sym: str) -> None:
        nonlocal acted
        t = sell(db, a, pos, price, why)
        _event(db, a, "order", f"{sym} {label} {t.qty:.4f}주 @ {t.price:,.2f}", sid, sym)
        held.remove(pos)
        acted += 1

    for s in strategies:
        kind = rules.KINDS.get(s.kind)
        if kind is None:
            continue
        daily = kind.timeframe == "daily"
        flatten = kind.day_trade or (s.flatten_at_close and not daily)
        near_close = close is not None and ts >= close - pd.Timedelta(minutes=10 if daily else max(rules.INTERVALS[s.interval], 10))
        state = dict(s.state or {})
        p = {**kind.defaults, **(s.params or {})}
        for sym in (s.symbols if s.enabled else []) + [x.symbol for x in held if x.strategy_id == s.id and x.symbol not in s.symbols]:
            price = px.get(sym)
            pos = next((x for x in held if x.strategy_id == s.id and x.symbol == sym), None)
            if price is None:
                state[sym] = {**state.get(sym, {}), "note": "시세 없음 (Data unavailable)"}
                continue
            # risk exits first, on the live price
            if pos and pos.stop_price and price <= pos.stop_price:
                exit_(pos, price, f"손절선 {pos.stop_price:,.2f} 도달 (현재 {price:,.2f})", "손절 매도", s.id, sym)
                pos = None
            if pos and flatten and near_close:
                exit_(pos, price, "장 마감 전 청산", "장 마감 전 청산", s.id, sym)
                pos = None
            if not s.enabled:
                continue
            try:
                bars = _bars(sym, s.interval, ts)
            except Exception as e:
                state[sym] = {**state.get(sym, {}), "note": f"봉 데이터 없음: {str(e)[:60]}"}
                continue
            if len(bars) < (210 if daily else 60):
                state[sym] = {**state.get(sym, {}), "note": "봉 데이터 부족"}
                continue
            sig_all = rules.signals(s.kind, bars, p)
            sig = sig_all.iloc[-1]
            if kind.intrabar:  # volatility breakout: today's level = today's open + k × yesterday's range
                note = state.get(sym, {}).get("note", "")
                if state.get(sym, {}).get("traded_day") != today.isoformat() and not pos:
                    try:
                        today_bars = rules.regular(prices.get_intraday(sym, "5m", "5d"))
                        today_bars = today_bars[pd.Index(today_bars.index.tz_convert(ET).date) == today]
                    except Exception:
                        today_bars = pd.DataFrame()
                    if len(today_bars):
                        level = float(today_bars["open"].iloc[0]) + float(p["k"]) * float(bars["high"].iloc[-1] - bars["low"].iloc[-1])
                        note = f"오늘 돌파선 {level:,.2f} (현재 {price:,.2f})"
                        if price >= level and not near_close and not halted and a.cash >= 1:
                            stop = price - float(p.get("atr_stop") or 0) * float(sig["atr"]) if float(p.get("atr_stop") or 0) > 0 and pd.notna(sig["atr"]) else None
                            t = buy(db, a, sym, s.max_position_usd, price, f"변동성 돌파: 돌파선 {level:,.2f} 통과", s.id, stop)
                            if t:
                                _event(db, a, "order", f"{sym} 매수 {t.qty:.4f}주 @ {t.price:,.2f} - 돌파선 {level:,.2f} 통과", s.id, sym)
                                acted += 1
                                note = f"매수 · 돌파선 {level:,.2f} 통과"
                                state[sym] = {**state.get(sym, {}), "traded_day": today.isoformat()}
                state[sym] = {**state.get(sym, {}), "note": note, "checked_at": ts.isoformat()}
                continue
            last = bars.index[-1].isoformat()
            if state.get(sym, {}).get("last_bar") == last:
                continue  # nothing new since the last evaluation
            note = "신호 없음"
            if pos and bool(sig["exit"]):
                exit_(pos, price, f"청산 신호: {sig['why_exit']}", f"매도 - {sig['why_exit']}", s.id, sym)
                note = f"청산 · {sig['why_exit']}"
            elif not pos and bool(sig["entry"]):
                if halted:
                    note = "매수 신호 - 일일 손실 한도로 건너뜀"
                    _event(db, a, "skip", f"{sym} {note} ({sig['why_entry']})", s.id, sym)
                elif flatten and near_close:
                    note = "매수 신호 - 장 마감 직전이라 건너뜀"
                elif a.cash < 1:
                    note = "매수 신호 - 현금 부족"
                    _event(db, a, "skip", f"{sym} {note}", s.id, sym)
                else:
                    k_atr = float(p.get("atr_stop") or 0)
                    if "stop" in sig_all and pd.notna(sig.get("stop")):
                        stop = float(sig["stop"])
                    else:
                        stop = price - k_atr * float(sig["atr"]) if k_atr > 0 and pd.notna(sig["atr"]) else None
                    t = buy(db, a, sym, s.max_position_usd, price, f"매수 신호: {sig['why_entry']}", s.id, stop)
                    if t:
                        _event(db, a, "order", f"{sym} 매수 {t.qty:.4f}주 @ {t.price:,.2f} - {sig['why_entry']}"
                               + (f" · 손절선 {stop:,.2f}" if stop else ""), s.id, sym)
                        note, acted = f"매수 · {sig['why_entry']}", acted + 1
            elif pos:
                note = "보유 중 - 청산 신호 없음"
            state[sym] = {**state.get(sym, {}), "last_bar": last, "note": note, "checked_at": ts.isoformat()}
        s.state = state
    if acted or not db.execute(select(PaperEquity.id).where(PaperEquity.account_id == a.id,
                                                             PaperEquity.ts >= (ts - pd.Timedelta(minutes=5)).to_pydatetime())).first():
        db.flush()
        snapshot(db, a, px)
    return {"state": "ran", "orders": acted, "halted": halted}


# ------------------------------------------------------------------ views
def summary(db: Session, a: PaperAccount) -> dict:
    pos = positions(db, a)
    px = {p.symbol: _price(p.symbol) for p in pos}
    eq = equity(db, a, px)
    strategies = list(db.execute(select(PaperStrategy).where(PaperStrategy.account_id == a.id).order_by(PaperStrategy.id)).scalars())
    trades = list(db.execute(select(PaperTrade).where(PaperTrade.account_id == a.id).order_by(PaperTrade.ts.desc()).limit(200)).scalars())
    events = list(db.execute(select(PaperEvent).where(PaperEvent.account_id == a.id).order_by(PaperEvent.ts.desc()).limit(100)).scalars())
    curve = list(db.execute(select(PaperEquity).where(PaperEquity.account_id == a.id).order_by(PaperEquity.ts)).scalars())
    names = {s.id: s.name for s in strategies}
    all_sells = list(db.execute(select(PaperTrade).where(PaperTrade.account_id == a.id, PaperTrade.side == "sell")).scalars())
    fees = sum(t.fee for t in db.execute(select(PaperTrade).where(PaperTrade.account_id == a.id)).scalars())

    def stats(sid: int | None) -> dict:
        ss = [t for t in all_sells if t.strategy_id == sid]
        wins = [t for t in ss if (t.realized_pl or 0) > 0]
        return {"closed": len(ss), "win_rate": len(wins) / len(ss) * 100 if ss else None,
                "realized_pl": sum(t.realized_pl or 0 for t in ss)}
    us = clock.status("US")
    return clean({
        "account": {"cash": a.cash, "equity": eq, "start_cash": a.start_cash, "total_pl": eq - a.start_cash,
                    "total_ret_pct": (eq / a.start_cash - 1) * 100 if a.start_cash else None,
                    "day_pl": eq - a.day_start_equity if a.day_start_equity and a.day == dt.datetime.now(ET).date() else None,
                    "fee_bps": a.fee_bps, "slippage_bps": a.slippage_bps, "daily_loss_limit_pct": a.daily_loss_limit_pct,
                    "paused": a.paused, "halted_today": a.halted_day == dt.datetime.now(ET).date(), "fees_paid": fees},
        "market": us,
        "positions": [{"id": p.id, "symbol": p.symbol, "strategy_id": p.strategy_id, "strategy": names.get(p.strategy_id, "수동"),
                       "qty": p.qty, "avg_price": p.avg_price, "price": px.get(p.symbol), "stop_price": p.stop_price,
                       "value": p.qty * px[p.symbol] if px.get(p.symbol) is not None else None,
                       "pl": p.qty * (px[p.symbol] - p.avg_price) if px.get(p.symbol) is not None else None,
                       "pl_pct": (px[p.symbol] / p.avg_price - 1) * 100 if px.get(p.symbol) is not None else None,
                       "opened_at": p.opened_at.isoformat()} for p in pos],
        "strategies": [{"id": s.id, "name": s.name, "kind": s.kind, "kind_name": rules.KINDS[s.kind].name if s.kind in rules.KINDS else s.kind, "interval": s.interval,
                        "params": s.params, "symbols": s.symbols, "max_position_usd": s.max_position_usd,
                        "flatten_at_close": s.flatten_at_close, "enabled": s.enabled, "state": s.state or {}, **stats(s.id)}
                       for s in strategies],
        "manual": stats(None),
        "trades": [{"id": t.id, "ts": t.ts.isoformat(), "symbol": t.symbol, "side": t.side, "qty": t.qty, "price": t.price,
                    "fee": t.fee, "realized_pl": t.realized_pl, "reason": t.reason, "strategy": names.get(t.strategy_id, "수동")}
                   for t in trades],
        "events": [{"ts": e.ts.isoformat(), "kind": e.kind, "symbol": e.symbol, "message": e.message,
                    "strategy": names.get(e.strategy_id) if e.strategy_id else None} for e in events],
        "equity_curve": [{"ts": c.ts.isoformat(), "equity": c.equity} for c in curve[-2000:]],
        "kinds": [{"id": k.id, "name": k.name, "family": k.family, "timeframe": k.timeframe, "intervals": list(k.intervals),
                   "day_trade": k.day_trade, "description": k.description, "defaults": k.defaults, "labels": k.labels,
                   "source": {"title": k.source[0], "url": k.source[1]} if k.source else None} for k in rules.KINDS.values()],
        "intervals": list(rules.INTERVALS),
        "method": __doc__,
    })


def reset(db: Session, a: PaperAccount, start_cash: float) -> None:
    for model in (PaperPosition, PaperTrade, PaperEvent, PaperEquity):
        db.execute(delete(model).where(model.account_id == a.id))
    for s in db.execute(select(PaperStrategy).where(PaperStrategy.account_id == a.id)).scalars():
        s.state, s.enabled = {}, False
    a.start_cash = a.cash = start_cash
    a.paused, a.halted_day, a.day, a.day_start_equity = False, None, None, None
    db.flush()
    snapshot(db, a, {})
    _event(db, a, "risk", f"계좌 초기화 - 시작 자금 ${start_cash:,.0f}, 모든 전략 꺼짐")


def backtest(kind: str, params: dict, symbols: list[str], interval: str, *, notional: float, fee_bps: float,
             slippage_bps: float, flatten: bool) -> dict:
    """The rule over history: 10 years of daily bars, or what Yahoo serves intraday (60 days of 5m/15m, ~2 years of 1h)."""
    k = rules.KINDS[kind]
    daily = k.timeframe == "daily"
    rows, total, total_nofee = [], [], []
    for sym in symbols:
        try:
            df = rules.adjusted_daily(prices.get_daily(sym, years=10).df) if daily else prices.get_intraday(sym, interval, rules.HISTORY[interval])
        except Exception as e:
            rows.append({"symbol": sym, "error": f"데이터 없음: {str(e)[:80]}"})
            continue
        r = rules.simulate(df, kind, params, notional=notional, fee_bps=fee_bps, slippage_bps=slippage_bps, flatten=flatten)
        if r.get("error"):
            rows.append({"symbol": sym, "error": r["error"]})
            continue
        r0 = rules.simulate(df, kind, params, notional=notional, fee_bps=0, slippage_bps=0, flatten=flatten)
        rows.append({"symbol": sym, **r["metrics"], "pl_no_cost": r0["metrics"]["total_pl"], "recent": r["trades"][-5:]})
        total.append(r["metrics"])
        total_nofee.append(r0["metrics"])
    agg = None
    if total:
        n = sum(m["trades"] for m in total)
        wins = sum((m["win_rate"] or 0) / 100 * m["trades"] for m in total)
        agg = {"trades": n, "win_rate": wins / n * 100 if n else None, "total_pl": sum(m["total_pl"] for m in total),
               "fees": sum(m["fees"] for m in total), "pl_no_cost": sum(m["total_pl"] for m in total_nofee),
               "days": max(m["days"] or 0 for m in total), "notional": notional,
               "from": min(m["from"] for m in total), "to": max(m["to"] for m in total)}
    return clean({"rows": rows, "total": agg, "interval": "1d" if daily else interval, "kind": kind, "params": params,
                  "method": rules.__doc__,
                  "note": "과거 데이터에 규칙을 적용한 결과이며 미래 수익을 보장하지 않습니다. 체결은 다음 봉 시가 + 슬리피지로 가정합니다."})
