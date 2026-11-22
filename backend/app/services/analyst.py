"""Sell-side view of one stock: consensus target, the latest reports and how the view has been moving.

* US: Yahoo - every rating / price-target action (firm, date, grade, target), the consensus target, the rating
  spread and EPS estimate revisions. Full US research reports are paywalled; the reason behind an action comes
  from a news headline about it (Google News, matched by firm name and date), when one exists.
* KR: Naver research - each broker report with its target price, rating and the broker's own summary.
The flow paragraph is assembled by rules from these records (no AI key is configured), so every sentence
traces back to a listed report or number.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from html import unescape

import httpx
import numpy as np
import pandas as pd

from ..analysis.analyst_evidence import micro_score
from ..analysis.timing_evidence import market_of
from ..cache import cached
from ..providers.news import GoogleNewsRSS
from .store import clean, utcnow

log = logging.getLogger(__name__)
NAVER = "https://stock.naver.com/api/stockSecurity/researches/v2/company"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Referer": "https://stock.naver.com/research/company"}

GRADE_KO = {"strong buy": "강력 매수", "buy": "매수", "outperform": "시장수익률 상회", "overweight": "비중 확대", "positive": "긍정적",
            "accumulate": "매집", "add": "추가", "market outperform": "시장수익률 상회", "sector outperform": "업종 상회",
            "hold": "보유", "neutral": "중립", "equal-weight": "시장 비중", "equal weight": "시장 비중", "market perform": "시장수익률",
            "sector perform": "업종 수준", "sector weight": "업종 비중", "in-line": "시장 수준", "peer perform": "동종 수준",
            "mixed": "중립", "underweight": "비중 축소", "underperform": "시장수익률 하회", "reduce": "비중 축소", "sell": "매도",
            "strong sell": "강력 매도", "negative": "부정적", "speculative buy": "투기적 매수", "long-term buy": "장기 매수",
            "trading buy": "단기 매수", "marketperform": "시장수익률", "not rated": "의견 없음"}
ACTION_KO = {"up": "상향", "down": "하향", "init": "신규", "main": "유지", "reit": "재확인"}
PT_KO = {"Raises": "목표가 상향", "Lowers": "목표가 하향", "Maintains": "목표가 유지", "Announces": "목표가 제시", "Adjusts": "목표가 조정",
         "Removes": "목표가 철회"}
OPINION_RANK = {"strongbuy": 3, "buy": 2, "outperform": 2, "hold": 1, "neutral": 1, "marketperform": 1, "reduce": 0,
                "sell": 0, "underperform": 0, "strongsell": 0}
OPINION_KO = {"buy": "매수", "strongbuy": "강력 매수", "hold": "보유", "neutral": "중립", "sell": "매도", "outperform": "시장수익률 상회",
              "marketperform": "시장수익률", "underperform": "시장수익률 하회", "reduce": "비중 축소"}
SHORT_FIRM = {"citigroup": ["citi"], "bank of america": ["bofa", "bank of america"], "jp morgan": ["jpmorgan", "jp morgan", "j.p. morgan"],
              "evercore isi group": ["evercore"], "td cowen": ["cowen"], "robert w. baird": ["baird"], "keybanc": ["keybanc", "key banc"],
              "d.a. davidson": ["davidson"], "b of a securities": ["bofa", "bank of america"], "bofa securities": ["bofa", "bank of america"]}
COMPANY_ALIAS = {"GOOGL": ["google"], "GOOG": ["google"], "META": ["facebook"], "BRK-B": ["berkshire"], "IBM": ["ibm"]}
GENERIC = {"bank", "securities", "capital", "group", "partners", "research", "markets", "the", "and", "global", "investment"}


def _grade(s: str | None) -> str:
    s = (s or "").strip()
    return GRADE_KO.get(s.lower(), s) if s else ""


def _plain(html: str | None) -> str:
    t = re.sub(r"<br\s*/?>", " ", html or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", unescape(t)).strip()


def _short_name(name: str | None, symbol: str) -> str:
    n = re.sub(r"[,.]|\b(Inc|Corp|Corporation|Company|Co|Ltd|Limited|Holdings?|PLC|Group|Class [A-C]|N\.?V|S\.?A)\b", " ", name or "", flags=re.I)
    n = re.sub(r"\s+", " ", n).strip()
    return n or symbol


def _firm_aliases(firm: str) -> list[str]:
    f = firm.lower().strip()
    out = {f, f.replace(" ", "")}
    out.update(SHORT_FIRM.get(f, []))
    words = [w for w in re.findall(r"[a-z&]+", f) if w not in GENERIC]
    if words and len(words[0]) >= 4:
        out.add(words[0])
    return [a for a in out if len(a) >= 3]


# ------------------------------------------------------------------ US
def _us(symbol: str) -> dict:
    import yfinance as yf
    t = yf.Ticker(symbol)
    info = {}
    try:
        info = t.info or {}
    except Exception:
        pass
    ud = None
    try:
        ud = t.upgrades_downgrades
    except Exception as e:
        log.info("upgrades_downgrades %s: %s", symbol, e)
    actions = []
    if ud is not None and not ud.empty:
        d = ud.reset_index()
        for _, r in d.iterrows():
            actions.append({"date": pd.Timestamp(r["GradeDate"]).tz_localize(None) if pd.Timestamp(r["GradeDate"]).tzinfo else pd.Timestamp(r["GradeDate"]),
                            "firm": str(r.get("Firm") or ""), "action": str(r.get("Action") or ""), "to": str(r.get("ToGrade") or ""),
                            "from": str(r.get("FromGrade") or ""), "pt_action": str(r.get("priceTargetAction") or ""),
                            "pt": float(r["currentPriceTarget"]) if pd.notna(r.get("currentPriceTarget")) and r.get("currentPriceTarget") else None,
                            "pt_prior": float(r["priorPriceTarget"]) if pd.notna(r.get("priorPriceTarget")) and r.get("priorPriceTarget") else None})
        actions.sort(key=lambda a: a["date"], reverse=True)
    targets = {}
    try:
        targets = t.analyst_price_targets or {}
    except Exception:
        pass
    rec = None
    try:
        rs = t.recommendations_summary
        if rs is not None and not rs.empty:
            row = rs[rs["period"] == "0m"].iloc[0]
            rec = {k: int(row[k]) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
    except Exception:
        pass
    eps = None
    try:
        et, er = t.eps_trend, t.eps_revisions
        if et is not None and not et.empty:
            eps = {}
            for per, label in (("0y", "올해"), ("+1y", "내년")):
                if per in et.index:
                    cur, ago = et.loc[per, "current"], et.loc[per, "90daysAgo"]
                    eps[per] = {"label": label, "current": float(cur), "ago90": float(ago),
                                "chg_pct": float((cur / ago - 1) * 100) if ago and ago > 0 else None,
                                "up30": int(er.loc[per, "upLast30days"]) if er is not None and per in er.index else None,
                                "down30": int(er.loc[per, "downLast30days"]) if er is not None and per in er.index else None}
    except Exception:
        pass
    price = info.get("currentPrice") or info.get("regularMarketPrice") or targets.get("current")
    return {"source": "Yahoo Finance", "name": info.get("shortName") or info.get("longName"), "price": price, "currency": info.get("currency") or "USD",
            "actions": actions, "official": {"mean": targets.get("mean") or info.get("targetMeanPrice"),
                                              "median": targets.get("median") or info.get("targetMedianPrice"),
                                              "high": targets.get("high") or info.get("targetHighPrice"),
                                              "low": targets.get("low") or info.get("targetLowPrice"),
                                              "analysts": info.get("numberOfAnalystOpinions")},
            "ratings": rec, "eps": eps}


# ------------------------------------------------------------------ KR
def _kr(symbol: str) -> dict:
    code = symbol.split(".")[0]
    rows = []
    with httpx.Client(headers=UA, timeout=15) as c:
        for idx in range(0, 4):  # newest first, 50 per page; stop once past a year
            j = c.get(NAVER, params=[("itemCodes", code), ("size", "50"), ("index", str(idx))]).json()
            items = j.get("items") or []
            rows += items
            if not items or not j.get("hasNext") or items[-1].get("writeDate", "9") < (dt.date.today() - dt.timedelta(days=400)).isoformat():
                break
    actions = []
    for it in rows:
        gp = (it.get("goalPrice") or "").replace(",", "")
        actions.append({"date": pd.Timestamp(it.get("writeDate")), "firm": it.get("brokerName") or "", "title": it.get("title") or "",
                        "summary": _plain(it.get("content")), "pt": float(gp) if gp.replace(".", "").isdigit() and float(gp) > 0 else None,
                        "opinion": (it.get("opinionType") or "").lower(), "opinion_text": it.get("opinionText") or "",
                        "url": f"https://stock.naver.com/research/company/{it.get('nid')}"})
    actions.sort(key=lambda a: a["date"], reverse=True)
    # changes vs the same broker's previous report
    by_firm: dict[str, list[dict]] = {}
    for a in sorted(actions, key=lambda a: a["date"]):
        prev = by_firm.get(a["firm"], [])
        last = prev[-1] if prev else None
        if last is None or a["date"] - last["date"] > pd.Timedelta(days=365):  # first report, or back after a year
            a["pt_action"], a["action"] = "Announces", "init"
        else:
            a["pt_prior"] = last.get("pt")
            a["pt_action"] = ("Raises" if a["pt"] and last.get("pt") and a["pt"] > last["pt"] * 1.001 else
                              "Lowers" if a["pt"] and last.get("pt") and a["pt"] < last["pt"] * 0.999 else "Maintains")
            r_now, r_prev = OPINION_RANK.get(a["opinion"]), OPINION_RANK.get(last.get("opinion", ""))
            a["action"] = "up" if r_now is not None and r_prev is not None and r_now > r_prev else \
                "down" if r_now is not None and r_prev is not None and r_now < r_prev else "main"
        by_firm.setdefault(a["firm"], []).append(a)
    return {"source": "네이버 증권 리서치", "actions": actions}


# ------------------------------------------------------------------ assembly
def _consensus(actions: list[dict], now: pd.Timestamp, days: int = 365) -> dict | None:
    latest: dict[str, dict] = {}
    for a in actions:  # newest first
        if a.get("pt") and a["date"] > now - pd.Timedelta(days=days) and a["firm"] not in latest:
            latest[a["firm"]] = a
    if not latest:
        return None
    pts = np.array([a["pt"] for a in latest.values()])
    return {"mean": float(pts.mean()), "median": float(np.median(pts)), "high": float(pts.max()), "low": float(pts.min()),
            "firms": len(latest), "oldest": min(a["date"] for a in latest.values()).date().isoformat()}


def _counts(actions: list[dict], now: pd.Timestamp) -> dict:
    r90 = [a for a in actions if a["date"] > now - pd.Timedelta(days=90)]
    r30 = [a for a in actions if a["date"] > now - pd.Timedelta(days=30)]
    ups, downs = sum(a.get("pt_action") == "Raises" for a in r90), sum(a.get("pt_action") == "Lowers" for a in r90)
    return {"pt_raises_90": ups, "pt_cuts_90": downs, "breadth_90": (ups - downs) / (ups + downs) if ups + downs >= 3 else None,
            "upgrades_30": sum(a.get("action") == "up" for a in r30), "downgrades_30": sum(a.get("action") == "down" for a in r30),
            "reports_90": len(r90)}


def _match_news(symbol: str, name: str, items: list[dict]) -> None:
    """Attach a headline about each US analyst action: one dated search per action (firm + company, the few days
    around it), keeping a headline only if it names the firm and was published within a day before / 4 days after."""
    from concurrent.futures import ThreadPoolExecutor
    g = GoogleNewsRSS()

    def search(it: dict) -> list:
        d = pd.Timestamp(it["date"])
        firm = sorted(_firm_aliases(it["firm"]), key=len)[-1]
        q = f'{firm} "{name}" after:{(d - pd.Timedelta(days=1)).date()} before:{(d + pd.Timedelta(days=5)).date()}'
        try:
            return g.search(q, limit=20, window="")
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=5) as ex:
        found = list(ex.map(search, items))
    company = {name.lower(), name.split()[0].lower(), symbol.lower()} | set(COMPANY_ALIAS.get(symbol, []))
    for it, heads in zip(items, found):
        al = _firm_aliases(it["firm"])
        d = pd.Timestamp(it["date"])
        best = None
        for h in heads:
            if not h.published_at:
                continue
            p = pd.Timestamp(h.published_at).tz_convert(None) if pd.Timestamp(h.published_at).tzinfo else pd.Timestamp(h.published_at)
            if not (d - pd.Timedelta(days=1) <= p <= d + pd.Timedelta(days=4)):
                continue
            title = h.title.lower()
            if any(re.search(rf"\b{re.escape(a)}\b", title) for a in al) and \
                    any(re.search(rf"\b{re.escape(c)}\b", title) for c in company if len(c) >= 2):
                best = h
                break
        if best:
            it["headline"] = {"title": best.title, "source": best.source, "url": best.url,
                              "published_at": best.published_at.isoformat() if best.published_at else None}


def _money(v: float | None, ccy: str) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}원" if ccy == "KRW" else f"${v:,.2f}"


def _flow(market: str, recent: list[dict], cons: dict | None, counts: dict, price: float | None, ccy: str, eps: dict | None,
          ratings: dict | None) -> str:
    """One paragraph: who said what recently, where the consensus is, and which way the view is moving."""
    if not recent:
        return "최근 1년 안에 나온 애널리스트 리포트 · 의견이 없습니다 (Data unavailable)."
    parts = []
    span = f"{recent[-1]['date'][5:].replace('-', '/')}~{recent[0]['date'][5:].replace('-', '/')}"
    if market == "KR":
        ops = {}
        for r in recent:
            ops[r["opinion_ko"] or "의견 없음"] = ops.get(r["opinion_ko"] or "의견 없음", 0) + 1
        firms = ", ".join(dict.fromkeys(r["firm"] for r in recent))
        parts.append(f"최근 리포트 {len(recent)}건({span}, {firms})의 투자의견은 " + " · ".join(f"{k} {v}건" for k, v in ops.items()) + ".")
        themes = [f"'{r['title']}'" for r in recent[:3] if r.get("title")]
        if themes:
            parts.append("제목으로 본 핵심 논거는 " + ", ".join(themes) + ".")
    else:
        kinds = {}
        for r in recent:
            kinds[r["action_ko"]] = kinds.get(r["action_ko"], 0) + 1
        parts.append(f"최근 의견 {len(recent)}건({span})은 " + " · ".join(f"{k} {v}" for k, v in kinds.items()) + ".")
        moved = [f"{r['firm']} {_money(r.get('pt_prior'), ccy)}→{_money(r.get('pt'), ccy)}" for r in recent
                 if r.get("pt_action") in ("Raises", "Lowers") and r.get("pt_prior") and r.get("pt")][:3]
        if moved:
            parts.append("목표가 변경: " + ", ".join(moved) + ".")
        heads = [f"'{r['headline']['title']}'" for r in recent if r.get("headline")][:2]
        if heads:
            parts.append("관련 기사: " + ", ".join(heads) + ".")
    if cons and price:
        up = (cons["mean"] / price - 1) * 100
        m = _money(cons["mean"], ccy)
        parts.append(f"평균 목표가는 {m}{'으로' if m.endswith('원') else '로'} 현재가 대비 {up:+.0f}% ({cons.get('n_label')}).")
    trend = counts
    if trend["pt_raises_90"] + trend["pt_cuts_90"]:
        direction = ("눈높이가 올라가는 중" if trend["pt_raises_90"] > trend["pt_cuts_90"] * 2 else
                     "눈높이가 내려가는 중" if trend["pt_cuts_90"] > trend["pt_raises_90"] * 2 else "엇갈림")
        parts.append(f"최근 90일 목표가 상향 {trend['pt_raises_90']}건 · 하향 {trend['pt_cuts_90']}건으로 {direction}.")
    if eps and eps.get("+1y", {}).get("chg_pct") is not None:
        e1 = eps["+1y"]["chg_pct"]
        e0 = (eps.get("0y") or {}).get("chg_pct")
        parts.append("EPS 추정치는 90일 전보다 " + (f"올해 {e0:+.1f}%, " if e0 is not None else "") + f"내년 {e1:+.1f}%.")
    if ratings:
        tot = sum(ratings.values())
        if tot:
            buy = ratings["strongBuy"] + ratings["buy"]
            parts.append(f"의견 분포는 매수 {buy} · 보유 {ratings['hold']} · 매도 {ratings['sell'] + ratings['strongSell']}.")
    return " ".join(parts)


_pool = None


def view_quick(symbol: str, timeout: float = 8.0) -> dict | None:
    """view() without headlines, but never wait longer than `timeout` (Yahoo can stall for minutes).
    A slow fetch keeps running in the background and is cached for the next request."""
    global _pool
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutTimeout
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="analyst")
    fut = _pool.submit(view, symbol, with_news=False)
    try:
        return fut.result(timeout=timeout)
    except FutTimeout:
        log.info("analyst view %s still loading after %.0fs - skipped this time", symbol, timeout)
        return None


def view(symbol: str, *, with_news: bool = True) -> dict:
    """Everything the stock page / opinions need. Cached 6 hours (news matching separately)."""
    core = cached(f"analyst:{symbol}", 6 * 3600, lambda: _build(symbol))
    if not with_news or core.get("market") != "US" or not core.get("recent"):
        return core
    return cached(f"analyst-news:{symbol}", 6 * 3600, lambda: _with_news(symbol, core))


def _with_news(symbol: str, core: dict) -> dict:
    out = {**core, "recent": [dict(r) for r in core["recent"]]}
    try:
        _match_news(symbol, _short_name(core.get("name"), symbol), out["recent"])
    except Exception as e:
        log.info("analyst news %s: %s", symbol, e)
    out["flow"] = _flow("US", out["recent"], out.get("consensus"), out["counts"], out.get("price"), out["currency"], out.get("eps"),
                        out.get("ratings"))
    return out


def _build(symbol: str) -> dict:
    market = market_of(symbol)
    now = pd.Timestamp(dt.datetime.now())
    try:
        raw = _kr(symbol) if market == "KR" else _us(symbol)
    except Exception as e:
        log.info("analyst view %s failed: %s", symbol, e)
        return {"symbol": symbol, "market": market, "available": False, "message": "애널리스트 데이터를 불러올 수 없습니다 (Data unavailable)"}
    actions = raw["actions"]
    price = raw.get("price")
    ccy = raw.get("currency") or ("KRW" if market == "KR" else "USD")
    if price is None:
        try:
            from . import prices
            price = float(prices.get_quote(symbol).price)
        except Exception:
            price = None
    cons = _consensus(actions, now)
    if market == "KR":  # rating spread = each broker's latest opinion within a year
        latest: dict[str, str] = {}
        for x in actions:
            if x["date"] > now - pd.Timedelta(days=365) and x["firm"] not in latest:
                latest[x["firm"]] = x.get("opinion") or ""
        rank = [OPINION_RANK.get(o) for o in latest.values() if OPINION_RANK.get(o) is not None]
        raw["ratings"] = {"strongBuy": sum(r == 3 for r in rank), "buy": sum(r == 2 for r in rank), "hold": sum(r == 1 for r in rank),
                          "sell": sum(r == 0 for r in rank), "strongSell": 0} if rank else None
    official = raw.get("official")
    if official and official.get("mean"):
        headline = {**official, "n_label": f"애널리스트 {official.get('analysts') or '?'}명 · Yahoo 집계", "basis": "Yahoo"}
    elif cons:
        headline = {**cons, "analysts": cons["firms"], "n_label": f"최근 1년 증권사 {cons['firms']}곳의 최신 목표가", "basis": "reports"}
    else:
        headline = None
    counts = _counts(actions, now)
    recent = []
    seen = set()
    for a in actions:
        key = (a["firm"], a["date"].date())
        if key in seen:
            continue
        seen.add(key)
        r = {"date": a["date"].date().isoformat(), "firm": a["firm"], "pt": a.get("pt"), "pt_prior": a.get("pt_prior"),
             "pt_action": a.get("pt_action"), "pt_action_ko": PT_KO.get(a.get("pt_action") or "", ""),
             "action": a.get("action"), "action_ko": ACTION_KO.get(a.get("action") or "", a.get("action") or "")}
        if market == "KR":
            r.update(title=a.get("title"), summary=a.get("summary"), url=a.get("url"),
                     opinion_ko=OPINION_KO.get(a.get("opinion") or "", a.get("opinion_text") or ""))
        else:
            r.update(grade=_grade(a.get("to")), grade_from=_grade(a.get("from")))
        recent.append(r)
        if len(recent) >= 5:
            break
    upside = (headline["mean"] / price - 1) * 100 if headline and price else None
    signals = {"upside": (cons["mean"] / price - 1) if cons and cons["firms"] >= 3 and price else None,
               "breadth": counts["breadth_90"], "net_rating": counts["upgrades_30"] - counts["downgrades_30"]}
    micro = micro_score(signals, market)
    out = {"symbol": symbol, "market": market, "available": bool(actions), "source": raw["source"], "name": raw.get("name"),
           "price": price, "currency": ccy, "consensus": headline, "reports_consensus": cons, "upside_pct": upside,
           "ratings": raw.get("ratings"), "eps": raw.get("eps"), "counts": counts, "recent": recent, "signals": signals,
           "micro": micro, "updated_at": utcnow().isoformat()}
    out["flow"] = _flow(market, recent, headline, counts, price, ccy, raw.get("eps"), raw.get("ratings"))
    return clean(out)
