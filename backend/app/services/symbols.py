"""Symbol master + bilingual search (English / 한국어 / KRX code / Yahoo symbol).

Sources: SEC company_tickers_exchange.json (US listings), KRX KIND (KOSPI/KOSDAQ),
Yahoo screener (English names of large Korean caps) and a small reference table of
Korean names for popular US stocks and indices. Unknown queries fall back to Yahoo search.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Stock
from ..providers.base import SymbolMatch
from ..providers.krx import krx
from ..providers.news import sec
from ..providers.yahoo import yahoo
from .store import upsert

log = logging.getLogger(__name__)

# Korean names / nicknames for popular US listings (reference data used only for search).
KO_ALIASES: dict[str, list[str]] = {
    "NVDA": ["엔비디아"], "AAPL": ["애플"], "MSFT": ["마이크로소프트", "마소"], "TSLA": ["테슬라"],
    "AMZN": ["아마존"], "GOOGL": ["알파벳", "구글"], "GOOG": ["알파벳C"], "META": ["메타", "페이스북"],
    "AVGO": ["브로드컴"], "AMD": ["에이엠디"], "NFLX": ["넷플릭스"], "PLTR": ["팔란티어"], "TSM": ["TSMC", "대만반도체"],
    "INTC": ["인텔"], "MU": ["마이크론"], "QCOM": ["퀄컴"], "ARM": ["암홀딩스"], "ASML": ["에이에스엠엘"],
    "ORCL": ["오라클"], "CRM": ["세일즈포스"], "ADBE": ["어도비"], "COST": ["코스트코"], "WMT": ["월마트"],
    "KO": ["코카콜라"], "PEP": ["펩시"], "JPM": ["제이피모건"], "BAC": ["뱅크오브아메리카"], "V": ["비자"],
    "MA": ["마스터카드"], "BRK-B": ["버크셔해서웨이", "버크셔"], "LLY": ["일라이릴리", "릴리"], "NVO": ["노보노디스크"],
    "UNH": ["유나이티드헬스"], "JNJ": ["존슨앤존슨"], "PFE": ["화이자"], "XOM": ["엑슨모빌"], "CVX": ["쉐브론"],
    "DIS": ["디즈니"], "NKE": ["나이키"], "SBUX": ["스타벅스"], "MCD": ["맥도날드"], "BA": ["보잉"],
    "IONQ": ["아이온큐"], "SMCI": ["슈퍼마이크로"], "COIN": ["코인베이스"], "MSTR": ["마이크로스트래티지"],
    "UBER": ["우버"], "SHOP": ["쇼피파이"], "SPY": ["S&P500 ETF"], "QQQ": ["나스닥100 ETF"], "SOXX": ["반도체 ETF"],
    "SOXL": ["반도체3배"], "TQQQ": ["나스닥3배"], "ARKK": ["아크"], "CRWD": ["크라우드스트라이크"],
    "PANW": ["팔로알토"], "SNOW": ["스노우플레이크"], "RIVN": ["리비안"], "LCID": ["루시드"],
    "^GSPC": ["S&P500", "에스앤피"], "^NDX": ["나스닥100"], "^IXIC": ["나스닥"], "^DJI": ["다우", "다우존스"],
    "^RUT": ["러셀2000"], "^VIX": ["빅스", "공포지수"], "^KS11": ["코스피"], "^KQ11": ["코스닥"],
    "^N225": ["니케이", "닛케이"], "^HSI": ["항셍"], "^STOXX50E": ["유로스톡스"],
}
STATIC: list[dict] = [
    {"symbol": "^GSPC", "name": "S&P 500", "market": "INDEX"}, {"symbol": "^NDX", "name": "Nasdaq 100", "market": "INDEX"},
    {"symbol": "^IXIC", "name": "Nasdaq Composite", "market": "INDEX"},
    {"symbol": "^DJI", "name": "Dow Jones Industrial Average", "market": "INDEX"},
    {"symbol": "^RUT", "name": "Russell 2000", "market": "INDEX"}, {"symbol": "^VIX", "name": "CBOE Volatility Index", "market": "INDEX"},
    {"symbol": "^KS11", "name": "KOSPI Composite", "name_ko": "코스피", "market": "INDEX"},
    {"symbol": "^KQ11", "name": "KOSDAQ Composite", "name_ko": "코스닥", "market": "INDEX"},
    {"symbol": "^STOXX50E", "name": "Euro Stoxx 50", "market": "INDEX"}, {"symbol": "^N225", "name": "Nikkei 225", "market": "INDEX"},
    {"symbol": "^HSI", "name": "Hang Seng", "market": "INDEX"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "market": "US"}, {"symbol": "QQQ", "name": "Invesco QQQ Trust", "market": "US"},
    {"symbol": "BTC-USD", "name": "Bitcoin USD", "name_ko": "비트코인", "market": "CRYPTO"},
    {"symbol": "ETH-USD", "name": "Ethereum USD", "name_ko": "이더리움", "market": "CRYPTO"},
    {"symbol": "GC=F", "name": "Gold Futures", "name_ko": "금 선물", "market": "FUT"},
    {"symbol": "CL=F", "name": "WTI Crude Oil Futures", "name_ko": "WTI 원유", "market": "FUT"},
    {"symbol": "KRW=X", "name": "USD/KRW", "name_ko": "원달러 환율", "market": "FX"},
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\s\.\,\(\)\-_/&']", "", s)


class SymbolIndex:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self.popular: set[str] = set()

    # ------------------------------------------------------------- loading
    def ensure_loaded(self) -> None:
        if self._rows:
            return
        with self._lock:
            if self._rows:
                return
            with SessionLocal() as db:
                rows = db.execute(select(Stock)).scalars().all()
            if rows:
                for r in rows:
                    self._add({"symbol": r.symbol, "name": r.name, "name_ko": r.name_ko, "market": r.market,
                               "exchange": r.exchange, "kr_code": r.kr_code, "aliases": r.aliases or [],
                               "sector": r.sector, "industry": r.industry})
                self._loaded_at = time.time()
                log.info("symbol index loaded from DB: %d", len(self._rows))
            else:
                self._refresh_locked()

    def refresh(self) -> int:
        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> int:
        rows: list[dict] = []
        try:
            for t, info in sec.ticker_map().items():
                rows.append({"symbol": t.replace(".", "-"), "name": info["name"], "market": "US",
                             "exchange": info.get("exchange"), "cik": str(info["cik"])})
        except Exception as e:
            log.warning("SEC symbol list unavailable: %s", e)
        try:
            for r in krx.listings():
                rows.append({"symbol": r["symbol"], "name": r["name_ko"], "name_ko": r["name_ko"], "market": "KR",
                             "exchange": r["market_label"], "kr_code": r["kr_code"], "industry": r["industry_ko"] or None})
        except Exception as e:
            log.warning("KRX listing unavailable: %s", e)
        try:  # English names for the largest Korean companies
            en = {}
            for exch in ("KSC", "KOE"):
                for q in yahoo.screen_kr(exch, size=150 if exch == "KSC" else 100):
                    en[q["symbol"]] = q["name"]
            for r in rows:
                if r["symbol"] in en:
                    r["name"] = en[r["symbol"]]
                    r.setdefault("aliases", []).append(en[r["symbol"]])
        except Exception as e:
            log.info("Yahoo KR names unavailable: %s", e)
        rows += [dict(s) for s in STATIC]
        if len(rows) < 100:
            log.warning("symbol refresh produced too few rows (%d); keeping previous index", len(rows))
            return 0
        for r in rows:
            r["aliases"] = list(dict.fromkeys((r.get("aliases") or []) + KO_ALIASES.get(r["symbol"], [])))
        self._rows = {}
        for r in rows:
            self._add(r)
        self._loaded_at = time.time()
        try:
            with SessionLocal() as db:
                upsert(db, Stock, [{"symbol": r["symbol"], "name": r["name"][:255], "name_ko": r.get("name_ko"),
                                    "market": r["market"], "exchange": r.get("exchange"), "kr_code": r.get("kr_code"),
                                    "cik": r.get("cik"), "aliases": r["aliases"], "industry": r.get("industry"),
                                    "source": "sec/krx/yahoo"} for r in rows], ["symbol"])
                db.commit()
        except Exception as e:
            log.warning("symbol persist failed: %s", e)
        log.info("symbol index refreshed: %d", len(self._rows))
        return len(self._rows)

    def _add(self, r: dict) -> None:
        r["_sym"] = _norm(r["symbol"])
        r["_name"] = _norm(r.get("name") or "")
        r["_ko"] = _norm(r.get("name_ko") or "")
        r["_aliases"] = [_norm(a) for a in r.get("aliases") or []]
        self._rows[r["symbol"]] = r

    # -------------------------------------------------------------- lookup
    def get(self, symbol: str) -> dict | None:
        self.ensure_loaded()
        return self._rows.get(symbol)

    def resolve(self, query: str) -> str | None:
        """Best-effort canonical Yahoo symbol for a user query (005930 -> 005930.KS, NVDA.US -> NVDA)."""
        q = (query or "").strip()
        if not q:
            return None
        u = q.upper()
        if u.endswith(".US"):
            u = u[:-3]
        u = u.replace(".", "-") if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", u) else u
        self.ensure_loaded()
        if u in self._rows:
            return u
        if re.fullmatch(r"\d{6}", u):
            for suf in (".KS", ".KQ"):
                if f"{u}{suf}" in self._rows:
                    return f"{u}{suf}"
            return f"{u}.KS"
        hits = self.search(q, limit=1, remote=False)
        if hits and hits[0].score >= 90:
            return hits[0].symbol
        return u if re.fullmatch(r"[\^A-Z0-9\.\-=]{1,20}", u) else None

    def search(self, query: str, limit: int = 10, remote: bool = True) -> list[SymbolMatch]:
        self.ensure_loaded()
        q = (query or "").strip()
        if not q:
            return []
        nq = _norm(q.removesuffix(".US").removesuffix(".us"))
        is_code = bool(re.fullmatch(r"\d{6}", nq))
        scored: list[tuple[float, dict]] = []
        for r in self._rows.values():
            s = 0.0
            if r["_sym"] == nq or (is_code and r.get("kr_code") == nq):
                s = 100
            elif nq in r["_aliases"] or (r["_ko"] and r["_ko"] == nq):
                s = 96
            elif r["_ko"] and r["_ko"].startswith(nq):
                s = 86 - min(10, len(r["_ko"]) - len(nq))
            elif r["_sym"].startswith(nq) and len(nq) >= 1:
                s = 82 - min(20, 3 * (len(r["_sym"]) - len(nq)))
            elif any(a.startswith(nq) for a in r["_aliases"]) and len(nq) >= 2:
                s = 80
            elif r["_ko"] and nq in r["_ko"] and len(nq) >= 2:
                s = 72
            elif r["_name"].startswith(nq) and len(nq) >= 2:
                s = 70
            elif len(nq) >= 3 and nq in r["_name"]:
                s = 55
            if s <= 0:
                continue
            if r["symbol"] in self.popular:
                s += 8
            if r.get("exchange") in ("OTC", None) and r.get("market") == "US":
                s -= 12
            if r.get("market") == "INDEX":
                s += 4
            scored.append((s, r))
        scored.sort(key=lambda x: (-x[0], len(x[1]["symbol"])))
        out = [SymbolMatch(symbol=r["symbol"], name=r.get("name") or r["symbol"], name_ko=r.get("name_ko"),
                           exchange=r.get("exchange"), market=r.get("market"), score=s) for s, r in scored[:limit]]
        if remote and (not out or out[0].score < 60):
            seen = {o.symbol for o in out}
            for m in yahoo.search(q, limit=limit):
                if m.symbol not in seen:
                    m.score = 50
                    out.append(m)
        return out[:limit]


index = SymbolIndex()
