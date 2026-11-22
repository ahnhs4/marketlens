"""Trade journal: the trades the user actually made, entered by hand, with per-strategy statistics.

Private by design - nothing here is part of the public demo snapshot, and nothing here sends an order.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TradeJournal
from .store import clean


def _row(t: TradeJournal) -> dict:
    closed = t.exit_date is not None and t.exit_price is not None
    cost = t.entry_price * t.quantity
    pnl = ret = None
    if closed and cost:
        pnl = (t.exit_price - t.entry_price) * t.quantity - (t.fees or 0)
        ret = pnl / cost
    risk = (t.entry_price - t.stop_price) if t.stop_price else None
    r_mult = ((t.exit_price - t.entry_price) / risk) if closed and risk and risk > 0 else None
    planned_rr = ((t.target_price - t.entry_price) / risk) if t.target_price and risk and risk > 0 else None
    days = ((t.exit_date or dt.date.today()) - t.entry_date).days
    return {"id": t.id, "symbol": t.symbol, "name": t.name, "currency": t.currency, "strategy": t.strategy or "미분류",
            "signal": t.signal, "followed": t.followed, "entry_date": str(t.entry_date), "entry_price": t.entry_price,
            "quantity": t.quantity, "stop_price": t.stop_price, "target_price": t.target_price,
            "exit_date": str(t.exit_date) if t.exit_date else None, "exit_price": t.exit_price, "fees": t.fees or 0,
            "note": t.note, "closed": closed, "pnl": pnl, "return": ret, "r_multiple": r_mult,
            "planned_rr": planned_rr, "holding_days": days,
            "created_at": t.created_at.isoformat() if t.created_at else None}


def _stats(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["closed"] and r["return"] is not None]
    rets = [r["return"] for r in closed]
    wins, losses = [x for x in rets if x > 0], [x for x in rets if x <= 0]
    gain, loss = sum(wins), -sum(losses)
    rm = [r["r_multiple"] for r in closed if r["r_multiple"] is not None]
    by_ccy: dict[str, float] = {}
    for r in closed:
        by_ccy[r["currency"] or "—"] = by_ccy.get(r["currency"] or "—", 0.0) + (r["pnl"] or 0)
    return {"trades": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
            "win_rate": (len(wins) / len(closed)) if closed else None,
            "avg_return": (sum(rets) / len(rets)) if rets else None,
            "avg_win": (sum(wins) / len(wins)) if wins else None,
            "avg_loss": (sum(losses) / len(losses)) if losses else None,
            "profit_factor": (gain / loss) if loss > 0 else None,
            "expectancy_r": (sum(rm) / len(rm)) if rm else None,
            "avg_holding_days": (sum(r["holding_days"] for r in closed) / len(closed)) if closed else None,
            "best": max(rets) if rets else None, "worst": min(rets) if rets else None,
            "pnl_by_currency": by_ccy,
            "followed_rate": (sum(1 for r in rows if r["followed"]) / len(rows)) if rows else None}


def overview(db: Session, user_id: int) -> dict:
    items = [_row(t) for t in db.execute(select(TradeJournal).where(TradeJournal.user_id == user_id)
                                         .order_by(TradeJournal.entry_date.desc(), TradeJournal.id.desc())).scalars()]
    groups: dict[str, list[dict]] = {}
    for r in items:
        groups.setdefault(r["strategy"], []).append(r)
    by_strategy = [{"strategy": k, **_stats(v)} for k, v in groups.items()]
    by_strategy.sort(key=lambda x: (-(x["closed"]), x["strategy"]))
    followed = [r for r in items if r["followed"]]
    ignored = [r for r in items if not r["followed"]]
    return clean({"items": items, "overall": _stats(items), "by_strategy": by_strategy,
                  "discipline": {"followed": _stats(followed), "deviated": _stats(ignored)},
                  "note": "수익률은 (매도가 − 매수가) × 수량 − 수수료를 매수 금액으로 나눈 값입니다. 환율 변동은 반영하지 않습니다. "
                          "R 배수는 손절가를 적은 거래만 계산합니다 (손익 ÷ 처음 감수한 1주당 손실)."})
