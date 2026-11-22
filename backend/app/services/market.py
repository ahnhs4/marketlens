"""Market snapshot, sector performance, correlation matrix."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..cache import cached
from ..providers.yahoo import yahoo
from . import clock
from .prices import get_quotes
from .store import clean, utcnow

log = logging.getLogger(__name__)

INDICES = [
    ("^GSPC", "S&P 500", "미국"), ("^NDX", "나스닥 100", "미국"), ("^DJI", "다우존스", "미국"),
    ("^RUT", "러셀 2000", "미국"), ("^VIX", "VIX 변동성", "미국"),
    ("^KS11", "코스피", "한국"), ("^KQ11", "코스닥", "한국"),
    ("^STOXX50E", "유로스톡스 50", "유럽"), ("^N225", "니케이 225", "일본"), ("^HSI", "항셍", "홍콩"),
]
SECTOR_ETFS = [("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "경기소비재"),
               ("XLP", "필수소비재"), ("XLV", "헬스케어"), ("XLF", "금융"), ("XLI", "산업재"),
               ("XLB", "소재"), ("XLE", "에너지"), ("XLU", "유틸리티"), ("XLRE", "부동산"),
               ("SMH", "반도체"), ("IGV", "소프트웨어"), ("XBI", "바이오"), ("KRE", "지역은행"),
               ("GDX", "금광"), ("ITA", "항공·방산"), ("XHB", "주택건설")]
CORR_ASSETS = [("^GSPC", "S&P 500"), ("^NDX", "나스닥 100"), ("^KS11", "코스피"), ("GC=F", "금"),
               ("BTC-USD", "비트코인"), ("DX-Y.NYB", "달러인덱스"), ("^TNX", "미 10년물 금리"), ("CL=F", "WTI 유가"),
               ("KRW=X", "원/달러"), ("TLT", "미 장기국채")]


def _daily_batch(symbols: tuple[str, ...], period: str) -> dict[str, pd.DataFrame]:
    return cached(f"batch:{period}:{','.join(symbols)}", 300, lambda: yahoo.history_parallel(list(symbols), period=period))


def _trend(close: pd.Series) -> dict:
    c = close.dropna()
    if len(c) < 55:
        return {"label": "n/a"}
    s20, s50 = c.rolling(20).mean().iloc[-1], c.rolling(50).mean().iloc[-1]
    last = c.iloc[-1]
    m20 = last / c.iloc[-21] - 1
    if last > s20 > s50:
        lab = "Uptrend"
    elif last < s20 < s50:
        lab = "Downtrend"
    else:
        lab = "Sideways"
    return {"label": lab, "vs_sma20_pct": (last / s20 - 1) * 100, "vs_sma50_pct": (last / s50 - 1) * 100,
            "mom20_pct": m20 * 100}


def snapshot() -> dict:
    def build():
        syms = [s for s, _, _ in INDICES]
        quotes = get_quotes(syms)
        daily = _daily_batch(tuple(syms), "6mo")
        cards = []
        for sym, name, region in INDICES:
            q = quotes.get(sym)
            d = daily.get(sym)
            spark = [] if d is None else [round(float(x), 4) for x in d["close"].iloc[-30:]]
            card = {"symbol": sym, "name": name, "region": region,
                    "trend": _trend(d["close"]) if d is not None else {"label": "n/a"}, "sparkline": spark}
            if q is None:
                card.update(price=None, status="Data unavailable")
            else:
                card.update(q.to_dict())
                card["name"] = name
                card["status"] = q.market_state
            cards.append(card)
        return {"cards": cards, "sessions": {"US": clock.status("US"), "KR": clock.status("KR")},
                "updated_at": utcnow().isoformat()}
    return clean(cached("market:snapshot", 30, build))


def sectors() -> dict:
    def build():
        syms = tuple([s for s, _ in SECTOR_ETFS] + ["SPY"])
        daily = _daily_batch(syms, "1y")
        spy = daily.get("SPY")
        rows = []
        for s, name in SECTOR_ETFS:
            d = daily.get(s)
            if d is None or len(d) < 70:
                rows.append({"symbol": s, "name": name, "status": "Data unavailable"})
                continue
            c = d["close"]
            perf = {k: float(c.iloc[-1] / c.iloc[-1 - n] - 1) * 100 for k, n in (("1d", 1), ("1w", 5), ("1m", 21), ("3m", 63))}
            perf["ytd"] = float(c.iloc[-1] / c[c.index.year == c.index[-1].year].iloc[0] - 1) * 100
            rs = None
            if spy is not None and len(spy) > 63:
                sc = spy["close"]
                rs = perf["3m"] - float(sc.iloc[-1] / sc.iloc[-64] - 1) * 100
            rows.append({"symbol": s, "name": name, "price": float(c.iloc[-1]), "perf": perf, "rs_3m_vs_spy": rs,
                         "above_sma50": bool(c.iloc[-1] > c.rolling(50).mean().iloc[-1]),
                         "as_of": str(c.index[-1].date())})
        return {"sectors": rows, "updated_at": utcnow().isoformat(), "source": "Yahoo Finance (섹터 ETF를 대용 지표로 사용)"}
    return clean(cached("market:sectors", 600, build))


def correlation(window: str = "90d") -> dict:
    days = {"30d": 30, "90d": 90, "1y": 252, "3y": 756}.get(window, 90)

    def build():
        syms = tuple(s for s, _ in CORR_ASSETS)
        daily = _daily_batch(syms, "4y")
        closes = {}
        for s, name in CORR_ASSETS:
            d = daily.get(s)
            if d is not None and not d.empty:
                closes[name] = d["close"]
        if len(closes) < 3:
            return {"status": "Data unavailable"}
        px = pd.DataFrame(closes).sort_index().ffill(limit=3)
        # yields: use changes; prices: log returns. Calendar mismatch (KR/crypto) handled by date alignment.
        rets = pd.DataFrame({k: (px[k].diff() if k == "미 10년물 금리" else np.log(px[k]).diff()) for k in px})
        rets = rets.dropna(how="all").iloc[-days:]
        corr = rets.corr(min_periods=max(15, days // 3))
        return {"window": window, "assets": list(corr.columns),
                "matrix": [[None if pd.isna(v) else round(float(v), 3) for v in row] for row in corr.to_numpy()],
                "observations": int(len(rets)), "updated_at": utcnow().isoformat(),
                "note": "일간 로그수익률(금리는 일간 변화폭) 기준, 공통 거래일끼리 쌍별 계산."}
    return clean(cached(f"market:corr:{window}", 1800, build))
