"""Naver Securities public market lists (no key): price and market cap for every KOSPI / KOSDAQ listing.

The KRX data portal now requires a login, so the market-wide price / market-cap snapshot comes from the
same lists m.stock.naver.com shows (100 rows per page, ~45 pages for both markets).
"""
from __future__ import annotations

import logging
import time

import httpx

from ..config import APP_USER_AGENT
from .base import ProviderError, health

log = logging.getLogger(__name__)
URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"


def _n(x) -> float | None:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


class NaverKR:
    name = "naver_kr"

    def market_values(self) -> list[dict]:
        """Every listed share class: code, name, market, type (stock/etf/...), close, market cap (KRW)."""
        out: list[dict] = []
        with httpx.Client(timeout=20.0, headers={"User-Agent": APP_USER_AGENT}) as c:
            for market in ("KOSPI", "KOSDAQ"):
                page, total = 1, None
                while total is None or (page - 1) * 100 < total:
                    try:
                        r = c.get(URL.format(market=market), params={"page": page, "pageSize": 100})
                        r.raise_for_status()
                        d = r.json()
                    except Exception as e:
                        health.fail(self.name, e)
                        raise ProviderError(f"Naver {market} p{page}: {e}") from e
                    total = int(d.get("totalCount") or 0)
                    rows = d.get("stocks") or []
                    if not rows:
                        break
                    for s in rows:
                        mv = _n(s.get("marketValue"))       # 억원
                        out.append({"code": s.get("itemCode"), "name": s.get("stockName"), "market": market,
                                    "type": s.get("stockEndType"), "close": _n(s.get("closePrice")),
                                    "change_pct": _n(s.get("fluctuationsRatio")),
                                    "market_cap": mv * 1e8 if mv is not None else None,
                                    "traded_at": s.get("localTradedAt")})
                    page += 1
                    time.sleep(0.15)
        health.ok(self.name)
        return out


naver_kr = NaverKR()
