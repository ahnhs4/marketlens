"""Daily investment briefing and the personalised dashboard summary.

Every bullet is generated from measured data (index moves, yield/FX/commodity
changes, sector returns, scan results, ranked news). Interpretations come from
explicit if-then rules, listed with the numbers that triggered them.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func, select

from ..cache import cached
from ..db import SessionLocal
from ..i18n_ko import COMPONENT_KO
from ..models import Alert, AlertEvent, Signal
from . import macro, market, news, recommendations, universe
from .store import clean, utcnow

log = logging.getLogger(__name__)


def _chg(s, n=1):
    s = s.dropna()
    if len(s) <= n:
        return None
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) * 100


def _bp(s, n=1):
    s = s.dropna()
    if len(s) <= n:
        return None
    return float(s.iloc[-1] - s.iloc[-1 - n]) * 100


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        log.info("briefing section failed: %s", e)
        return default


def build_briefing(watchlist: list[str] | None = None) -> dict:
    snap = _safe(market.snapshot, {"cards": []})
    series, _ = _safe(macro.load_all, ({}, {}))
    sect = _safe(market.sectors, {"sectors": []})
    b = universe.latest_breadth()
    reg = _safe(lambda: macro.regime(b.get("pct_above_sma200") if b else None))
    nws = _safe(lambda: news.market_news({"reasons": (reg or {}).get("reasons", [])[:4]}), {"items": []})

    cards = {c["symbol"]: c for c in snap.get("cards", [])}
    market_lines = []
    for sym in ("^GSPC", "^NDX", "^DJI", "^RUT", "^KS11", "^KQ11", "^VIX"):
        c = cards.get(sym)
        if c and c.get("change_pct") is not None:
            market_lines.append(f"{c['name']} {c['change_pct']:+.2f}%" + (f" ({c['price']:,.2f})" if c.get("price") else ""))

    t10 = _bp(series["tnx"]) if "tnx" in series else None
    t10w = _bp(series["tnx"], 5) if "tnx" in series else None
    dxy = _chg(series["dxy"]) if "dxy" in series else None
    oil = _chg(series["oil"]) if "oil" in series else None
    gold = _chg(series["gold"]) if "gold" in series else None
    krw = _chg(series["usdkrw"]) if "usdkrw" in series else None
    macro_lines = []
    if t10 is not None:
        macro_lines.append(f"미 10년물 금리 당일 {t10:+.0f}bp (5거래일 {t10w:+.0f}bp)")
    for v, lab in ((dxy, "달러인덱스"), (oil, "WTI 유가"), (gold, "금"), (krw, "원/달러 환율")):
        if v is not None:
            macro_lines.append(f"{lab} {v:+.2f}%")

    ndx = (cards.get("^NDX") or {}).get("change_pct")
    vix = (cards.get("^VIX") or {}).get("change_pct")
    interp = []
    if t10 is not None and ndx is not None:
        if t10 <= -3 and ndx > 0:
            interp.append("금리 하락이 성장주를 지지")
        elif t10 >= 3 and ndx < 0:
            interp.append("금리 상승이 성장주·장기 듀레이션 자산을 압박")
        elif t10 >= 3 and ndx > 0:
            interp.append("금리 상승에도 주가 상승 - 성장 기대가 금리 부담을 상쇄")
    if oil is not None and abs(oil) >= 2:
        interp.append(f"유가 {'상승' if oil > 0 else '하락'}({oil:+.1f}%) - "
                      f"{'기대인플레이션 주의, 에너지 업종 유리' if oil > 0 else '인플레이션 압력 완화, 소비자에 유리'}")
    if vix is not None and abs(vix) >= 5:
        interp.append(f"변동성 {'진정' if vix < 0 else '확대'} (VIX {vix:+.1f}%)")
    if dxy is not None and abs(dxy) >= 0.4:
        interp.append(f"달러 {'강세' if dxy > 0 else '약세'}({dxy:+.2f}%) - 신흥국·원화 자산에 {'역풍' if dxy > 0 else '순풍'}")
    secs = [s for s in sect.get("sectors", []) if s.get("perf")]
    smh = next((s for s in secs if s["symbol"] == "SMH"), None)
    spy_1d = (cards.get("^GSPC") or {}).get("change_pct")
    if smh and spy_1d is not None and smh["perf"]["1d"] - spy_1d >= 0.75:
        interp.append(f"반도체 모멘텀 강화 (SMH {smh['perf']['1d']:+.1f}% vs S&P {spy_1d:+.1f}%)")

    strong = sorted(secs, key=lambda s: -s["perf"]["1d"])[:3]
    weak = sorted(secs, key=lambda s: s["perf"]["1d"])[:3]
    top_news = [i for i in nws.get("items", []) if i.get("published_at") and
                dt.datetime.fromisoformat(i["published_at"]) >= utcnow() - dt.timedelta(hours=24)][:5]
    setups = recommendations.technical_ranking("ALL", limit=5)["items"]
    risks = [r["text"] for r in (reg or {}).get("reasons", []) if r["direction"] == "headwind"][:3]
    risks += [f"뉴스: {i['title'][:120]}" for i in nws.get("items", [])
              if i["impact"] in ("High", "Critical") and i["sentiment"] in ("Bearish", "Very Bearish")][:2]
    wl_changes = watchlist_changes(watchlist or [])
    return clean({
        "date": dt.date.today().isoformat(), "generated_at": utcnow().isoformat(),
        "regime": {k: reg[k] for k in ("label", "score", "quadrant", "overlays")} if reg else None,
        "market": market_lines, "macro": macro_lines, "interpretation": interp,
        "news": [{"title": i["title"], "source": i["source"], "published_at": i["published_at"], "impact": i["impact"],
                  "sentiment": i["sentiment"], "url": i["url"]} for i in top_news],
        "strong_sectors": [f"{s['name']} {s['perf']['1d']:+.2f}%" for s in strong],
        "weak_sectors": [f"{s['name']} {s['perf']['1d']:+.2f}%" for s in weak],
        "top_setups": [{"symbol": x["symbol"], "name": x["name"], "score": x["score"], "signal": x["signal"],
                        "reason": x["reason"][:2]} for x in setups],
        "risks": risks, "watchlist_changes": wl_changes,
        "news_summary": nws.get("summary"),
        "method": "실시간 데이터 기반 규칙" + (" + AI 뉴스 요약" if (nws.get("summary") or {}).get("method") == "ai" else ""),
    })


def briefing(watchlist: list[str] | None = None) -> dict:
    key = f"briefing:{','.join(sorted(watchlist or []))}"
    return cached(key, 600, lambda: build_briefing(watchlist))


def watchlist_changes(symbols: list[str], since: dt.date | None = None) -> list[dict]:
    if not symbols:
        return []
    out = []
    with SessionLocal() as db:
        for s in symbols[:50]:
            rows = db.execute(select(Signal).where(Signal.symbol == s).order_by(Signal.as_of.desc()).limit(10)).scalars().all()
            if len(rows) < 2:
                continue
            cur = rows[0]
            prev = next((r for r in rows[1:] if since is None or r.as_of <= since), rows[1])
            if cur.technical is None or prev.technical is None:
                continue
            d = cur.technical - prev.technical
            drivers = []
            for k, v in (cur.components or {}).items():
                pv = (prev.components or {}).get(k)
                if pv is not None and v is not None and abs(v - pv) >= 8:
                    drivers.append(f"{COMPONENT_KO.get(k, k)} {pv:.0f}→{v:.0f}")
            if abs(d) >= 3 or cur.signal != prev.signal:
                out.append({"symbol": s, "from": prev.technical, "to": cur.technical, "change": d,
                            "signal": [prev.signal, cur.signal], "from_date": str(prev.as_of), "to_date": str(cur.as_of),
                            "drivers": drivers[:3]})
    return sorted(out, key=lambda x: -abs(x["change"]))


def dashboard(user_id: int, prefs: dict, watchlist: list[str], last_visit: dt.datetime | None) -> dict:
    b = universe.latest_breadth()
    reg = _safe(lambda: macro.regime(b.get("pct_above_sma200") if b else None))
    mac = _safe(recommendations.macro_ranking, {"groups": []})
    groups = mac.get("groups", [])
    pref_secs = set(prefs.get("preferred_sectors") or [])
    top_group = next((g for g in groups if g["group"] in pref_secs), groups[0] if groups else None)
    tech = _safe(lambda: recommendations.technical_ranking(
        {"US": "SP500", "Korea": "KOSPI"}.get(prefs.get("preferred_market", "Global"), "ALL"), limit=1), {"items": []})
    nws = _safe(news.market_news, {"items": []})
    important = [i for i in nws.get("items", []) if i["impact"] in ("High", "Critical")
                 and i.get("published_at") and dt.datetime.fromisoformat(i["published_at"]) >= utcnow() - dt.timedelta(hours=24)]
    with SessionLocal() as db:
        unread = db.execute(select(func.count(AlertEvent.id)).join(Alert).where(Alert.user_id == user_id,
                                                                                AlertEvent.read.is_(False))).scalar() or 0
    headwinds = [r for r in (reg or {}).get("reasons", []) if r["direction"] == "headwind"]
    supports = [r for r in (reg or {}).get("reasons", []) if r["direction"] == "supportive"]
    since = last_visit.date() - dt.timedelta(days=0) if last_visit else None
    return clean({
        "regime": {k: reg[k] for k in ("label", "score", "quadrant", "overlays", "subscores")} if reg else None,
        "top_macro_signal": supports[0]["text"] if supports else None,
        "top_risk": headwinds[0]["text"] if headwinds else None,
        "top_sector": {"group": top_group["group"], "score": top_group["score"], "kind": top_group["kind"]} if top_group else None,
        "top_setup": tech["items"][0] if tech.get("items") else None,
        "important_news_count": len(important), "important_news": [
            {"title": i["title"], "source": i["source"], "impact": i["impact"], "sentiment": i["sentiment"],
             "published_at": i["published_at"], "url": i["url"]} for i in important[:5]],
        "watchlist_alerts": unread, "since_last_visit": watchlist_changes(watchlist, since),
        "last_visit": last_visit.isoformat() if last_visit else None,
        "breadth": b, "scan": universe.status(), "updated_at": utcnow().isoformat(),
    })
