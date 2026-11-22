"""Stock analyzer, comparison and backtesting endpoints."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..analysis import backtest as bt
from ..cache import cached
from ..db import get_db
from ..models import Backtest, User
from ..security import current_user
from ..services import dart_fin, prices, settings_store, stock
from ..services.store import clean, utcnow
from .common import guard, sym

router = APIRouter(prefix="/api")


def _user_weights(db: Session, user: User) -> tuple[dict, dict, int]:
    st = settings_store.get(db, user)
    return st["weights"]["technical_components"], st["weights"]["overall_blend"], int(st["preferences"]["rsi_period"])


def _rsi_periods(db: Session, user: User) -> tuple[int, int, int]:
    """(short, medium, long) RSI periods from the user's preferences."""
    pr = settings_store.get(db, user)["preferences"]
    return int(pr["rsi_short_period"]), int(pr["rsi_period"]), int(pr["rsi_long_period"])


def _stoch_sets(db: Session, user: User) -> dict[str, tuple[int, int, int]]:
    """Slow stochastic (n, m, t) per horizon from preferences, falling back to HTS defaults on bad input."""
    from ..analysis.indicators import STOCH_SETS
    raw = settings_store.get(db, user)["preferences"].get("stoch_sets") or {}
    out = {}
    for hz, default in STOCH_SETS.items():
        try:
            v = tuple(int(x) for x in raw.get(hz, default))
            out[hz] = v if len(v) == 3 and all(1 <= x <= 120 for x in v) else default
        except (TypeError, ValueError):
            out[hz] = default
    return out


def _parse_stoch(s: str | None) -> dict[str, tuple[int, int, int]] | None:
    """'5-3-3,10-6-6,20-12-12' -> {short: (5,3,3), ...}"""
    if not s:
        return None
    try:
        sets = [tuple(int(x) for x in part.split("-")) for part in s.split(",")]
        if len(sets) == 3 and all(len(t) == 3 and all(1 <= x <= 120 for x in t) for t in sets):
            return dict(zip(("short", "medium", "long"), sets))
    except ValueError:
        pass
    raise HTTPException(422, detail={"code": "bad_stoch", "message": "stoch 형식: 5-3-3,10-6-6,20-12-12"})


def _wkey(d: dict) -> str:
    return hashlib.md5(json.dumps(d, sort_keys=True).encode()).hexdigest()[:10]


@router.get("/stock/{ticker}")
def api_stock(ticker: str):
    s = sym(ticker)
    return guard(lambda: stock.overview(s), f"overview {s}")


@router.get("/stock/{ticker}/chart")
def api_chart(ticker: str, range_: Literal["1D", "1W", "1M", "3M", "6M", "1Y", "3Y", "5Y", "MAX"] = Query("1Y", alias="range"),
              interval: Literal["1m", "5m", "15m", "1h"] | None = None, rsi_period: int = Query(14, ge=2, le=50),
              rsi_short: int = Query(7, ge=2, le=50), rsi_long: int = Query(28, ge=2, le=100),
              stoch: str | None = Query(None, max_length=40)):
    s = sym(ticker)
    sets = _parse_stoch(stoch)
    return guard(lambda: stock.chart(s, range_, interval, rsi_period, rsi_short, rsi_long, sets), f"chart {s}")


@router.get("/stock/{ticker}/technical")
def api_technical(ticker: str, vp_period: Literal["1M", "3M", "6M", "1Y", "3Y"] = "6M",
                  rsi_period: int | None = Query(None, ge=2, le=50),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = sym(ticker)
    w, _, _ = _user_weights(db, user)
    rs, rp, rl = _rsi_periods(db, user)
    st = _stoch_sets(db, user)
    rp = rsi_period or rp
    return guard(lambda: cached(f"tech:{s}:{rs}-{rp}-{rl}:{_wkey(st)}:{vp_period}:{_wkey(w)}", 120,
                                lambda: stock.technicals(s, rsi_period=rp, weights=w, vp_period=vp_period,
                                                         rsi_short=rs, rsi_long=rl, stoch_sets=st)), f"technical {s}")


@router.get("/stock/{ticker}/outlook")
def api_outlook(ticker: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """단기 · 중기 · 장기 written analysis combining the technical and macro views."""
    from ..services import fear_greed, outlook

    s = sym(ticker)
    w, blend, _ = _user_weights(db, user)
    rs, rp, rl = _rsi_periods(db, user)
    st = _stoch_sets(db, user)

    def run():
        tech = cached(f"tech:{s}:{rs}-{rp}-{rl}:{_wkey(st)}:6M:{_wkey(w)}", 120,
                      lambda: stock.technicals(s, rsi_period=rp, weights=w, rsi_short=rs, rsi_long=rl, stoch_sets=st))
        sc = cached(f"scores:{s}:{_wkey(w)}:{_wkey(blend)}", 180, lambda: stock.scores(s, weights=w, blend=blend))
        try:
            fg = fear_greed.index_payload()
        except Exception:
            fg = None
        try:
            ets = stock.profile(s).get("earnings_timestamp")
        except Exception:
            ets = None
        return outlook.build(s, tech, sc, stock.current_regime(), fg, ets)
    return guard(lambda: cached(f"outlook:{s}:{_wkey(w)}:{_wkey(blend)}:{_wkey(st)}", 180, run), f"outlook {s}")


@router.get("/stock/{ticker}/scores")
def api_scores(ticker: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = sym(ticker)
    w, blend, _ = _user_weights(db, user)
    return guard(lambda: cached(f"scores:{s}:{_wkey(w)}:{_wkey(blend)}", 180,
                                lambda: stock.scores(s, weights=w, blend=blend)), f"scores {s}")


@router.get("/stock/{ticker}/patterns")
def api_patterns(ticker: str):
    s = sym(ticker)
    return guard(lambda: cached(f"patterns:{s}", 300, lambda: stock.pattern_report(s)), f"patterns {s}")


@router.get("/stock/{ticker}/similar")
def api_similar(ticker: str, years: float = Query(3.0, ge=1, le=10)):
    s = sym(ticker)
    return guard(lambda: cached(f"similar:{s}:{years}", 600, lambda: stock.analogs(s, years)), f"analogs {s}")


@router.get("/stock/{ticker}/analyst")
def api_stock_analyst(ticker: str):
    """Sell-side view: consensus target, the 5 latest reports / rating actions with reasons, and the flow."""
    from ..services import analyst
    s = sym(ticker)
    return guard(lambda: analyst.view(s), f"analyst {s}")


@router.get("/stock/{ticker}/news")
def api_stock_news(ticker: str):
    s = sym(ticker)

    def run():
        try:
            p = stock.profile(s)
        except Exception:
            p = {}
        from ..services import news
        return news.stock_news(s, p.get("name"), p.get("name_ko"), p.get("industry"))
    return guard(run, f"news {s}")


@router.get("/stock/{ticker}/fundamentals")
def api_fundamentals(ticker: str):
    s = sym(ticker)
    return guard(lambda: cached(f"fund:{s}", 3600, lambda: stock.fundamentals_detail(s)), f"fundamentals {s}")


@router.get("/stock/{ticker}/dart")
def api_stock_dart(ticker: str):
    """Korean companies: OpenDART statements, metrics, KR fundamental score, low-PBR / value-up status."""
    s = sym(ticker)
    if not s.endswith((".KS", ".KQ")):
        return {"available": False, "message": "국내 상장사만 DART 재무제표를 제공합니다"}
    return guard(lambda: dart_fin.company(s.split(".")[0]), f"dart {s}")


@router.get("/stock/{ticker}/macro")
def api_stock_macro(ticker: str):
    s = sym(ticker)

    def run():
        p = stock.profile(s)
        return stock.macro_view(s, p.get("sector"), p.get("industry"))
    return guard(lambda: cached(f"smacro:{s}", 600, run), f"macro {s}")


# ---------------------------------------------------------------- compare
@router.get("/compare")
def api_compare(tickers: str = Query(..., description="Comma-separated, up to 6"),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    syms = [sym(t) for t in tickers.split(",") if t.strip()][:6]
    if len(syms) < 2:
        raise HTTPException(400, detail={"code": "need_two", "message": "종목을 2개 이상 입력하세요"})
    w, blend, _ = _user_weights(db, user)

    def one(s: str) -> dict:
        try:
            sc = cached(f"scores:{s}:{_wkey(w)}:{_wkey(blend)}", 180, lambda: stock.scores(s, weights=w, blend=blend))
            p = stock.profile(s)
            df = prices.get_daily(s, years=3).df
            c = df["adj_close"]
            r = c.pct_change().dropna()
            perf = {k: float(c.iloc[-1] / c.iloc[-1 - n] - 1) * 100 if len(c) > n else None
                    for k, n in (("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252), ("3y", 756))}
            yr = c.iloc[-252:]
            vol = float(r.iloc[-252:].std() * np.sqrt(252))
            mdd = float((yr / yr.cummax() - 1).min())
            fs = sc.get("fundamental_detail") or {}
            pillars = (fs.get("pillars") or {})
            mom_score = max(0.0, min(100.0, 50 + (perf["6m"] or 0) * 1.0))
            radar = {"기술적": sc.get("technical"), "매크로": sc.get("macro"), "펀더멘털": sc.get("fundamental"),
                     "모멘텀": mom_score, "밸류에이션": (pillars.get("valuation") or {}).get("score"),
                     "수익성": (pillars.get("quality") or {}).get("score"),
                     "성장성": (pillars.get("growth") or {}).get("score"),
                     "저위험": max(0.0, min(100.0, 100 - vol * 100))}
            norm = (yr / yr.iloc[0] * 100).iloc[::2]
            return {"symbol": s, "name": p.get("name"), "sector": p.get("sector"), "industry": p.get("industry"),
                    "currency": p.get("currency"), "price": float(df["close"].iloc[-1]), "scores": sc,
                    "fundamentals": p.get("fundamentals"), "performance": perf, "volatility_1y": vol,
                    "max_drawdown_1y": mdd, "radar": radar, "macro_empirical": (sc.get("macro_detail") or {}).get("empirical"),
                    "history": [{"date": str(d.date()), "value": round(float(v), 2)} for d, v in norm.items()]}
        except Exception as e:
            return {"symbol": s, "status": "Data unavailable", "error": type(e).__name__}
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(6, len(syms))) as ex:
        items = list(ex.map(one, syms))
    return clean({"items": items, "updated_at": utcnow().isoformat()})


# --------------------------------------------------------------- backtest
def _years_for(start: dt.date) -> int:
    """History needed for a backtest from `start`: the period itself + ~1.5y of indicator warm-up."""
    return int(min(30, max(5, (dt.date.today() - start).days / 365.25 + 2)))


def _bench_frames(symbol: str, years: int = 10) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    def one(item):
        name, b = item
        try:
            return name, prices.get_daily(b, years=years).df
        except Exception:
            return name, None
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = dict(ex.map(one, (("S&P 500", "^GSPC"), ("나스닥 100", "^NDX"), ("코스피", "^KS11"))))
    return {k: v for k, v in res.items() if v is not None}


def _regime_series(symbol: str, years: int = 10):
    try:
        bench = prices.get_daily("^KS11" if symbol.endswith((".KS", ".KQ")) else "^GSPC", years=years).df["close"]
        vix = prices.get_daily("^VIX", years=years).df["close"]
        return bt.regime_series(bench, vix)
    except Exception:
        return None


def _regime_breakdown(symbol: str, years: int, res: dict) -> dict | None:
    """How the run did per rate / sentiment / risk regime; never allowed to fail the backtest itself."""
    try:
        from ..analysis import regime_perf
        from ..services import regimes
        hold = res["benchmarks"].get("단순 보유")
        return regime_perf.breakdown(res["equity"], hold["equity"] if hold else None, res["trades"],
                                     regimes.dimensions(symbol, years))
    except Exception:
        return None


def _eq(series, step: int = 1) -> list[dict]:
    if series is None:
        return []
    s = series.iloc[::step]
    return [{"date": str(d.date()), "value": round(float(v), 2)} for d, v in s.items()]


@router.get("/backtest/strategies")
def api_strategies():
    return {"strategies": [{"id": s.id, "name": s.name, "description": s.description, "defaults": s.defaults,
                            "grid": s.grid, "needs_regime": s.needs_regime} for s in bt.STRATEGIES.values()],
            "execution": "t일 종가에 신호 판단 → t+1일 시가에 체결, 매수·매도마다 수수료와 슬리피지 반영, 배당 조정 가격 사용.",
            "benchmarks": ["단순 보유", "S&P 500", "나스닥 100", "코스피"]}


class BacktestRequest(BaseModel):
    ticker: str
    strategy: Literal["A", "B", "C", "D"] = "A"
    start: dt.date = dt.date(2019, 1, 1)
    end: dt.date = Field(default_factory=dt.date.today)
    params: dict[str, float] | None = None
    commission_bps: float = Field(5.0, ge=0, le=100)
    slippage_bps: float = Field(5.0, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self):
        if self.end <= self.start:
            raise ValueError("종료일은 시작일 이후여야 합니다")
        return self


def _load_bt(s: str, years: int = 10):
    ps = prices.get_daily(s, years=years)
    if len(ps.df) < 260:
        raise HTTPException(422, detail={"code": "history", "message": "백테스트에 필요한 가격 이력이 부족합니다 (1년 이상 필요)"})
    return ps


@router.post("/backtest/run")
def api_backtest(req: BacktestRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = sym(req.ticker)

    def run():
        years = _years_for(req.start)
        ps = _load_bt(s, years)
        params = {k: (int(v) if float(v).is_integer() else v) for k, v in (req.params or {}).items()}
        res = bt.backtest(ps.df, req.strategy, params, str(req.start), str(req.end), benchmarks=_bench_frames(s, years),
                          regime=_regime_series(s, years), commission_bps=req.commission_bps, slippage_bps=req.slippage_bps)
        if "error" in res:
            raise HTTPException(422, detail={"code": "period", "message": res["error"]})
        step = max(1, len(res["equity"]) // 600)
        out = {"symbol": s, "strategy": res["strategy"], "metrics": res["metrics"], "trades": res["trades"][-200:],
               "equity": _eq(res["equity"], step),
               "benchmarks": {k: {"metrics": v["metrics"], "equity": _eq(v["equity"], step)} for k, v in res["benchmarks"].items()},
               "config": {"start": str(req.start), "end": str(req.end), "commission_bps": req.commission_bps,
                          "slippage_bps": req.slippage_bps},
               "regimes": _regime_breakdown(s, years, res),
               "data": ps.meta(), "disclaimer": "과거 데이터에 대한 규칙의 성과는 미래 결과를 예측하지 않습니다."}
        db.add(Backtest(user_id=user.id, symbol=s, strategy=req.strategy, params=res["strategy"]["params"],
                        start_date=req.start, end_date=req.end, metrics=clean(res["metrics"])))
        db.commit()
        return clean(out)
    return guard(run, f"backtest {s}")


@router.get("/stock/{ticker}/backtest")
def api_backtest_get(ticker: str, strategy: Literal["A", "B", "C", "D"] = "A", start: dt.date = dt.date(2019, 1, 1),
                     end: dt.date | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return api_backtest(BacktestRequest(ticker=ticker, strategy=strategy, start=start, end=end or dt.date.today()), user, db)


class WalkForwardRequest(BaseModel):
    ticker: str
    strategy: Literal["A", "B", "C", "D"] = "A"
    mode: Literal["split", "rolling"] = "split"
    train: tuple[dt.date, dt.date] = (dt.date(2021, 1, 1), dt.date(2023, 12, 31))
    validation: tuple[dt.date, dt.date] = (dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    test: tuple[dt.date, dt.date] = (dt.date(2025, 1, 1), dt.date(2025, 12, 31))
    live: tuple[dt.date, dt.date] | None = None
    train_years: int = Field(3, ge=1, le=10)
    test_months: int = Field(6, ge=1, le=24)
    commission_bps: float = Field(5.0, ge=0, le=100)
    slippage_bps: float = Field(5.0, ge=0, le=100)

    @model_validator(mode="after")
    def _order(self):
        if self.mode == "split":
            seq = [self.train, self.validation, self.test] + ([self.live] if self.live else [])
            for a, b in seq:
                if b <= a:
                    raise ValueError("각 기간의 종료일은 시작일 이후여야 합니다")
            for (_, e1), (s2, _) in zip(seq, seq[1:]):
                if s2 <= e1:
                    raise ValueError("기간은 학습 < 검증 < 표본 외 < 실전 순서로 겹치지 않아야 합니다")
        return self


@router.post("/backtest/walk-forward")
def api_walk_forward(req: WalkForwardRequest):
    s = sym(req.ticker)

    def run():
        years = _years_for(req.train[0] if req.mode == "split" else dt.date.today() - dt.timedelta(days=365 * 10))
        ps = _load_bt(s, years)
        reg = _regime_series(s, years)
        if req.mode == "split":
            live = req.live or (req.test[1] + dt.timedelta(days=1), dt.date.today())
            periods = {"train": tuple(map(str, req.train)), "validation": tuple(map(str, req.validation)),
                       "test": tuple(map(str, req.test)), "live": tuple(map(str, live))}
            res = bt.walk_forward_split(ps.df, req.strategy, periods, regime=reg, commission_bps=req.commission_bps,
                                        slippage_bps=req.slippage_bps)
            if "error" in res:
                raise HTTPException(422, detail={"code": "period", "message": res["error"]})
            for k, v in res["periods"].items():
                if "equity" in v:
                    v["equity"] = _eq(v["equity"], max(1, len(v["equity"]) // 300))
                    v["trades"] = v.get("trades", [])[-50:]
        else:
            res = bt.walk_forward_rolling(ps.df, req.strategy, train_years=req.train_years, test_months=req.test_months,
                                          regime=reg, commission_bps=req.commission_bps, slippage_bps=req.slippage_bps)
            if "error" in res:
                raise HTTPException(422, detail={"code": "period", "message": res["error"]})
            res["oos_equity"] = _eq(res["oos_equity"], max(1, len(res["oos_equity"]) // 600))
        return clean({"symbol": s, "mode": req.mode, **res, "data": ps.meta()})
    return guard(run, f"walk-forward {s}")

