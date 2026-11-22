"""Strategy lab: rule builder, trade plan, monthly rotation, trade journal and the at-a-glance views.

Nothing here places an order - the journal stores what the user typed, the plan is a calculation.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..analysis import backtest as bt
from ..analysis import builder, regime_perf
from ..db import get_db
from ..models import Backtest, CustomStrategy, TradeJournal, User
from ..security import current_user
from ..services import journal, overview, regimes, rotation, trade_plan, universe
from ..services.clock import market_of
from ..services.store import clean, utcnow
from .common import guard, sym
from .market import _watchlist_symbols
from .stock import _bench_frames, _eq, _load_bt, _regime_series, _years_for

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
MAX_SAVED = 30


def _bad(code: str, message: str, status: int = 422) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _spec(raw: dict) -> dict:
    try:
        return builder.normalize(raw)
    except ValueError as e:
        raise _bad("invalid_strategy", str(e))


def regime_breakdown(symbol: str, years: int, equity: pd.Series, hold: dict | None, trades: list[dict]) -> dict | None:
    """Per-regime split of a finished backtest; a failure here must never fail the backtest itself."""
    try:
        return regime_perf.breakdown(equity, hold["equity"] if hold else None, trades, regimes.dimensions(symbol, years))
    except Exception:
        log.warning("regime breakdown failed for %s", symbol, exc_info=True)
        return None


# ------------------------------------------------------------------ builder
@router.get("/builder/catalog")
def api_builder_catalog():
    return builder.catalog()


class StrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    spec: dict
    note: str | None = Field(None, max_length=300)


def _saved(s: CustomStrategy) -> dict:
    return {"id": s.id, "name": s.name, "spec": s.spec, "note": s.note, "description": builder.describe(s.spec),
            "updated_at": s.updated_at.isoformat() if s.updated_at else None}


@router.get("/builder/strategies")
def api_builder_list(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(CustomStrategy).where(CustomStrategy.user_id == user.id)
                      .order_by(CustomStrategy.updated_at.desc())).scalars().all()
    return {"items": [_saved(r) for r in rows], "max": MAX_SAVED}


@router.post("/builder/strategies")
def api_builder_save(req: StrategyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    spec, name = _spec(req.spec), req.name.strip()
    if not name:
        raise _bad("invalid_input", "전략 이름을 입력하세요")
    row = db.execute(select(CustomStrategy).where(CustomStrategy.user_id == user.id, CustomStrategy.name == name)).scalar_one_or_none()
    if row is None:
        n = len(db.execute(select(CustomStrategy.id).where(CustomStrategy.user_id == user.id)).all())
        if n >= MAX_SAVED:
            raise _bad("limit", f"전략은 {MAX_SAVED}개까지 저장할 수 있습니다", 409)
        row = CustomStrategy(user_id=user.id, name=name, spec=spec, note=req.note)
        db.add(row)
    else:                       # same name = overwrite, like saving a file
        row.spec, row.note, row.updated_at = spec, req.note, utcnow()
    db.commit()
    db.refresh(row)
    return _saved(row)


def _own_strategy(db: Session, user: User, sid: int) -> CustomStrategy:
    row = db.get(CustomStrategy, sid)
    if row is None or row.user_id != user.id:
        raise _bad("not_found", "저장된 전략을 찾을 수 없습니다", 404)
    return row


@router.put("/builder/strategies/{sid}")
def api_builder_update(sid: int, req: StrategyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _own_strategy(db, user, sid)
    row.name, row.spec, row.note, row.updated_at = req.name.strip(), _spec(req.spec), req.note, utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _bad("duplicate", "같은 이름의 전략이 이미 있습니다", 409)
    db.refresh(row)
    return _saved(row)


@router.delete("/builder/strategies/{sid}")
def api_builder_delete(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_strategy(db, user, sid))
    db.commit()
    return {"ok": True}


class BuilderRun(BaseModel):
    ticker: str
    spec: dict
    name: str | None = Field(None, max_length=80)
    start: dt.date = dt.date(2019, 1, 1)
    end: dt.date = Field(default_factory=dt.date.today)
    commission_bps: float = Field(5.0, ge=0, le=100)
    slippage_bps: float = Field(5.0, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self):
        if self.end <= self.start:
            raise ValueError("종료일은 시작일 이후여야 합니다")
        return self


@router.post("/builder/run")
def api_builder_run(req: BuilderRun, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = sym(req.ticker)
    spec = _spec(req.spec)

    def run():
        years = _years_for(req.start)
        ps = _load_bt(s, years)
        ind = bt.prepare(ps.df)
        regime = _regime_series(s, years) if builder.needs_regime(spec) else None
        en, ex = builder.signals(ind, spec, regime)
        a, b = pd.Timestamp(req.start), pd.Timestamp(req.end)
        res = bt.run_period(ind, en, ex, a, b, commission_bps=req.commission_bps, slippage_bps=req.slippage_bps,
                            **builder.risk_kwargs(spec))
        if "error" in res:
            raise _bad("period", res["error"])
        bench = {"단순 보유": bt.buy_and_hold(ind, a, b)}
        for name, bdf in _bench_frames(s, years).items():
            bench[name] = bt.buy_and_hold(bdf, a, b)
        bench = {k: v for k, v in bench.items() if v}
        step = max(1, len(res["equity"]) // 600)
        trades = [{**t, "reason_label": builder.REASON_KO.get(t.get("reason"), t.get("reason"))} for t in res["trades"]]
        reasons: dict[str, int] = {}
        for t in trades:
            if not t.get("open"):
                reasons[t["reason_label"]] = reasons.get(t["reason_label"], 0) + 1
        name = (req.name or "").strip() or "내 전략"
        out = {"symbol": s,
               "strategy": {"id": "custom", "name": name, "description": builder.describe(spec), "spec": spec,
                            "risk": builder.risk_labels(spec)},
               "metrics": res["metrics"], "trades": trades[-200:], "exit_reasons": reasons,
               "equity": _eq(res["equity"], step),
               "benchmarks": {k: {"metrics": v["metrics"], "equity": _eq(v["equity"], step)} for k, v in bench.items()},
               "state": builder.current_state(ind, spec, regime),
               "regimes": regime_breakdown(s, years, res["equity"], bench.get("단순 보유"), res["trades"]),
               "config": {"start": str(req.start), "end": str(req.end), "commission_bps": req.commission_bps,
                          "slippage_bps": req.slippage_bps},
               "data": ps.meta(), "disclaimer": "과거 데이터에 대한 규칙의 성과는 미래 결과를 예측하지 않습니다."}
        db.add(Backtest(user_id=user.id, symbol=s, strategy=f"custom:{name}"[:40], params=spec,
                        start_date=req.start, end_date=req.end, metrics=clean(res["metrics"])))
        db.commit()
        return clean(out)
    return guard(run, f"builder {s}")


# --------------------------------------------------------------- trade plan
@router.get("/stock/{ticker}/plan")
def api_trade_plan(ticker: str, account_krw: float = Query(trade_plan.DEFAULT_ACCOUNT_KRW, ge=100_000, le=1e12),
                   risk_pct: float = Query(trade_plan.DEFAULT_RISK_PCT, ge=0.1, le=10),
                   max_weight_pct: float = Query(trade_plan.DEFAULT_MAX_WEIGHT_PCT, ge=1, le=100)):
    s = sym(ticker)
    return guard(lambda: trade_plan.plan(s, account_krw, risk_pct, max_weight_pct), f"trade plan {s}")


# ----------------------------------------------------------------- rotation
RotationUniverse = Literal["SP500", "KOSPI"]


@router.get("/rotation")
def api_rotation(universe_: RotationUniverse = Query("SP500", alias="universe"), top_n: int = Query(10, ge=3, le=30),
                 commission_bps: float | None = Query(None, ge=0, le=100), slippage_bps: float = Query(5.0, ge=0, le=100),
                 sell_tax_bps: float | None = Query(None, ge=0, le=100), gains_tax: bool | None = None,
                 capital: float = Query(10_000_000, ge=1_000_000, le=1e11)):
    return guard(lambda: rotation.result(universe_, top_n, commission_bps=commission_bps, slippage_bps=slippage_bps,
                                         sell_tax_bps=sell_tax_bps, gains_tax=gains_tax, capital=capital), "rotation")


@router.get("/rotation/status")
def api_rotation_status(universe_: RotationUniverse = Query("SP500", alias="universe")):
    return rotation.status(universe_)


@router.post("/rotation/prepare")
def api_rotation_prepare(universe_: RotationUniverse = Query("SP500", alias="universe")):
    return rotation.start(universe_)


# ------------------------------------------------------------------ journal
class JournalIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    strategy: str = Field("", max_length=80)
    signal: str | None = Field(None, max_length=300)
    followed: bool = True
    entry_date: dt.date
    entry_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    stop_price: float | None = Field(None, gt=0)
    target_price: float | None = Field(None, gt=0)
    exit_date: dt.date | None = None
    exit_price: float | None = Field(None, gt=0)
    fees: float = Field(0, ge=0)
    note: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _check(self):
        if self.exit_price is not None and self.exit_date is None:
            raise ValueError("매도가를 적었다면 매도일도 적어 주세요")
        if self.exit_date is not None and self.exit_price is None:
            raise ValueError("매도일을 적었다면 매도가도 적어 주세요")
        if self.exit_date is not None and self.exit_date < self.entry_date:
            raise ValueError("매도일은 매수일 이후여야 합니다")
        if self.entry_date > dt.date.today() or (self.exit_date and self.exit_date > dt.date.today()):
            raise ValueError("미래 날짜는 기록할 수 없습니다")
        return self


def _apply(row: TradeJournal, req: JournalIn, symbol: str) -> None:
    meta = universe.constituents().get("meta", {}).get(symbol) or {}
    row.symbol, row.name = symbol, meta.get("name_ko") or meta.get("name")
    row.currency = "KRW" if market_of(symbol) == "KR" else "USD"
    for k in ("strategy", "signal", "followed", "entry_date", "entry_price", "quantity", "stop_price", "target_price",
              "exit_date", "exit_price", "fees", "note"):
        setattr(row, k, getattr(req, k))
    row.strategy = (row.strategy or "").strip()
    row.updated_at = utcnow()


def _own_trade(db: Session, user: User, tid: int) -> TradeJournal:
    row = db.get(TradeJournal, tid)
    if row is None or row.user_id != user.id:
        raise _bad("not_found", "기록을 찾을 수 없습니다", 404)
    return row


@router.get("/journal")
def api_journal(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return journal.overview(db, user.id)


@router.post("/journal")
def api_journal_add(req: JournalIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = sym(req.symbol)
    row = TradeJournal(user_id=user.id)
    _apply(row, req, s)
    db.add(row)
    db.commit()
    return {"ok": True, "id": row.id}


@router.put("/journal/{tid}")
def api_journal_update(tid: int, req: JournalIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _own_trade(db, user, tid)
    _apply(row, req, sym(req.symbol))
    db.commit()
    return {"ok": True, "id": row.id}


@router.delete("/journal/{tid}")
def api_journal_delete(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_trade(db, user, tid))
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------- overview
def _symbols(raw: str | None, db: Session, user: User) -> list[str]:
    """Explicit list (the public demo passes its own tickers) or the user's watchlists."""
    if raw is None:
        return _watchlist_symbols(db, user)
    out = []
    for t in raw.split(",")[:200]:
        t = t.strip().upper()
        if t and len(t) <= 24 and t not in out:
            out.append(t)
    return out


@router.get("/overview/summary")
def api_overview_summary(symbols: str | None = Query(None, max_length=4000), user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    syms = _symbols(symbols, db, user)
    return guard(lambda: overview.summary(syms), "overview summary")


@router.get("/overview/matrix")
def api_overview_matrix(symbols: str | None = Query(None, max_length=4000), user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    syms = _symbols(symbols, db, user)
    return guard(lambda: overview.matrix(syms), "signal matrix")


@router.get("/market/heatmap")
def api_heatmap(universe_: Literal["SP500", "KOSPI"] = Query("SP500", alias="universe")):
    return guard(lambda: overview.heatmap(universe_), "heatmap")
