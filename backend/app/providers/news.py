"""News providers.

* Google News RSS  - free, no key; EN + KO queries (headline, source, publish time).
* Yahoo Finance    - headline search with related tickers (see yahoo.py).
* SEC EDGAR        - official filings (8-K/10-Q/10-K...) via data.sec.gov submissions API;
                     requires a descriptive User-Agent with contact e-mail, max 10 req/s.
* Finnhub          - optional (FINNHUB_API_KEY): /company-news and /news?category=general.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
import re
import threading
import time
from urllib.parse import quote_plus

import feedparser
import httpx

from ..config import APP_USER_AGENT, get_settings
from .base import NewsItem, ProviderError, health

log = logging.getLogger(__name__)


class GoogleNewsRSS:
    name = "google_news"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0, follow_redirects=True, headers={"User-Agent": APP_USER_AGENT})

    def search(self, query: str, *, language: str = "en", limit: int = 30, window: str = "3d") -> list[NewsItem]:
        if language == "ko":
            loc = "hl=ko&gl=KR&ceid=KR:ko"
        else:
            loc = "hl=en-US&gl=US&ceid=US:en"
        q = f"{query} when:{window}" if window else query
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&{loc}"
        try:
            r = self._client.get(url)
            r.raise_for_status()
        except Exception as e:
            health.fail(self.name, e)
            raise ProviderError(f"google news: {e}") from e
        feed = feedparser.parse(r.text)
        out: list[NewsItem] = []
        for e in feed.entries[: limit * 2]:
            title = (e.get("title") or "").strip()
            src = (e.get("source") or {}).get("title")
            if src and title.endswith(f" - {src}"):
                title = title[: -len(src) - 3].strip()
            pub = None
            if e.get("published_parsed"):
                pub = dt.datetime.fromtimestamp(calendar.timegm(e.published_parsed), tz=dt.timezone.utc)
            if not title:
                continue
            out.append(NewsItem(title=title, url=e.get("link"), source=src, published_at=pub,
                                provider=self.name, language=language))
            if len(out) >= limit:
                break
        health.ok(self.name)
        return out


class SecEdgar:
    """Company filings from data.sec.gov (US issuers only)."""
    name = "sec_edgar"
    FORMS = {"8-K", "10-K", "10-Q", "6-K", "20-F", "S-1", "S-3", "DEF 14A", "SC 13D", "SC 13G", "424B5"}
    ITEM_LABELS = {"1.01": "Material agreement", "2.01": "Acquisition/disposition", "2.02": "Results of operations (earnings)",
                   "2.05": "Restructuring costs", "2.06": "Impairment", "5.02": "Officer/director change",
                   "7.01": "Reg FD disclosure", "8.01": "Other events", "1.05": "Cybersecurity incident",
                   "3.02": "Unregistered equity sale", "5.07": "Shareholder vote"}

    def __init__(self) -> None:
        ua = get_settings().sec_user_agent
        self._client = httpx.Client(timeout=20.0, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self._tickers: dict[str, dict] | None = None
        self._tickers_at = 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0

    def _throttle(self) -> None:  # stay well below the 10 req/s fair-access limit
        with self._lock:
            wait = 0.15 - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def ticker_map(self) -> dict[str, dict]:
        if self._tickers is not None and time.time() - self._tickers_at < 86400:
            return self._tickers
        self._throttle()
        try:
            r = self._client.get("https://www.sec.gov/files/company_tickers_exchange.json")
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            health.fail(self.name, e)
            raise ProviderError(f"SEC tickers: {e}") from e
        fields = data["fields"]
        out = {}
        for row in data["data"]:
            d = dict(zip(fields, row))
            out[str(d["ticker"]).upper()] = {"cik": int(d["cik"]), "name": d["name"], "exchange": d.get("exchange")}
        self._tickers, self._tickers_at = out, time.time()
        health.ok(self.name)
        return out

    def filings(self, ticker: str, limit: int = 15) -> list[NewsItem]:
        info = self.ticker_map().get(ticker.upper().replace("-", "."), None) or self.ticker_map().get(ticker.upper())
        if not info:
            return []
        cik = info["cik"]
        self._throttle()
        try:
            r = self._client.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
            r.raise_for_status()
            recent = r.json().get("filings", {}).get("recent", {})
        except Exception as e:
            health.fail(self.name, e)
            raise ProviderError(f"SEC submissions: {e}") from e
        out: list[NewsItem] = []
        n = len(recent.get("form", []))
        for i in range(n):
            form = recent["form"][i]
            if form not in self.FORMS:
                continue
            acc = recent["accessionNumber"][i]
            doc = recent["primaryDocument"][i]
            accepted = recent.get("acceptanceDateTime", [None] * n)[i]
            try:
                pub = dt.datetime.fromisoformat(accepted.replace("Z", "+00:00")) if accepted else None
            except ValueError:
                pub = None
            if pub is None:
                pub = dt.datetime.fromisoformat(recent["filingDate"][i]).replace(tzinfo=dt.timezone.utc)
            items = recent.get("items", [""] * n)[i] or ""
            labels = [self.ITEM_LABELS[x] for x in items.split(",") if x in self.ITEM_LABELS]
            desc = recent.get("primaryDocDescription", [""] * n)[i] or ""
            title = f"{form} filed" + (f": {', '.join(labels)}" if labels else (f": {desc}" if desc and desc.upper() != form else ""))
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
            out.append(NewsItem(title=title, url=url, source="SEC EDGAR", published_at=pub, provider=self.name,
                                tickers=[ticker.upper()], category="filing", language="en", scope="filing",
                                summary=f"Form {form}" + (f" items {items}" if items else "")))
            if len(out) >= limit:
                break
        health.ok(self.name)
        return out


class Finnhub:
    name = "finnhub"
    BASE = "https://finnhub.io/api/v1"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0)

    @property
    def enabled(self) -> bool:
        return bool(get_settings().finnhub_api_key)

    def _get(self, path: str, params: dict) -> list[dict]:
        key = get_settings().finnhub_api_key
        if not key:
            return []
        try:
            r = self._client.get(f"{self.BASE}{path}", params={**params, "token": key})
            if r.status_code == 429:
                raise ProviderError("finnhub rate limit")
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            health.fail(self.name, e)
            raise ProviderError(f"finnhub: {e}") from e
        health.ok(self.name)
        return data if isinstance(data, list) else []

    @staticmethod
    def _to_item(d: dict, scope: str) -> NewsItem:
        ts = d.get("datetime")
        related = [t for t in re.split(r"[,\s]+", d.get("related") or "") if t]
        return NewsItem(title=(d.get("headline") or "").strip(), url=d.get("url"), source=d.get("source"),
                        published_at=dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc) if ts else None,
                        provider="finnhub", summary=(d.get("summary") or None), tickers=related, language="en",
                        scope=scope)

    def company_news(self, symbol: str, days: int = 7) -> list[NewsItem]:
        to = dt.date.today()
        rows = self._get("/company-news", {"symbol": symbol, "from": (to - dt.timedelta(days=days)).isoformat(),
                                           "to": to.isoformat()})
        return [self._to_item(d, "stock") for d in rows[:50] if d.get("headline")]

    def general_news(self) -> list[NewsItem]:
        rows = self._get("/news", {"category": "general"})
        return [self._to_item(d, "market") for d in rows[:60] if d.get("headline")]


google_news = GoogleNewsRSS()
sec = SecEdgar()
finnhub = Finnhub()
