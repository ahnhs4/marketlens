"""Market, macro, news, search, briefing, dashboard, recommendations, screener, universe."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User, Watchlist
from ..providers.ai import claude
from ..providers.base import health
from ..providers.fred import fred
from ..providers.news import finnhub
from ..security import current_user
from ..services import (briefing, dart_fin, fear_greed, fedwatch, macro, market, news, recap, recommendations, settings_store,
                       leading_study, risk_signals, thirteenf, trend_flow, universe, valueup)
from ..services.symbols import index
from ..cache import cache, cached
from .common import guard

router = APIRouter(prefix="/api")


@router.get("/health")
def api_health():
    s = get_settings()
    return {"status": "ok", "providers": health.snapshot(), "cache": cache.backend,
            "database": s.database_url.split(":", 1)[0],
            "features": {"ai_analysis": claude.enabled, "ai_model": claude.model if claude.enabled else None,
                         "fred_mode": fred.mode, "finnhub": finnhub.enabled, "auth_required": s.auth_required},
            "universe_scan": universe.status()}


@router.get("/search")
def api_search(q: str = Query(..., min_length=1, max_length=50), limit: int = Query(10, ge=1, le=25)):
    res = guard(lambda: index.search(q, limit=limit), "search")
    return {"query": q, "results": [r.__dict__ for r in res]}


# ------------------------------------------------------------------ market
@router.get("/market/overview")
def api_market_overview():
    return guard(market.snapshot, "market snapshot")


@router.get("/market/sectors")
def api_market_sectors():
    return guard(market.sectors, "sectors")


@router.get("/trend/flow")
def api_trend_flow(market: Literal["KR", "US"] = "KR", mode: Literal["bull", "normal", "bear"] | None = None,
                   conds: str | None = Query(None, max_length=400)):
    """추세추종 4단계 플로우. `conds` (comma list) overrides the `mode` preset; no mode = suggested by the index trend."""
    keys = [c for c in conds.split(",") if c] if conds is not None else None
    return guard(lambda: trend_flow.flow(market, mode, keys), "trend flow")


@router.get("/trend/status")
def api_trend_status(market: Literal["KR", "US"] = "KR"):
    return trend_flow.history_status(market)


@router.post("/trend/refresh")
def api_trend_refresh():
    trend_flow.start_history_fill()
    return trend_flow.history_status("KR")


@router.get("/kr/valueup")
def api_valueup():
    """Exchange's low-PBR list + value-up plan disclosures + DART-computed PBR/ROE/score for every KR listing."""
    return guard(valueup.overview, "valueup")


@router.get("/kr/dart/status")
def api_dart_status():
    return dart_fin.status()


@router.post("/kr/dart/refresh")
def api_dart_refresh(background: BackgroundTasks, force: bool = False):
    """Collect main accounts for every listed company (runs in the background; ~5-10 minutes the first time)."""
    if not dart_fin.enabled():
        return {"ok": False, "message": "DART_API_KEY가 설정되지 않았습니다 (.env에 추가 후 백엔드 재시작)"}
    if dart_fin.status()["state"] == "running":
        return {"ok": False, "message": "이미 수집 중입니다", "status": dart_fin.status()}
    background.add_task(dart_fin.refresh_market, force)
    return {"ok": True, "message": "수집을 시작했습니다"}


@router.get("/macro/fedwatch")
def api_fedwatch():
    """FOMC hike / hold / cut probabilities from Fed Funds futures, CME FedWatch method."""
    return guard(lambda: fedwatch.fedwatch(), "fedwatch")


@router.get("/institutions/top")
def api_institutions(group: Literal["all", "hedge", "bank"] = "all", metric: Literal["weight", "bought"] = "weight",
                     managers: int = Query(20, ge=5, le=40), top: int = Query(10, ge=5, le=30)):
    """13F: the stocks the largest hedge funds / banks hold (or bought last quarter) most, as an average portfolio weight."""
    def load():
        res = thirteenf.top_stocks(group, managers, top, metric)
        if not res.get("ready"):
            thirteenf.start_ingest()
        return res
    settled = thirteenf.has_data() and thirteenf.status()["state"] != "running"  # don't cache a half-loaded set
    return guard(lambda: cached(f"13f:{group}:{metric}:{managers}:{top}", 6 * 3600, load) if settled else load(), "13F")


@router.get("/institutions/status")
def api_institutions_status():
    return thirteenf.status()


@router.post("/institutions/refresh")
def api_institutions_refresh():
    thirteenf.start_ingest()
    return thirteenf.status()


@router.get("/market/recap")
def api_recap(hours: int = Query(12, ge=1, le=48)):
    """지난 N시간 두 문단 요약 + 중요도 가중치."""
    return guard(lambda: recap.recap(hours), "recap")


@router.get("/market/fear-greed")
def api_fear_greed():
    """공포·탐욕 지수: current value, comparisons, 7 indicator scores and up to 3 years of history."""
    return guard(fear_greed.index_payload, "fear & greed")


@router.get("/market/breadth")
def api_market_breadth():
    return universe.breadth_all()


@router.get("/market/correlation")
def api_market_correlation(window: Literal["30d", "90d", "1y", "3y"] = "90d"):
    return guard(lambda: market.correlation(window), "correlation")


@router.get("/market/risk/credit")
def api_risk_credit(threshold: float = Query(7.8, ge=1, le=30)):
    """하이일드 CCC - B 스프레드: 기준선 돌파일과 그 뒤 S&P 500 하락."""
    return guard(lambda: risk_signals.credit(round(threshold, 2)), "credit spreads")


@router.get("/market/risk/correlation")
def api_risk_correlation():
    """시장 상관관계 (Cboe 내재 상관, 섹터 실현 상관)와 S&P 500 하락 구간."""
    return guard(risk_signals.correlation, "market correlation")


@router.get("/market/risk/leading")
def api_risk_leading():
    """선행지표 검증: 27개 규칙이 S&P 500 10% 이상 하락 전에 켜져 있었는지."""
    return guard(leading_study.study, "leading indicators")


# ------------------------------------------------------------------- macro
@router.get("/macro/overview")
def api_macro_overview():
    return guard(macro.indicator_cards, "macro")


@router.get("/macro/regime")
def api_macro_regime():
    b = universe.latest_breadth()
    return guard(lambda: macro.regime(b.get("pct_above_sma200") if b else None), "regime")


@router.get("/macro/series/{key}")
def api_macro_series(key: str, years: int = Query(5, ge=1, le=12)):
    return guard(lambda: macro.series_history(key, years), "macro series")


# -------------------------------------------------------------------- news
@router.get("/news/market")
def api_news_market():
    def run():
        b = universe.latest_breadth()
        try:
            reg = macro.regime(b.get("pct_above_sma200") if b else None)
            ctx = {"regime": reg["label"], "score": reg["score"], "reasons": [r["text"] for r in reg["reasons"][:6]]}
        except Exception:
            ctx = None
        return news.market_news(ctx)
    return guard(run, "market news")


# ---------------------------------------------------------- personalised
def _watchlist_symbols(db: Session, user: User) -> list[str]:
    wls = db.execute(select(Watchlist).where(Watchlist.user_id == user.id)).scalars().all()
    return sorted({i.symbol for w in wls for i in w.items})


@router.get("/briefing")
def api_briefing(user: User = Depends(current_user), db: Session = Depends(get_db)):
    syms = _watchlist_symbols(db, user)
    return guard(lambda: briefing.briefing(syms), "briefing")


@router.get("/dashboard")
def api_dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    st = settings_store.get(db, user)
    prev = st["previous_visit_at"]
    import datetime as dt

    last = dt.datetime.fromisoformat(prev) if prev else None
    out = guard(lambda: briefing.dashboard(user.id, st["profile"], _watchlist_symbols(db, user), last), "dashboard")
    settings_store.touch_visit(db, user)
    return out


# --------------------------------------------------------- recommendations
Universe = Literal["ALL", "SP500", "NDX", "KOSPI", "KOSDAQ", "WATCHLIST"]


@router.get("/recommendations/macro")
def api_reco_macro():
    return guard(recommendations.macro_ranking, "macro ranking")


@router.get("/recommendations/technical")
def api_reco_technical(universe_: Universe = Query("ALL", alias="universe"), limit: int = Query(30, ge=1, le=200)):
    return guard(lambda: recommendations.technical_ranking(universe_, limit), "technical ranking")


@router.get("/recommendations/combined")
def api_reco_combined(universe_: Universe = Query("ALL", alias="universe"), limit: int = Query(30, ge=1, le=200),
                      w_tech: float | None = Query(None, ge=0, le=1), w_macro: float | None = Query(None, ge=0, le=1),
                      user: User = Depends(current_user), db: Session = Depends(get_db)):
    w = settings_store.get(db, user)["weights"]["combined_ranking"]
    wt = w_tech if w_tech is not None else w["technical"]
    wm = w_macro if w_macro is not None else w["macro"]
    return guard(lambda: recommendations.combined_ranking(universe_, wt, wm, limit), "combined ranking")


@router.get("/recommendations/top")
def api_reco_top(user: User = Depends(current_user), db: Session = Depends(get_db)):
    st = settings_store.get(db, user)
    prefs = {**st["profile"], "w_tech": st["weights"]["combined_ranking"]["technical"],
             "w_macro": st["weights"]["combined_ranking"]["macro"]}
    return guard(lambda: recommendations.top_opportunities(prefs), "top opportunities")


# ---------------------------------------------------------------- screener
class Filter(BaseModel):
    field: Literal[tuple(recommendations.FIELDS)]  # type: ignore[valid-type]
    op: Literal[">", ">=", "<", "<=", "="]
    value: float


class ScreenRequest(BaseModel):
    universe: Universe = "ALL"
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    above_sma200: bool | None = None
    sma20_gt_sma60: bool | None = None
    near_breakout: bool | None = None
    sort: str = "technical"
    desc: bool = True
    limit: int = Field(100, ge=1, le=500)


@router.post("/screener")
def api_screener(req: ScreenRequest):
    return guard(lambda: recommendations.screen([f.model_dump() for f in req.filters], req.universe, req.sort, req.desc,
                                                req.limit, {"above_sma200": req.above_sma200,
                                                            "sma20_gt_sma60": req.sma20_gt_sma60,
                                                            "near_breakout": req.near_breakout}), "screener")


@router.get("/screener")
def api_screener_default(universe_: Universe = Query("ALL", alias="universe")):
    return api_screener(ScreenRequest(universe=universe_))


# ---------------------------------------------------------------- universe
@router.get("/universe/status")
def api_universe_status():
    c = universe.constituents() if cache.get("universe:constituents") else None
    return {"scan": universe.status(), "results": len(universe.results()),
            "sizes": {k: len(c.get(k, [])) for k in ("SP500", "NDX", "KOSPI", "KOSDAQ")} if c else None}


@router.post("/universe/scan")
def api_universe_scan(background: BackgroundTasks, _: User = Depends(current_user)):
    if universe.status()["state"] != "running":
        background.add_task(universe.scan, "ALL")
    return {"scan": universe.status()}
