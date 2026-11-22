"""Auth, settings, watchlists, portfolios and alerts (all scoped to the current user)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cache import cached
from ..config import get_settings
from ..db import get_db
from ..models import Alert, AlertEvent, Portfolio, PortfolioPosition, User, UserSettings, Watchlist, WatchlistItem
from ..security import (LOCAL_EMAIL, clear_session, current_user, hash_password, issue_session, verify_password)
from ..services import alerts as alert_svc
from ..services import portfolio as pf
from ..services import prices, settings_store, stock, universe
from ..services.stock import resolve
from ..services.store import clean
from .common import guard, sym

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- auth
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(None, max_length=100)


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": None if user.email == LOCAL_EMAIL else user.email,
            "display_name": user.display_name, "local_mode": user.email == LOCAL_EMAIL,
            "auth_required": get_settings().auth_required}


@router.post("/auth/register")
def register(c: Credentials, resp: Response, db: Session = Depends(get_db)):
    u = User(email=c.email.lower(), password_hash=hash_password(c.password), display_name=c.display_name)
    db.add(u)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail={"code": "exists", "message": "이미 가입된 이메일입니다"})
    db.add(UserSettings(user_id=u.id))
    db.commit()
    issue_session(resp, u)
    return {"id": u.id, "email": u.email}


@router.post("/auth/login")
def login(c: Credentials, resp: Response, db: Session = Depends(get_db)):
    u = db.execute(select(User).where(User.email == c.email.lower())).scalar_one_or_none()
    if not u or not verify_password(c.password, u.password_hash):
        raise HTTPException(401, detail={"code": "invalid_credentials", "message": "이메일 또는 비밀번호가 올바르지 않습니다"})
    issue_session(resp, u)
    return {"id": u.id, "email": u.email}


@router.post("/auth/logout")
def logout(resp: Response, everywhere: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if everywhere and user.email != LOCAL_EMAIL:
        user.session_version += 1
        db.commit()
    clear_session(resp)
    return {"ok": True}


# ----------------------------------------------------------------- settings
@router.get("/settings")
def get_settings_api(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return settings_store.get(db, user)


class SettingsPatch(BaseModel):
    profile: dict | None = None
    weights: dict | None = None
    preferences: dict | None = None


@router.put("/settings")
def put_settings(p: SettingsPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    before = settings_store.get(db, user)["preferences"].get("timing_style")
    out = settings_store.update(db, user, p.model_dump(exclude_none=True))
    if out["preferences"].get("timing_style") != before:
        from ..services import scoring_style
        scoring_style.changed()
    return out


# --------------------------------------------------------------- watchlists
class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ItemIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    note: str | None = Field(None, max_length=500)


def _own_wl(db: Session, user: User, wid: int) -> Watchlist:
    w = db.get(Watchlist, wid)
    if not w or w.user_id != user.id:
        raise HTTPException(404, detail={"code": "not_found", "message": "관심종목 목록을 찾을 수 없습니다"})
    return w


@router.get("/watchlists")
def list_watchlists(user: User = Depends(current_user), db: Session = Depends(get_db)):
    wls = db.execute(select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.sort_order, Watchlist.id)).scalars().all()
    if not wls:  # starter lists so the page is useful on first visit
        for i, (name, syms) in enumerate((("AI·반도체", ["NVDA", "AMD", "AVGO", "TSM", "000660.KS"]),
                                          ("한국", ["005930.KS", "000660.KS", "035420.KS", "005380.KS"]))):
            w = Watchlist(user_id=user.id, name=name, sort_order=i)
            db.add(w)
            db.flush()
            for s in syms:
                db.add(WatchlistItem(watchlist_id=w.id, symbol=s))
        db.commit()
        wls = db.execute(select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.sort_order)).scalars().all()
    return [{"id": w.id, "name": w.name, "symbols": [i.symbol for i in w.items]} for w in wls]


@router.post("/watchlists")
def create_watchlist(body: WatchlistIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    w = Watchlist(user_id=user.id, name=body.name.strip())
    db.add(w)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail={"code": "exists", "message": "같은 이름의 목록이 이미 있습니다"})
    return {"id": w.id, "name": w.name, "symbols": []}


@router.patch("/watchlists/{wid}")
def rename_watchlist(wid: int, body: WatchlistIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    w = _own_wl(db, user, wid)
    w.name = body.name.strip()
    db.commit()
    return {"id": w.id, "name": w.name}


@router.delete("/watchlists/{wid}")
def delete_watchlist(wid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_wl(db, user, wid))
    db.commit()
    return {"ok": True}


@router.post("/watchlists/{wid}/items")
def add_item(wid: int, body: ItemIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    w = _own_wl(db, user, wid)
    s = sym(body.symbol)
    try:  # reject symbols that have no data rather than storing a dead ticker
        prices.get_quote(s)
    except Exception:
        raise HTTPException(404, detail={"code": "ticker_not_found", "message": f"'{body.symbol}'의 시세 데이터를 찾을 수 없습니다"})
    db.add(WatchlistItem(watchlist_id=w.id, symbol=s, note=body.note))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return {"id": w.id, "symbols": [i.symbol for i in _own_wl(db, user, wid).items]}


@router.delete("/watchlists/{wid}/items/{symbol}")
def remove_item(wid: int, symbol: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    w = _own_wl(db, user, wid)
    for i in list(w.items):
        if i.symbol == symbol:
            db.delete(i)
    db.commit()
    return {"ok": True}


@router.get("/watchlists/{wid}/quotes")
def watchlist_quotes(wid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import stock

    w = _own_wl(db, user, wid)
    symbols = [i.symbol for i in w.items]
    quotes = guard(lambda: prices.get_quotes(symbols), "watchlist quotes")
    scanned = {r["symbol"]: r for r in universe.results()}
    rows = []
    for s in symbols:
        q = quotes.get(s)
        r = scanned.get(s)
        row = {"symbol": s, "name": (q.name if q else None), "price": q.price if q else None,
               "change_pct": q.change_pct if q else None, "currency": q.currency if q else None,
               "market_cap": None, "volume": q.volume if q else None, "delay_minutes": q.delay_minutes if q else None,
               "quote_time": q.quote_time.isoformat() if q and q.quote_time else None}
        try:
            if r is None:
                t = stock.technicals(s)
                ind = t["indicators"]
                r = {"technical": t["signal"]["score"], "signal": t["signal"]["signal"], "rsi": ind.get("rsi"),
                     "trend": t["ma"]["structure"], "mom20": ind.get("mom20"), "macro": None,
                     "vol_ratio": ind.get("vol_ratio")}
            row.update(technical=r.get("technical"), signal=r.get("signal"), rsi=r.get("rsi"), trend=r.get("trend"),
                       macro=r.get("macro"), mom20=r.get("mom20"), vol_ratio=r.get("vol_ratio"),
                       market_cap=r.get("f_market_cap_usd"))
        except Exception:
            row["status"] = "Analysis unavailable"
        rows.append(row)
    return clean({"id": w.id, "name": w.name, "rows": rows})


# ---------------------------------------------------------------- portfolio
class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    base_currency: Literal["USD", "KRW"] = "USD"


class PositionIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    quantity: float = Field(gt=0, le=1e12)
    avg_cost: float = Field(gt=0, le=1e12)
    currency: Literal["USD", "KRW"] | None = None
    account: str | None = Field(None, max_length=100)
    avg_cost_estimated: bool = False
    avg_cost_krw: float | None = Field(None, gt=0, le=1e13)


class PositionPatch(BaseModel):
    quantity: float | None = Field(None, gt=0, le=1e12)
    avg_cost: float | None = Field(None, gt=0, le=1e12)
    avg_cost_krw: float | None = Field(None, gt=0, le=1e13)
    account: str | None = Field(None, max_length=100)


class CostsIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


def _pos_dicts(p: Portfolio) -> list[dict]:
    return [{"id": x.id, "symbol": x.symbol, "quantity": x.quantity, "avg_cost": x.avg_cost, "currency": x.currency,
             "account": x.account, "avg_cost_estimated": bool(x.avg_cost_estimated), "avg_cost_krw": x.avg_cost_krw}
            for x in p.positions]


def _own_pf(db: Session, user: User, pid: int) -> Portfolio:
    p = db.get(Portfolio, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(404, detail={"code": "not_found", "message": "포트폴리오를 찾을 수 없습니다"})
    return p


@router.get("/portfolios")
def list_portfolios(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ps = db.execute(select(Portfolio).where(Portfolio.user_id == user.id)).scalars().all()
    if not ps:
        p = Portfolio(user_id=user.id, name="내 포트폴리오", base_currency="USD")
        db.add(p)
        db.commit()
        ps = [p]
    return [{"id": p.id, "name": p.name, "base_currency": p.base_currency, "positions": len(p.positions)} for p in ps]


@router.post("/portfolios")
def create_portfolio(body: PortfolioIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = Portfolio(user_id=user.id, name=body.name, base_currency=body.base_currency)
    db.add(p)
    db.commit()
    return {"id": p.id, "name": p.name, "base_currency": p.base_currency}


@router.get("/portfolios/{pid}")
def get_portfolio(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _own_pf(db, user, pid)
    st = settings_store.get(db, user)
    positions = _pos_dicts(p)
    res = guard(lambda: pf.analyze(positions, p.base_currency, st["profile"].get("max_drawdown")), "portfolio")
    return {"id": p.id, "name": p.name, **res}


def _analysis_fetch(db: Session, user: User, st: dict):
    """symbol -> (technicals, scores) with the user's settings, on the same cache keys as the stock page
    (so opening a holding afterwards is instant and consistent)."""
    from .stock import _rsi_periods, _stoch_sets, _wkey

    w, blend = st["weights"]["technical_components"], st["weights"]["overall_blend"]
    rs, rp, rl = _rsi_periods(db, user)
    ss = _stoch_sets(db, user)

    def fetch(s: str) -> tuple[dict, dict]:
        tech = cached(f"tech:{s}:{rs}-{rp}-{rl}:{_wkey(ss)}:6M:{_wkey(w)}", 120,
                      lambda: stock.technicals(s, rsi_period=rp, weights=w, rsi_short=rs, rsi_long=rl, stoch_sets=ss))
        sc = cached(f"scores:{s}:{_wkey(w)}:{_wkey(blend)}", 180, lambda: stock.scores(s, weights=w, blend=blend))
        return tech, sc
    return fetch


@router.get("/portfolios/{pid}/actions")
def portfolio_actions(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Per-holding opinion (강력 매수 / 추가 매수 / 보유 / 비중 축소 / 매도 추천) with the full reasoning."""
    p = _own_pf(db, user, pid)
    st = settings_store.get(db, user)
    positions = _pos_dicts(p)
    fetch = _analysis_fetch(db, user, st)
    return guard(lambda: pf.actions(positions, p.base_currency, st["profile"], fetch), "portfolio actions")


class PlanIn(BaseModel):
    """Unsaved edits to preferences.plan, to preview the result before saving."""
    dividend_target_krw: float | None = Field(None, ge=0, le=1e11)
    dividend_basis: Literal["net", "gross"] | None = None
    realized_overseas_krw: float | None = Field(None, ge=-1e11, le=1e11)
    contribution_krw: float | None = Field(None, ge=0, le=1e11)
    targets: dict[str, float] | None = None
    bucket_overrides: dict[str, str | None] | None = None

    @field_validator("targets")
    @classmethod
    def _targets(cls, v):
        if v is not None and any(x < 0 or x > 100 for x in v.values()):
            raise ValueError("목표 비중은 0~100% 사이여야 합니다")
        return v


def _plan(pid: int, user: User, db: Session, draft: dict | None = None) -> dict:
    from ..services import plan

    p = _own_pf(db, user, pid)
    st = settings_store.get(db, user)
    prefs = settings_store._merge(st["preferences"]["plan"], draft or {})
    positions = _pos_dicts(p)
    fetch = _analysis_fetch(db, user, st)
    return guard(lambda: plan.build(positions, p.base_currency, st["profile"], prefs, fetch), "portfolio plan")


@router.get("/portfolios/{pid}/plan")
def portfolio_plan(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Dividend goal, yearly tax estimate and the trades that reach the target allocation (settings: preferences.plan)."""
    return _plan(pid, user, db)


@router.post("/portfolios/{pid}/plan/preview")
def portfolio_plan_preview(pid: int, body: PlanIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Same as GET with unsaved edits applied on top of the saved plan settings."""
    return _plan(pid, user, db, body.model_dump(exclude_unset=True))


@router.post("/portfolios/{pid}/positions")
def add_position(pid: int, body: PositionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _own_pf(db, user, pid)
    s = sym(body.symbol)
    try:
        q = prices.get_quote(s)
    except Exception:
        raise HTTPException(404, detail={"code": "ticker_not_found", "message": f"'{body.symbol}'의 시세 데이터를 찾을 수 없습니다"})
    pos = PortfolioPosition(portfolio_id=p.id, symbol=s, quantity=body.quantity, avg_cost=body.avg_cost,
                            currency=body.currency or q.currency or "USD", account=body.account,
                            avg_cost_estimated=body.avg_cost_estimated, avg_cost_krw=body.avg_cost_krw)
    db.add(pos)
    db.commit()
    return {"id": pos.id, "symbol": s}


@router.put("/portfolios/{pid}/positions/{pos_id}")
def update_position(pid: int, pos_id: int, body: PositionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _own_pf(db, user, pid)
    pos = db.get(PortfolioPosition, pos_id)
    if not pos or pos.portfolio_id != p.id:
        raise HTTPException(404, detail={"code": "not_found", "message": "보유 종목을 찾을 수 없습니다"})
    pos.quantity, pos.avg_cost, pos.account = body.quantity, body.avg_cost, body.account
    pos.avg_cost_estimated = body.avg_cost_estimated
    pos.avg_cost_krw = body.avg_cost_krw
    if body.currency:
        pos.currency = body.currency
    db.commit()
    return {"ok": True}


@router.patch("/portfolios/{pid}/positions/{pos_id}")
def patch_position(pid: int, pos_id: int, body: PositionPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Edit one field in place; entering an average cost marks it as real (no longer a stand-in)."""
    p = _own_pf(db, user, pid)
    pos = db.get(PortfolioPosition, pos_id)
    if not pos or pos.portfolio_id != p.id:
        raise HTTPException(404, detail={"code": "not_found", "message": "보유 종목을 찾을 수 없습니다"})
    if body.quantity is not None:
        pos.quantity = body.quantity
    if body.avg_cost is not None:
        pos.avg_cost, pos.avg_cost_estimated = body.avg_cost, False
    if body.avg_cost_krw is not None:
        pos.avg_cost_krw = body.avg_cost_krw
    if body.account is not None:
        pos.account = body.account or None
    db.commit()
    return {"ok": True, "id": pos.id, "avg_cost": pos.avg_cost, "avg_cost_krw": pos.avg_cost_krw,
            "avg_cost_estimated": pos.avg_cost_estimated}


@router.post("/portfolios/{pid}/avg-costs")
def bulk_avg_costs(pid: int, body: CostsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Paste lines like 'NVDL 29.85' / '엔비디아 180.2' / '005930 61,200' to set average costs in one go."""
    p = _own_pf(db, user, pid)
    by_sym = {x.symbol.upper(): x for x in p.positions}
    updated, unmatched = [], []
    for ident, price, line, krw in pf.parse_cost_lines(body.text, with_krw=True):
        if price is None:
            unmatched.append({"line": line, "reason": "가격을 읽을 수 없음"})
            continue
        key = ident.upper()
        pos = by_sym.get(key) or by_sym.get(f"{key}.KS") or by_sym.get(f"{key}.KQ")
        if pos is None:
            try:
                r = resolve(ident)
            except Exception:
                r = None
            pos = by_sym.get((r or "").upper())
        if pos is None:
            unmatched.append({"line": line, "reason": "보유 종목에서 찾을 수 없음"})
            continue
        pos.avg_cost, pos.avg_cost_estimated = price, False
        if krw and pos.currency != "KRW":
            pos.avg_cost_krw = krw
        updated.append({"symbol": pos.symbol, "avg_cost": price, "avg_cost_krw": pos.avg_cost_krw, "line": line})
    db.commit()
    return {"updated": updated, "unmatched": unmatched,
            "remaining_estimated": sum(1 for x in p.positions if x.avg_cost_estimated)}


@router.delete("/portfolios/{pid}/positions/{pos_id}")
def delete_position(pid: int, pos_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _own_pf(db, user, pid)
    pos = db.get(PortfolioPosition, pos_id)
    if pos and pos.portfolio_id == p.id:
        db.delete(pos)
        db.commit()
    return {"ok": True}


@router.get("/portfolio/scenarios")
def scenarios():
    return {"scenarios": pf.SCENARIOS, "factors": {"market": "S&P 500 변동률, %", "rate_bp": "미 10년물 금리 변화, bp",
                                                    "usd": "달러인덱스 변동률, %", "oil": "WTI 유가 변동률, %"}}


class StressIn(BaseModel):
    scenario: str | None = None
    market: float = Field(0, ge=-50, le=50)
    rate_bp: float = Field(0, ge=-300, le=300)
    usd: float = Field(0, ge=-20, le=20)
    oil: float = Field(0, ge=-80, le=200)


@router.post("/portfolios/{pid}/stress")
def stress(pid: int, body: StressIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _own_pf(db, user, pid)
    shocks = pf.SCENARIOS.get(body.scenario) if body.scenario else None
    shocks = dict(shocks) if shocks else {"label": "사용자 시나리오", "market": body.market, "rate_bp": body.rate_bp,
                                          "usd": body.usd, "oil": body.oil}
    positions = _pos_dicts(p)

    def run():
        a = pf.analyze(positions, p.base_currency)
        valued = [r for r in a.get("positions", []) if r.get("weight")]
        return pf.stress_test(valued, shocks)
    return guard(run, "stress test")


# ------------------------------------------------------------------- alerts
class AlertIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    condition_type: Literal[tuple(alert_svc.CONDITIONS)]  # type: ignore[valid-type]
    params: dict = Field(default_factory=dict)
    channels: list[Literal["web", "email", "discord", "telegram"]] = Field(default_factory=lambda: ["web"])
    cooldown_minutes: int = Field(24 * 60, ge=5, le=60 * 24 * 30)

    @field_validator("params")
    @classmethod
    def _params(cls, v: dict) -> dict:
        for k, x in v.items():
            if not isinstance(x, (int, float, str)) or (isinstance(x, str) and len(x) > 20):
                raise ValueError(f"invalid parameter {k}")
        return v


@router.get("/alerts/conditions")
def alert_conditions():
    s = get_settings()
    return {"conditions": alert_svc.CONDITIONS,
            "channels": {"web": True, "email": bool(s.smtp_host), "discord": bool(s.discord_webhook_url),
                         "telegram": bool(s.telegram_bot_token and s.telegram_chat_id)}}


@router.get("/alerts")
def list_alerts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc())).scalars().all()
    return [{"id": a.id, "symbol": a.symbol, "condition_type": a.condition_type, "params": a.params, "channels": a.channels,
             "is_active": a.is_active, "cooldown_minutes": a.cooldown_minutes,
             "last_triggered_at": a.last_triggered_at.isoformat() if a.last_triggered_at else None,
             "last_checked_at": a.last_checked_at.isoformat() if a.last_checked_at else None} for a in rows]


@router.post("/alerts")
def create_alert(body: AlertIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = sym(body.symbol)
    if body.condition_type in ("price_above", "price_below") and "value" not in body.params:
        raise HTTPException(422, detail={"code": "params", "message": "가격 기준값을 입력하세요"})
    a = Alert(user_id=user.id, symbol=s, condition_type=body.condition_type,
              params={**alert_svc.CONDITIONS[body.condition_type]["params"], **body.params},
              channels=list(body.channels), cooldown_minutes=body.cooldown_minutes)
    db.add(a)
    db.commit()
    return {"id": a.id, "symbol": s}


class AlertPatch(BaseModel):
    is_active: bool | None = None
    params: dict | None = None
    channels: list[Literal["web", "email", "discord", "telegram"]] | None = None


@router.patch("/alerts/{aid}")
def patch_alert(aid: int, body: AlertPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.get(Alert, aid)
    if not a or a.user_id != user.id:
        raise HTTPException(404, detail={"code": "not_found", "message": "알림을 찾을 수 없습니다"})
    if body.is_active is not None:
        a.is_active = body.is_active
    if body.params is not None:
        a.params = {**(a.params or {}), **body.params}
    if body.channels is not None:
        a.channels = list(body.channels)
    db.commit()
    return {"ok": True}


@router.delete("/alerts/{aid}")
def delete_alert(aid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.get(Alert, aid)
    if a and a.user_id == user.id:
        db.delete(a)
        db.commit()
    return {"ok": True}


@router.get("/alerts/events")
def alert_events(unread: bool = False, limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    q = select(AlertEvent, Alert).join(Alert).where(Alert.user_id == user.id)
    if unread:
        q = q.where(AlertEvent.read.is_(False))
    rows = db.execute(q.order_by(AlertEvent.triggered_at.desc()).limit(limit)).all()
    return [{"id": e.id, "alert_id": a.id, "symbol": a.symbol, "condition_type": a.condition_type, "message": e.message,
             "triggered_at": e.triggered_at.isoformat(), "read": e.read, "delivered": e.delivered} for e, a in rows]


@router.post("/alerts/events/read")
def mark_read(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(AlertEvent).join(Alert).where(Alert.user_id == user.id, AlertEvent.read.is_(False))).scalars().all()
    for e in rows:
        e.read = True
    db.commit()
    return {"marked": len(rows)}


@router.post("/alerts/check")
def check_now(_: User = Depends(current_user)):
    return guard(alert_svc.run_checks, "alert check")
