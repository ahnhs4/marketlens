"""Paper trading (stage 1): simulated account, rule strategies, backtests. Never sends a real order."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis import rules
from ..db import get_db
from ..models import PaperPosition, PaperStrategy, User
from ..security import current_user
from ..services import paper
from ..services.stock import resolve
from .common import guard

router = APIRouter(prefix="/api/paper")
Interval = Literal["5m", "15m", "1h", "1d"]


def _syms(v: list[str], limit: int = 30) -> list[str]:
    out = []
    for s in v:
        s = s.strip().upper()
        if not s:
            continue
        try:
            s = resolve(s)
        except Exception:
            raise ValueError(f"'{s}'을(를) 찾을 수 없습니다") from None
        if s.endswith((".KS", ".KQ")):
            raise ValueError("1단계 모의매매는 미국 주식만 지원합니다 (LS증권 해외주식 계획 기준)")
        if s not in out:
            out.append(s)
    if not 1 <= len(out) <= limit:
        raise ValueError(f"종목은 1~{limit}개")
    return out


def _check(kind: str, interval: str, symbols: list[str]) -> str:
    """Validate kind / interval / symbol count together; returns the interval to use."""
    k = rules.KINDS.get(kind)
    if k is None:
        raise HTTPException(422, detail={"code": "kind", "message": f"알 수 없는 전략 종류: {kind}"})
    iv = interval if interval in k.intervals else k.intervals[0]
    cap = 30 if k.timeframe == "daily" else 10
    if len(symbols) > cap:
        raise HTTPException(422, detail={"code": "symbols", "message": f"이 전략은 종목을 최대 {cap}개까지 넣을 수 있습니다"})
    return iv


def _params(kind: str, p: dict | None) -> dict:
    d = dict(rules.KINDS[kind].defaults)
    for k, v in (p or {}).items():
        if k in d:
            d[k] = float(v) if isinstance(d[k], float) else int(v)
    return d


class StrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(max_length=40)
    interval: Interval = "15m"
    params: dict | None = None
    symbols: list[str]
    max_position_usd: float = Field(1000, gt=0, le=1_000_000)
    flatten_at_close: bool = True

    @field_validator("symbols")
    @classmethod
    def _check_symbols(cls, v):
        return _syms(v)


class StrategyPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    interval: Interval | None = None
    params: dict | None = None
    symbols: list[str] | None = None
    max_position_usd: float | None = Field(None, gt=0, le=1_000_000)
    flatten_at_close: bool | None = None
    enabled: bool | None = None

    @field_validator("symbols")
    @classmethod
    def _v(cls, v):
        return None if v is None else _syms(v)


class AccountPatch(BaseModel):
    fee_bps: float | None = Field(None, ge=0, le=200)
    slippage_bps: float | None = Field(None, ge=0, le=200)
    daily_loss_limit_pct: float | None = Field(None, gt=0, le=50)
    paused: bool | None = None


class OrderIn(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    notional_usd: float | None = Field(None, gt=0, le=1_000_000)   # buy
    qty: float | None = Field(None, gt=0)                            # sell (None = all)


class ResetIn(BaseModel):
    start_cash: float = Field(10_000, ge=100, le=10_000_000)


class BacktestIn(BaseModel):
    kind: str = Field(max_length=40)
    interval: Interval = "15m"
    params: dict | None = None
    symbols: list[str]
    max_position_usd: float = Field(1000, gt=0, le=1_000_000)
    flatten_at_close: bool = True

    @field_validator("symbols")
    @classmethod
    def _check_symbols(cls, v):
        return _syms(v)


def _own(db: Session, a, sid: int) -> PaperStrategy:
    s = db.get(PaperStrategy, sid)
    if not s or s.account_id != a.id:
        raise HTTPException(404, detail={"code": "not_found", "message": "전략을 찾을 수 없습니다"})
    return s


@router.get("")
def get_paper(user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = paper.account(db, user)
    return guard(lambda: paper.summary(db, a), "paper")


@router.patch("/account")
def patch_account(body: AccountPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = paper.account(db, user)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    if body.paused is not None:
        paper._event(db, a, "risk", "긴급 정지 - 자동 주문 모두 멈춤" if body.paused else "긴급 정지 해제 - 자동 주문 재개")
    db.commit()
    return paper.summary(db, a)


@router.post("/reset")
def reset(body: ResetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = paper.account(db, user)
    paper.reset(db, a, body.start_cash)
    db.commit()
    return paper.summary(db, a)


@router.post("/orders")
def order(body: OrderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A manual paper order at the latest quote (simulated; nothing is sent to a broker)."""
    a = paper.account(db, user)
    try:
        sym = _syms([body.symbol])[0]
    except ValueError as e:
        raise HTTPException(422, detail={"code": "symbol", "message": str(e)})
    price = paper._price(sym)
    if price is None:
        raise HTTPException(503, detail={"code": "no_quote", "message": f"{sym} 시세를 불러올 수 없습니다 (Data unavailable)"})
    when = "" if paper.clock.status("US")["state"] == "open" else " (장외 시간 - 마지막 가격 기준)"
    if body.side == "buy":
        if not body.notional_usd:
            raise HTTPException(422, detail={"code": "amount", "message": "매수 금액($)을 입력하세요"})
        if a.cash < 1:
            raise HTTPException(422, detail={"code": "cash", "message": "현금이 부족합니다"})
        t = paper.buy(db, a, sym, body.notional_usd, price, "수동 주문" + when)
        paper._event(db, a, "order", f"{sym} 수동 매수 {t.qty:.4f}주 @ {t.price:,.2f}{when}", None, sym)
    else:
        pos = db.execute(select(PaperPosition).where(PaperPosition.account_id == a.id, PaperPosition.symbol == sym,
                                                     PaperPosition.strategy_id.is_(None))).scalar_one_or_none()
        if not pos:
            raise HTTPException(422, detail={"code": "position", "message": f"수동 보유 중인 {sym}이(가) 없습니다 (전략 보유분은 포지션 표에서 청산)"})
        t = paper.sell(db, a, pos, price, "수동 주문" + when, body.qty)
        paper._event(db, a, "order", f"{sym} 수동 매도 {t.qty:.4f}주 @ {t.price:,.2f}{when}", None, sym)
    db.flush()
    paper.snapshot(db, a)
    db.commit()
    return paper.summary(db, a)


@router.post("/positions/{pos_id}/close")
def close_position(pos_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = paper.account(db, user)
    pos = db.get(PaperPosition, pos_id)
    if not pos or pos.account_id != a.id:
        raise HTTPException(404, detail={"code": "not_found", "message": "포지션을 찾을 수 없습니다"})
    price = paper._price(pos.symbol)
    if price is None:
        raise HTTPException(503, detail={"code": "no_quote", "message": f"{pos.symbol} 시세를 불러올 수 없습니다 (Data unavailable)"})
    sym = pos.symbol
    t = paper.sell(db, a, pos, price, "수동 청산")
    paper._event(db, a, "order", f"{sym} 수동 청산 {t.qty:.4f}주 @ {t.price:,.2f}", t.strategy_id, sym)
    db.flush()
    paper.snapshot(db, a)
    db.commit()
    return paper.summary(db, a)


@router.post("/strategies")
def create_strategy(body: StrategyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = paper.account(db, user)
    iv = _check(body.kind, body.interval, body.symbols)
    s = PaperStrategy(account_id=a.id, name=body.name, kind=body.kind, interval=iv, params=_params(body.kind, body.params),
                      symbols=body.symbols, max_position_usd=body.max_position_usd, flatten_at_close=body.flatten_at_close,
                      enabled=False, state={})
    db.add(s)
    db.commit()
    return paper.summary(db, a)


@router.patch("/strategies/{sid}")
def patch_strategy(sid: int, body: StrategyPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = paper.account(db, user)
    s = _own(db, a, sid)
    data = body.model_dump(exclude_none=True)
    data["interval"] = _check(s.kind, data.get("interval", s.interval), data.get("symbols", s.symbols))
    if "params" in data:
        data["params"] = _params(s.kind, data["params"])
    for k, v in data.items():
        setattr(s, k, v)
    if "enabled" in data:
        paper._event(db, a, "risk", f"전략 '{s.name}' {'켜짐' if s.enabled else '꺼짐 (보유분의 손절·마감 청산은 계속)'}", s.id)
    db.commit()
    return paper.summary(db, a)


@router.delete("/strategies/{sid}")
def delete_strategy(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = paper.account(db, user)
    s = _own(db, a, sid)
    for pos in [p for p in paper.positions(db, a) if p.strategy_id == s.id]:
        price = paper._price(pos.symbol)
        if price is None:
            raise HTTPException(503, detail={"code": "no_quote", "message": f"{pos.symbol} 시세가 없어 청산할 수 없습니다 - 잠시 후 다시"})
        paper.sell(db, a, pos, price, f"전략 '{s.name}' 삭제로 청산")
    paper._event(db, a, "risk", f"전략 '{s.name}' 삭제")
    db.delete(s)
    db.commit()
    return paper.summary(db, a)


@router.post("/run")
def run_now(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Run one engine cycle now (the scheduler does this every minute)."""
    a = paper.account(db, user)
    res = guard(lambda: paper.run_account(db, a), "paper run")
    db.commit()
    return {"result": res, **paper.summary(db, a)}


@router.post("/backtest")
def backtest(body: BacktestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = paper.account(db, user)
    iv = _check(body.kind, body.interval, body.symbols)
    return guard(lambda: paper.backtest(body.kind, _params(body.kind, body.params), body.symbols, iv,
                                        notional=body.max_position_usd, fee_bps=a.fee_bps, slippage_bps=a.slippage_bps,
                                        flatten=body.flatten_at_close), "paper backtest")


@router.get("/study")
def study(user: User = Depends(current_user)):
    """Results of the strategy study (which popular rules hold up after costs)."""
    from ..services import strategy_study
    return strategy_study.load()


@router.post("/study/run")
def study_run(user: User = Depends(current_user)):
    from ..services import strategy_study
    strategy_study.start()
    return strategy_study.load()
