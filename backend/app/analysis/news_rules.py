"""Transparent rule-based news classifier and de-duplication.

Used when no AI key is configured (and as a fallback when an AI call fails). Every
output carries method="rules" and a rationale listing the matched keywords, so the
UI never presents keyword matching as AI analysis.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from ..i18n_ko import CATEGORY_KO, EVENT_KO

STOP = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "as", "at", "by", "with", "is", "are", "its",
        "from", "after", "amid", "over", "into", "says", "say", "said", "new", "this", "that", "be", "will", "what",
        "how", "why", "it", "us", "s", "vs", "than", "more", "up", "down"}

TIER1 = {"reuters", "bloomberg", "the wall street journal", "wsj", "financial times", "ft", "associated press", "ap",
         "cnbc", "barron's", "the economist", "nikkei asia", "yonhap", "연합뉴스", "한국경제", "매일경제", "sec edgar",
         "federal reserve", "marketwatch", "yahoo finance", "investing.com", "the new york times", "bbc"}
TIER2_HINT = ("news", "times", "post", "journal", "herald", "daily", "경제", "일보", "뉴스")

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("SEC Filing", [r"\b(8-k|10-k|10-q|6-k|20-f|s-1)\b"]),
    ("Fed", [r"\bfed\b", r"federal reserve", r"\bfomc\b", r"powell", r"rate (cut|hike)", r"interest rates?", r"연준", r"금리"]),
    ("Inflation", [r"inflation", r"\bcpi\b", r"\bpce\b", r"consumer prices", r"물가"]),
    ("Treasury", [r"treasur", r"bond (yield|market)", r"\byields?\b", r"국채"]),
    ("Earnings", [r"earnings", r"quarterly (results|profit|revenue)", r"\beps\b", r"results beat", r"실적", r"영업이익"]),
    ("Guidance", [r"guidance", r"outlook", r"forecast(s)? (raised|cut)", r"전망"]),
    ("Analyst Action", [r"upgrade", r"downgrade", r"price target", r"initiat(es|ed) coverage", r"overweight", r"underweight", r"목표주가"]),
    ("M&A", [r"acquir", r"merger", r"takeover", r"buyout", r"\bdeal to buy\b", r"인수", r"합병"]),
    ("Partnership", [r"partner", r"collaborat", r"teams up", r"agreement with", r"협력", r"제휴"]),
    ("Product", [r"launch", r"unveil", r"introduc", r"new (chip|model|product)", r"출시", r"공개"]),
    ("Regulation/Legal", [r"lawsuit", r"\bsues?\b", r"probe", r"investigation", r"antitrust", r"regulator", r"\bfine\b",
                          r"export (control|ban|curb)", r"소송", r"규제"]),
    ("Oil", [r"\boil\b", r"crude", r"opec", r"brent", r"\bwti\b", r"유가"]),
    ("Semiconductors", [r"semiconductor", r"chip", r"\bhbm\b", r"foundry", r"tsmc", r"반도체", r"하이닉스"]),
    ("AI", [r"\bai\b", r"artificial intelligence", r"data center", r"\bgpu", r"openai", r"인공지능"]),
    ("Geopolitics", [r"\bwar\b", r"sanction", r"tariff", r"geopolit", r"missile", r"conflict", r"ukraine", r"israel",
                     r"iran", r"taiwan strait", r"관세", r"전쟁"]),
    ("China", [r"china", r"chinese", r"beijing", r"\byuan\b", r"pboc", r"중국"]),
    ("Japan", [r"japan", r"\bboj\b", r"bank of japan", r"\byen\b", r"nikkei", r"일본", r"엔화"]),
    ("Korea", [r"korea", r"kospi", r"kosdaq", r"\bwon\b", r"bank of korea", r"코스피", r"코스닥", r"한국은행", r"환율"]),
    ("Europe", [r"\becb\b", r"euro(zone| area)", r"lagarde", r"europe", r"유럽"]),
    ("Economic Data", [r"jobs report", r"payroll", r"unemployment", r"\bgdp\b", r"retail sales", r"\bpmi\b", r"\bism\b",
                       r"jobless claims", r"housing starts", r"consumer (confidence|sentiment)", r"고용"]),
    ("Crypto", [r"bitcoin", r"crypto", r"ethereum", r"비트코인"]),
]
EVENT_RULES: list[tuple[str, str]] = [
    ("earnings_beat", r"(beat|tops|top|exceed|surpass)\w* (estimates|expectations|forecasts)|record (revenue|profit)|어닝 서프라이즈"),
    ("earnings_miss", r"miss\w* (estimates|expectations)|falls? short|어닝 쇼크"),
    ("guidance_raise", r"raises? (guidance|outlook|forecast)|lifts? (outlook|forecast)|boosts? (guidance|outlook)"),
    ("guidance_cut", r"(cuts?|lowers?|slashes?) (guidance|outlook|forecast)|warns"),
    ("analyst_upgrade", r"upgrade"), ("analyst_downgrade", r"downgrade"),
    ("price_target_change", r"price target|목표주가"),
    ("acquisition", r"acquir|merger|takeover|인수"), ("lawsuit", r"lawsuit|\bsues?\b|소송"),
    ("regulatory_action", r"probe|investigation|antitrust|fine[ds]?\b|export (control|ban|curb)"),
    ("central_bank_signal", r"\bfed\b|fomc|powell|ecb|boj|bank of (japan|korea|england)|연준|한국은행"),
    ("macro_release", r"\bcpi\b|\bpce\b|jobs report|payrolls|\bgdp\b|retail sales|\bpmi\b|jobless claims"),
    ("product_launch", r"launch|unveil|출시"), ("partnership", r"partner|collaborat|제휴"),
    ("filing", r"\b(8-k|10-k|10-q|6-k|20-f)\b"),
    ("opinion", r"\b(should you|is it time|\d+ (stocks?|reasons)|here's why|buy now|prediction|could soar|millionaire|best .* to buy)\b"),
]
POS = ["beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies", "record", "upgrade",
       "upgraded", "raises", "raised", "strong", "stronger", "growth", "gain", "gains", "rise", "rises", "rebound",
       "approval", "approved", "wins", "win", "buyback", "outperform", "bullish", "boost", "boosts", "optimism", "cut rates",
       "rate cut", "rate cuts", "easing", "cools", "cooling", "eases", "eased", "tops", "climbs", "higher", "expands",
       "상승", "급등", "호실적", "최고", "개선", "흑자", "증가", "돌파"]
NEG = ["miss", "misses", "plunge", "plunges", "tumble", "tumbles", "slump", "slumps", "fall", "falls", "drop", "drops",
       "downgrade", "downgraded", "lawsuit", "probe", "investigation", "recall", "warns", "warning", "weak", "weaker",
       "layoffs", "bearish", "slows", "slowdown", "selloff", "sell-off", "crash", "default", "sanctions", "tariff",
       "tariffs", "war", "attack", "hike", "hikes", "hawkish", "sticky", "hotter", "fears", "concern", "concerns", "risk",
       "lower", "loss", "losses", "decline", "declines", "cuts", "slashes", "halt", "bankrupt", "fraud",
       "하락", "급락", "부진", "적자", "감소", "우려", "쇼크", "제재"]
CHAIN_TEMPLATES: dict[str, list[dict]] = {
    "Fed_hawk": [{"node": "연준", "type": "macro", "direction": "up"}, {"node": "국채금리", "type": "macro", "direction": "up"},
                 {"node": "달러", "type": "asset", "direction": "up"}, {"node": "성장주", "type": "asset", "direction": "down"},
                 {"node": "기술주", "type": "sector", "direction": "down"}],
    "Fed_dove": [{"node": "연준", "type": "macro", "direction": "down"}, {"node": "국채금리", "type": "macro", "direction": "down"},
                 {"node": "달러", "type": "asset", "direction": "down"}, {"node": "성장주", "type": "asset", "direction": "up"},
                 {"node": "기술주", "type": "sector", "direction": "up"}],
    "Inflation_up": [{"node": "인플레이션", "type": "macro", "direction": "up"}, {"node": "금리인하 기대", "type": "macro", "direction": "down"},
                     {"node": "국채금리", "type": "macro", "direction": "up"}, {"node": "주식", "type": "asset", "direction": "down"}],
    "Inflation_down": [{"node": "인플레이션", "type": "macro", "direction": "down"}, {"node": "금리인하 기대", "type": "macro", "direction": "up"},
                       {"node": "국채금리", "type": "macro", "direction": "down"}, {"node": "주식", "type": "asset", "direction": "up"}],
    "Oil_up": [{"node": "유가", "type": "asset", "direction": "up"}, {"node": "기대인플레이션", "type": "macro", "direction": "up"},
               {"node": "에너지", "type": "sector", "direction": "up"}, {"node": "항공·소비재", "type": "sector", "direction": "down"}],
    "Oil_down": [{"node": "유가", "type": "asset", "direction": "down"}, {"node": "기대인플레이션", "type": "macro", "direction": "down"},
                 {"node": "에너지", "type": "sector", "direction": "down"}, {"node": "소비재", "type": "sector", "direction": "up"}],
    "AI_up": [{"node": "AI 투자", "type": "macro", "direction": "up"}, {"node": "반도체", "type": "sector", "direction": "up"},
              {"node": "NVDA / AMD / AVGO", "type": "stock", "direction": "up"}, {"node": "SK하이닉스 / 삼성전자 (HBM)", "type": "stock", "direction": "up"}],
    "Geo_risk": [{"node": "지정학 리스크", "type": "macro", "direction": "up"}, {"node": "유가·금", "type": "asset", "direction": "up"},
                 {"node": "주식", "type": "asset", "direction": "down"}],
    "Japan_tight": [{"node": "일본은행 긴축", "type": "macro", "direction": "up"}, {"node": "엔화", "type": "asset", "direction": "up"},
                    {"node": "엔 캐리 트레이드", "type": "macro", "direction": "down"}, {"node": "아시아 증시", "type": "asset", "direction": "down"}],
}
SECTOR_OF_CATEGORY = {"Semiconductors": ["Semiconductors"], "AI": ["Semiconductors", "Software"], "Oil": ["Energy"],
                      "Fed": ["Financials", "Technology"], "Treasury": ["Financials", "Real Estate"],
                      "Crypto": ["Crypto"], "Korea": ["Korea"], "China": ["Materials", "Consumer Discretionary"]}


def normalize_title(t: str) -> str:
    t = re.sub(r"\s+-\s+[^-]{2,60}$", "", t or "")  # trailing " - Source"
    t = t.lower()
    t = re.sub(r"[^\w\s%$.]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def fingerprint(title: str, published_at: dt.datetime | None = None, url: str | None = None,
                filing: bool = False) -> str:
    """Article identity: normalised title + publication day (+ URL for filings, whose titles repeat)."""
    day = published_at.strftime("%Y-%m-%d") if published_at else ""
    key = f"{normalize_title(title)}|{day}" + (f"|{url}" if filing and url else "")
    return hashlib.sha1(key.encode()).hexdigest()


def tokens(title: str) -> set[str]:
    return {w for w in normalize_title(title).split() if w not in STOP and len(w) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def cluster(items: list[dict], threshold: float = 0.5, window_hours: float = 48) -> list[dict]:
    """Group near-duplicate headlines. Keeps the earliest article as the primary; others become `also`."""
    items = sorted(items, key=lambda x: x.get("published_at") or dt.datetime.max.replace(tzinfo=dt.timezone.utc))
    clusters: list[dict] = []
    for it in items:
        tk = tokens(it["title"])
        is_filing = it.get("scope") == "filing"
        fp = fingerprint(it["title"], it.get("published_at"), it.get("url"), filing=is_filing)
        placed = False
        for c in clusters:
            if (c.get("scope") == "filing") != is_filing:
                continue
            if fp == c["fingerprint"] or (not is_filing and jaccard(tk, c["_tokens"]) >= threshold):
                ta, tb = it.get("published_at"), c.get("published_at")
                if ta and tb and abs((ta - tb).total_seconds()) > window_hours * 3600:
                    continue
                if it.get("source") and it["source"] not in c["sources"]:
                    c["sources"].append(it["source"])
                    c["also"].append({"title": it["title"], "source": it.get("source"), "url": it.get("url"),
                                      "published_at": it.get("published_at")})
                c["tickers"] = sorted(set(c["tickers"]) | set(it.get("tickers") or []))
                placed = True
                break
        if not placed:
            clusters.append({**it, "fingerprint": fp, "_tokens": tk, "sources": [it.get("source")] if it.get("source") else [],
                             "also": [], "tickers": list(it.get("tickers") or [])})
    for c in clusters:
        c.pop("_tokens", None)
        c["cluster_size"] = 1 + len(c["also"])
    return clusters


def source_tier(source: str | None) -> int:
    s = (source or "").lower()
    if any(t == s or t in s for t in TIER1):
        return 1
    if any(h in s for h in TIER2_HINT):
        return 2
    return 3


def classify(item: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    text = f"{item['title']} {item.get('summary') or ''}"
    low = text.lower()
    cat = item.get("category")
    if not cat or cat == "filing":
        cat = next((c for c, pats in CATEGORY_RULES if any(re.search(p, low) for p in pats)), "Other")
    events = [e for e, p in EVENT_RULES if re.search(p, low)]
    _rank = {"guidance_raise": 3, "guidance_cut": 3, "central_bank_signal": 3, "macro_release": 3}
    event = "opinion" if "opinion" in events else (max(events, key=lambda e: _rank.get(e, 2 if "earnings" in e else 1))
                                                    if events else "other")
    words = re.findall(r"[\w\-']+", low)
    pos = [w for w in POS if (" " in w and w in low) or (" " not in w and w in words)]
    neg = [w for w in NEG if (" " in w and w in low) or (" " not in w and w in words)]
    raw = len(pos) - len(neg)
    # Macro-direction overrides: what matters is the effect on risk assets.
    up_w = r"(rise|rises|rising|rose|jump|jumps|climb|climbs|surge|surges|soar|soars|higher|spike|spikes|accelerat\w*|heats?)"
    dn_w = r"(fall|falls|fell|drop|drops|slide|slides|decline|declines|lower|ease|eases|eased|retreat\w*|cool\w*|slow\w*|soft\w*|moderat\w*)"
    subj = r"(yields?|inflation|cpi|pce|prices)"
    softened = re.search(r"(eas\w*|dims?|cool\w*|reduc\w*|lower\w*|tempers?)\s+(\w+\s+){0,4}(rate[- ]hikes?|hikes?|tightening)", low)
    cut_doubt = re.search(r"(dents?|dims?|fades?|trims?|pares?|reduc\w*|scales? back|lower\w*|dashes?)\s+(\w+\s+){0,2}rate[- ]cuts?", low)
    hot_after = re.search(rf"{subj}(\s+\w+){{0,4}}\s+(hotter|higher|above|stronger)", low)
    cool_after = re.search(rf"{subj}(\s+\w+){{0,4}}\s+(cooler|softer|below|weaker)", low)
    hawk = bool(cut_doubt or hot_after or not softened and (
        re.search(r"rate[- ]hikes?|hawkish|higher for longer|slower (rate )?cuts|fewer cuts|sticky|tightening", low)
        or re.search(rf"{subj}(\s+\w+){{0,2}}\s+{up_w}", low) or re.search(rf"(hotter|higher|sticky)\s+(\w+\s+)?{subj}", low)))
    dove = bool(not cut_doubt and not hot_after) and bool(
        softened or cool_after or re.search(r"rate cuts?|dovish|pause|easing", low)
                or re.search(rf"{subj}(\s+\w+){{0,2}}\s+{dn_w}", low) or re.search(rf"(softer|cooler|lower)\s+(\w+\s+)?{subj}", low))
    if cat in ("Fed", "Inflation", "Treasury"):
        # For rates/inflation headlines the equity-relevant direction replaces generic word counting.
        raw = (2 if dove else 0) - (2 if hawk else 0)
    if event in ("earnings_beat", "guidance_raise", "analyst_upgrade"):
        raw += 2
    if event in ("earnings_miss", "guidance_cut", "analyst_downgrade", "lawsuit", "regulatory_action"):
        raw -= 2
    sentiment = ("Very Bullish" if raw >= 3 else "Bullish" if raw >= 1 else "Very Bearish" if raw <= -3 else
                 "Bearish" if raw <= -1 else "Neutral")
    if event == "opinion":
        sentiment = "Neutral"
    base = {"central_bank_signal": 3, "macro_release": 3, "earnings_beat": 2, "earnings_miss": 2, "guidance_raise": 3,
            "guidance_cut": 3, "acquisition": 2, "regulatory_action": 2, "analyst_upgrade": 1, "analyst_downgrade": 1,
            "price_target_change": 1, "lawsuit": 1, "product_launch": 1, "partnership": 1, "filing": 1, "opinion": 0,
            "other": 1}.get(event, 1)
    if cat in ("Geopolitics",) and re.search(r"attack|war|missile|invasion|strike", low):
        base = max(base, 3)
    size = item.get("cluster_size", 1)
    if size >= 3:
        base += 1
    tier = source_tier(item.get("source"))
    if event == "central_bank_signal" and not re.search(
            r"decision|minutes|statement|signal|hike|cut|hold|pause|warns|says|testimony|speech|holds|raises|lowers", low):
        base = 2
    if event == "macro_release" and re.search(
            r"\b(uk|britain|south africa|india|brazil|australia|canada|mexico|turkey|indonesia|philippines|nigeria)\b", low):
        base = 2
    base = max(0, min(4, base))
    impact = ["Low", "Low", "Medium", "High", "Critical"][base]
    importance = [15, 30, 50, 72, 90][base] + {1: 8, 2: 3, 3: 0}[tier] + min(12, 4 * (size - 1))
    pub = item.get("published_at")
    if pub:
        hours = max(0.0, (now - pub).total_seconds() / 3600)
        importance -= min(20, hours / 3)
    if event == "opinion":
        importance -= 15
    importance = int(max(0, min(100, importance)))
    conf = 0.35 + 0.08 * min(3, abs(raw)) + (0.05 if tier == 1 else 0)
    chain_key = None
    if cat == "Fed" or (cat in ("Treasury",) and (hawk or dove)):
        chain_key = "Fed_hawk" if (hawk or raw < 0) else "Fed_dove" if (dove or raw > 0) else None
    elif cat == "Inflation":
        chain_key = "Inflation_up" if (hawk or raw < 0) else "Inflation_down" if raw > 0 else None
    elif cat == "Oil":
        chain_key = "Oil_up" if re.search(r"rise|jump|surge|soar|climb|higher|rall", low) else \
            "Oil_down" if re.search(r"fall|drop|slump|tumble|lower|plunge", low) else None
    elif cat in ("AI", "Semiconductors") and raw > 0:
        chain_key = "AI_up"
    elif cat == "Geopolitics" and raw < 0:
        chain_key = "Geo_risk"
    elif cat == "Japan" and re.search(r"hike|tighten|hawk", low):
        chain_key = "Japan_tight"
    chain = list(CHAIN_TEMPLATES.get(chain_key, [])) if chain_key else []
    tickers = list(item.get("tickers") or [])
    if not chain and tickers and cat not in ("Other", "SEC Filing") and raw != 0:
        d = "up" if raw > 0 else "down" if raw < 0 else "mixed"
        chain = [{"node": CATEGORY_KO.get(cat, cat), "type": "macro" if cat in ("Fed", "Inflation", "Treasury", "Oil") else "sector",
                  "direction": d}] + \
                [{"node": t, "type": "stock", "direction": d} for t in tickers[:3]]
    matched = (pos + neg)[:6]
    rationale = (f"규칙 기반: 분류 '{CATEGORY_KO.get(cat, cat)}', 이벤트 '{EVENT_KO.get(event, event)}', "
                 f"{'키워드 ' + ', '.join(matched) if matched else '감성 키워드 없음'}; "
                 f"출처 등급 {tier}; {size}개 매체 보도.")
    return {"category": cat, "event_type": event, "sentiment": sentiment, "sentiment_score": max(-1.0, min(1.0, raw / 3)),
            "impact": impact, "importance": importance, "confidence": round(min(0.7, conf), 2),
            "tickers": tickers, "sectors": SECTOR_OF_CATEGORY.get(cat, []), "chain": chain, "rationale": rationale,
            "method": "rules", "model": None}


SENTIMENT_SCORE = {"Very Bullish": 1.0, "Bullish": 0.5, "Neutral": 0.0, "Bearish": -0.5, "Very Bearish": -1.0}
IMPACT_RANK = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
