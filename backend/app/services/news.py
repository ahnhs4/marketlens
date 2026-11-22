"""News ingestion -> de-duplication -> classification (AI or rules) -> ranking -> summary.

Raw articles go to `news`; classifications to `news_analysis` (method + model);
narratives to `ai_summaries` with the time window and article ids they used.
An article already classified by the AI is never sent to the model again.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from sqlalchemy import select

from ..analysis import news_rules as nr
from ..cache import cached
from ..i18n_ko import CATEGORY_KO
from ..db import SessionLocal
from ..models import AISummary, News, NewsAnalysis
from ..providers.ai import claude
from ..providers.base import NewsItem
from ..providers.news import finnhub, google_news, sec
from ..providers.yahoo import yahoo
from .store import clean, utcnow

log = logging.getLogger(__name__)

MARKET_QUERIES: list[tuple[str, str, str]] = [
    ("Fed", "Federal Reserve OR FOMC OR Powell", "en"),
    ("Inflation", "inflation CPI OR PCE prices", "en"),
    ("Treasury", "Treasury yields bond market", "en"),
    ("Geopolitics", "geopolitical risk markets OR sanctions OR tariffs", "en"),
    ("Oil", "oil prices OPEC crude", "en"),
    ("AI", "artificial intelligence stocks AI spending", "en"),
    ("Semiconductors", "semiconductor stocks chipmakers", "en"),
    ("China", "China economy stimulus yuan", "en"),
    ("Japan", "Bank of Japan yen Nikkei", "en"),
    ("Korea", "KOSPI Korea stocks won", "en"),
    ("Korea", "코스피 증시 환율", "ko"),
    ("Europe", "ECB eurozone economy", "en"),
    ("Economic Data", "jobs report OR GDP OR retail sales OR PMI economy", "en"),
]


def _item_dict(n: NewsItem, category: str | None = None) -> dict:
    d = asdict(n)
    if category and not d.get("category"):
        d["category"] = category
    return d


def _fetch_market_raw() -> list[dict]:
    def one(q):
        cat, query, lang = q
        try:
            return [_item_dict(x, cat) for x in google_news.search(query, language=lang, limit=15, window="2d")]
        except Exception as e:
            log.info("news query failed (%s): %s", query, e)
            return []
    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(one, MARKET_QUERIES):
            items += res
    try:
        items += [_item_dict(x) for x in yahoo.news("stock market today", limit=15)]
    except Exception:
        pass
    if finnhub.enabled:
        try:
            items += [_item_dict(x) for x in finnhub.general_news()]
        except Exception as e:
            log.info("finnhub general news failed: %s", e)
    return items


def _persist(items: list[dict], scope: str) -> dict[str, int]:
    """Upsert articles by fingerprint, return {fingerprint: id}."""
    ids: dict[str, int] = {}
    with SessionLocal() as db:
        fps = [i["fingerprint"] for i in items]
        existing = {n.fingerprint: n for n in db.execute(select(News).where(News.fingerprint.in_(fps))).scalars()}
        for it in items:
            n = existing.get(it["fingerprint"])
            if n is None:
                n = News(fingerprint=it["fingerprint"], cluster_key=it["fingerprint"], title=it["title"][:2000],
                         url=it.get("url"), source=it.get("source"), provider=it.get("provider"),
                         scope=it.get("scope") if it.get("scope") in ("filing", "industry") else scope,
                         category=it.get("category"), tickers=it.get("tickers") or [], language=it.get("language"),
                         summary=it.get("summary"), published_at=it.get("published_at"))
                db.add(n)
            else:
                n.tickers = sorted(set(n.tickers or []) | set(it.get("tickers") or []))
            db.flush()
            existing[it["fingerprint"]] = n
            ids[it["fingerprint"]] = n.id
        db.commit()
    return ids


def _cached_ai(ids: list[int]) -> dict[int, dict]:
    with SessionLocal() as db:
        rows = db.execute(select(NewsAnalysis).where(NewsAnalysis.news_id.in_(ids), NewsAnalysis.method == "ai")).scalars().all()
    return {r.news_id: {"category": r.category, "sentiment": r.sentiment, "impact": r.impact,
                        "importance": r.importance, "confidence": r.confidence, "event_type": r.event_type,
                        "sectors": r.sectors, "tickers": r.tickers, "chain": r.chain, "rationale": r.rationale,
                        "method": "ai", "model": r.model} for r in rows}


def _save_analysis(news_id: int, a: dict) -> None:
    with SessionLocal() as db:
        db.add(NewsAnalysis(news_id=news_id, method=a["method"], model=a.get("model"), category=a.get("category"),
                            sentiment=a["sentiment"],
                            sentiment_score=nr.SENTIMENT_SCORE.get(a["sentiment"], 0.0), impact=a["impact"],
                            importance=a["importance"], confidence=a["confidence"], event_type=a.get("event_type"),
                            sectors=a.get("sectors") or [], tickers=a.get("tickers") or [], chain=a.get("chain") or [],
                            rationale=a.get("rationale")))
        db.commit()


def analyze(items: list[dict], scope: str, context: str | None = None, ai_limit: int = 40) -> tuple[list[dict], dict]:
    """Cluster, persist, classify. Returns (ranked clusters, method info)."""
    now = utcnow()
    clusters = nr.cluster([i for i in items if i.get("title")])
    ids = _persist(clusters, scope)
    for c in clusters:
        c["id"] = ids[c["fingerprint"]]
        c["rules"] = nr.classify(c, now)
    method = {"method": "rules", "model": None, "ai_enabled": claude.enabled, "ai_error": None}
    if claude.enabled:
        prior = _cached_ai([c["id"] for c in clusters])
        todo = [c for c in sorted(clusters, key=lambda x: -x["rules"]["importance"]) if c["id"] not in prior][:ai_limit]
        fresh = {}
        if todo:
            payload = [{"id": str(c["id"]), "title": c["title"], "source": c.get("source"),
                        "published_at": c.get("published_at"), "summary": (c.get("summary") or "")[:400],
                        "related_tickers": c.get("tickers"), "outlets": c["cluster_size"]} for c in todo]
            if context:
                payload.insert(0, {"id": "context", "title": f"Context: {context}", "source": None})
            res = claude.classify(payload)
            if res is None:
                method["ai_error"] = "AI analysis failed - showing rule-based classification"
            else:
                for c in todo:
                    a = res.get(str(c["id"]))
                    if a:
                        a.update(method="ai", model=claude.model)
                        fresh[c["id"]] = a
                        _save_analysis(c["id"], a)
        prior.update(fresh)
        for c in clusters:
            if c["id"] in prior:
                c["analysis"] = {**c["rules"], **{k: v for k, v in prior[c["id"]].items() if v is not None}}
        if prior:
            method.update(method="ai", model=claude.model)
    for c in clusters:
        if c.get("relevance") == "mentioned" and "analysis" not in c:
            # Stock pages: headlines that name the company matter more than sector chatter.
            c["rules"]["importance"] = min(100, c["rules"]["importance"] + 15)
            if c["rules"]["impact"] == "Low":
                c["rules"]["impact"] = "Medium"
        if "analysis" not in c:
            c["analysis"] = c["rules"]
            _save_analysis_once(c["id"], c["rules"])
        c.pop("rules", None)
        a = c["analysis"]
        # Priority: model/rule importance, then impact, then recency.
        age_h = (now - c["published_at"]).total_seconds() / 3600 if c.get("published_at") else 48
        c["priority"] = round(a["importance"] + 6 * nr.IMPACT_RANK.get(a["impact"], 0) + 4 * (c["cluster_size"] - 1)
                              - min(25, age_h / 2), 1)
    clusters.sort(key=lambda c: -c["priority"])
    return clusters, method


_saved_rules: set[int] = set()


def _save_analysis_once(news_id: int, a: dict) -> None:
    if news_id in _saved_rules:
        return
    _saved_rules.add(news_id)
    try:
        _save_analysis(news_id, a)
    except Exception as e:
        log.debug("save analysis failed: %s", e)


def _rule_summary(clusters: list[dict], macro_ctx: dict | None) -> dict:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for c in clusters[:60]:
        by_cat[c["analysis"]["category"]].append(c)
    bullets = []
    for cat, cs in sorted(by_cat.items(), key=lambda kv: -sum(x["priority"] for x in kv[1]))[:6]:
        sc = sum(nr.SENTIMENT_SCORE.get(x["analysis"]["sentiment"], 0) for x in cs) / len(cs)
        tone = "긍정적" if sc > 0.15 else "부정적" if sc < -0.15 else "혼조"
        bullets.append(f"{CATEGORY_KO.get(cat, cat)}: 기사 {len(cs)}건, 분위기 {tone} - 대표: \"{cs[0]['title'][:110]}\"")
    risks = [f"{CATEGORY_KO.get(c['analysis']['category'], c['analysis']['category'])}: {c['title'][:120]}" for c in clusters
             if c["analysis"]["sentiment"] in ("Bearish", "Very Bearish") and c["analysis"]["impact"] in ("High", "Critical")][:4]
    opps = [f"{CATEGORY_KO.get(c['analysis']['category'], c['analysis']['category'])}: {c['title'][:120]}" for c in clusters
            if c["analysis"]["sentiment"] in ("Bullish", "Very Bullish") and c["analysis"]["impact"] in ("High", "Critical")][:4]
    if macro_ctx:
        bullets += [r["text"] for r in macro_ctx.get("reasons", [])[:2]]
    counts = Counter(c["analysis"]["sentiment"] for c in clusters)
    ko = {"Very Bullish": "매우 긍정", "Bullish": "긍정", "Neutral": "중립", "Bearish": "부정", "Very Bearish": "매우 부정"}
    return {"headline": f"고유 기사 {len(clusters)}건 · 감성 분포: " +
                        ", ".join(f"{ko.get(k, k)} {v}" for k, v in counts.most_common()),
            "bullets": bullets, "risks": risks, "opportunities": opps, "themes": []}


def summarize(scope: str, clusters: list[dict], macro_ctx: dict | None, method: dict) -> dict:
    top = clusters[:25]
    times = [c["published_at"] for c in top if c.get("published_at")]
    ctx = {"scope": scope, "macro": macro_ctx or {}, "headlines": [
        {"id": str(c["id"]), "title": c["title"], "source": c.get("source"), "published_at": c.get("published_at"),
         "category": c["analysis"]["category"], "sentiment": c["analysis"]["sentiment"], "impact": c["analysis"]["impact"],
         "outlets": c["cluster_size"]} for c in top]}
    content, used = None, "rules"
    if method.get("method") == "ai":
        content = claude.summarize(ctx)
        used = "ai" if content else "rules"
    if content is None:
        content = _rule_summary(clusters, macro_ctx)
    row = AISummary(scope=scope, method=used, model=claude.model if used == "ai" else None, content=clean(content),
                    input_ids=[c["id"] for c in top], data_from=min(times) if times else None,
                    data_to=max(times) if times else None)
    with SessionLocal() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
    return {**content, "method": used, "model": row.model, "generated_at": row.created_at.isoformat(),
            "data_from": row.data_from.isoformat() if row.data_from else None,
            "data_to": row.data_to.isoformat() if row.data_to else None, "input_count": len(top)}


def _public(c: dict) -> dict:
    a = c["analysis"]
    return clean({"id": c["id"], "title": c["title"], "url": c.get("url"), "source": c.get("source"),
                  "provider": c.get("provider"), "published_at": c.get("published_at"), "language": c.get("language"),
                  "scope": c.get("scope"), "tickers": sorted(set(c.get("tickers") or []) | set(a.get("tickers") or [])),
                  "cluster_size": c["cluster_size"], "also": c["also"][:6], "priority": c["priority"],
                  "category": a["category"], "event_type": a.get("event_type"), "sentiment": a["sentiment"],
                  "impact": a["impact"], "importance": a["importance"], "confidence": a["confidence"],
                  "sectors": a.get("sectors") or [], "chain": a.get("chain") or [], "rationale": a.get("rationale"),
                  "method": a.get("method"), "model": a.get("model"), "summary": c.get("summary")})


def market_news(macro_ctx: dict | None = None) -> dict:
    def build():
        fetched_at = utcnow()
        raw = _fetch_market_raw()
        if not raw:
            return {"status": "News unavailable", "items": [], "summary": None, "fetched_at": fetched_at.isoformat()}
        clusters, method = analyze(raw, "market")
        summary = summarize("market", clusters, macro_ctx, method)
        by_cat = Counter(c["analysis"]["category"] for c in clusters)
        return {"items": [_public(c) for c in clusters[:80]], "summary": summary, "method": method,
                "categories": dict(by_cat), "raw_count": len(raw), "unique_count": len(clusters),
                "fetched_at": fetched_at.isoformat(),
                "sources": ["Google News RSS", "Yahoo Finance"] + (["Finnhub"] if finnhub.enabled else [])}
    return cached("news:market", 600, build)


def stock_news(symbol: str, name: str | None, name_ko: str | None, industry: str | None) -> dict:
    def build():
        fetched_at = utcnow()
        items: list[dict] = []
        jobs = []
        is_kr = symbol.endswith((".KS", ".KQ"))
        base = symbol.split(".")[0]
        jobs.append(lambda: [_item_dict(x) for x in yahoo.news(symbol if not is_kr else (name or base), limit=15)])
        if is_kr:
            q = name_ko or name or base
            jobs.append(lambda: [_item_dict(x) for x in google_news.search(q, language="ko", limit=20, window="7d")])
            if name:
                jobs.append(lambda: [_item_dict(x) for x in google_news.search(f'"{name}"', language="en", limit=10, window="7d")])
        else:
            q = f'"{name}" OR {symbol} stock' if name else f"{symbol} stock"
            jobs.append(lambda: [_item_dict(x) for x in google_news.search(q, language="en", limit=20, window="7d")])
            jobs.append(lambda: [_item_dict(x) for x in sec.filings(symbol, limit=10)])
            if finnhub.enabled:
                jobs.append(lambda: [_item_dict(x) for x in finnhub.company_news(symbol)])
        if industry:
            jobs.append(lambda: [{**_item_dict(x, None), "scope": "industry"} for x in
                                 google_news.search(f"{industry} industry", language="en", limit=8, window="3d")])

        def run(j):
            try:
                return j()
            except Exception as e:
                log.info("stock news source failed: %s", e)
                return []
        with ThreadPoolExecutor(max_workers=5) as ex:
            for res in ex.map(run, jobs):
                items += res
        keys = {k.lower() for k in (base, symbol, name_ko or "") if k}
        if name:
            first = re.split(r"[\s,.]+", name)[0]
            keys |= {name.lower(), first.lower()} if len(first) > 2 else {name.lower()}
        kept = []
        for it in items:
            if it.get("scope") in ("industry", "filing"):
                kept.append(it)
                continue
            tagged = symbol in (it.get("tickers") or [])
            mentioned = any(k and k in it["title"].lower() for k in keys)
            if not (tagged or mentioned):
                continue  # drop search noise that does not mention the company
            it["scope"] = "stock"
            it["relevance"] = "mentioned" if mentioned else "tagged"
            if symbol not in (it.get("tickers") or []):
                it["tickers"] = list(it.get("tickers") or []) + [symbol]
            kept.append(it)
        items = kept
        if not items:
            return {"status": "News unavailable", "items": [], "summary": None, "fetched_at": fetched_at.isoformat()}
        clusters, method = analyze(items, "stock", context=f"Company in focus: {name or symbol} ({symbol}), industry {industry}")
        summary = summarize(f"stock:{symbol}", clusters, None, method)
        filings = [c for c in clusters if c.get("scope") == "filing"]
        return {"items": [_public(c) for c in clusters if c.get("scope") != "filing"][:40],
                "filings": [_public(c) for c in filings][:10], "summary": summary, "method": method,
                "fetched_at": fetched_at.isoformat(), "unique_count": len(clusters), "raw_count": len(items)}
    return cached(f"news:stock:{symbol}", 600, build)


def recent_sentiment(symbol: str, days: int = 7) -> dict | None:
    """Aggregate stored analyses for a ticker (used by alerts / briefing)."""
    since = utcnow() - dt.timedelta(days=days)
    with SessionLocal() as db:
        rows = db.execute(select(News, NewsAnalysis).join(NewsAnalysis, NewsAnalysis.news_id == News.id)
                          .where(News.published_at >= since)).all()
    rel = [(n, a) for n, a in rows if symbol in (n.tickers or []) or symbol in (a.tickers or [])]
    if not rel:
        return None
    sc = [a.sentiment_score or 0 for _, a in rel]
    return {"count": len(rel), "avg_sentiment": sum(sc) / len(sc),
            "high_impact": sum(1 for _, a in rel if a.impact in ("High", "Critical"))}
