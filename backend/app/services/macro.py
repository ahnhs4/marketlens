"""Macro dashboard data + regime computation.

FRED series are cached for 6 hours and persisted to `macro_data`; market-priced
series (FX, commodities, VIX) come from Yahoo and are cached for 10 minutes.
"""
from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from sqlalchemy import select

from ..analysis.regime import compute_regime, factor_trends
from ..cache import cached
from ..db import SessionLocal
from ..models import MacroData
from ..providers.fred import fred
from ..providers.yahoo import yahoo
from .store import clean, upsert, utcnow

log = logging.getLogger(__name__)

FRED_SERIES = ["DFF", "DGS2", "DGS10", "DGS30", "DGS3MO", "T10Y2Y", "DFII10", "T10YIE", "M2SL", "WALCL", "WTREGEN",
               "RRPONTSYD", "NFCI", "BAMLH0A0HYM2", "CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE", "UNRATE", "PAYEMS",
               "ICSA", "CFNAI", "INDPRO", "GACDFSA066MSFRBPHI", "UMCSENT", "SAHMREALTIME"]
YAHOO_SERIES = {"dxy": "DX-Y.NYB", "usdkrw": "KRW=X", "eurusd": "EURUSD=X", "usdjpy": "JPY=X", "usdcny": "CNY=X",
                "gold": "GC=F", "oil": "CL=F", "brent": "BZ=F", "copper": "HG=F", "vix": "^VIX", "vix3m": "^VIX3M",
                "spx": "^GSPC", "tnx": "^TNX", "btc": "BTC-USD"}

# key, label, group, source key, transform, unit, decimals, up_is, impact when rising, impact when falling
INDICATORS: list[tuple] = [
    ("fed_funds", "미국 기준금리(실효)", "Rates", "DFF", "level", "%", 2, "bad",
     "긴축 강화 - 할인율·조달비용 상승", "완화 - 밸류에이션과 신용에 우호적"),
    ("ust2y", "미 국채 2년물", "Rates", "DGS2", "level", "%", 2, "bad",
     "시장이 더 높은 금리 경로를 반영", "시장이 더 낮은 금리 경로를 반영"),
    ("ust10y", "미 국채 10년물", "Rates", "DGS10", "level", "%", 2, "bad",
     "장기금리 상승은 성장주(장기 듀레이션)에 부담", "장기금리 하락은 성장주·주택에 우호적"),
    ("ust30y", "미 국채 30년물", "Rates", "DGS30", "level", "%", 2, "bad",
     "기간 프리미엄 상승, 모기지 금리 상승", "기간 프리미엄 완화"),
    ("spread_2s10s", "장단기 금리차(10Y-2Y)", "Rates", "T10Y2Y", "level", "pp", 2, "mixed",
     "스티프닝: 은행 마진 개선, 성장·기간 프리미엄 재평가일 수도", "플래트닝/역전: 역사적으로 경기 후반 경고"),
    ("real10y", "10년 실질금리(TIPS)", "Rates", "DFII10", "level", "%", 2, "bad",
     "실질금리 상승은 금·고PER 주식에 부담", "실질금리 하락은 금·성장주에 우호적"),
    ("breakeven10y", "10년 기대인플레이션(BEI)", "Inflation", "T10YIE", "level", "%", 2, "bad",
     "인플레이션 기대 상승", "인플레이션 기대 완화"),
    ("dxy", "달러인덱스(DXY)", "Currencies", "dxy", "level", "", 2, "bad",
     "달러 강세는 글로벌 금융여건 긴축, 신흥국·원화 압박", "달러 약세는 글로벌 여건 완화, 신흥국에 우호적"),
    ("usdkrw", "원/달러 환율", "Currencies", "usdkrw", "level", "₩", 1, "bad",
     "원화 약세: 수출기업 환산이익, 외국인 자금 이탈 위험", "원화 강세: 코스피 외국인 자금 유입"),
    ("eurusd", "유로/달러", "Currencies", "eurusd", "level", "", 4, "good", "유로 강세·달러 약세", "유로 약세"),
    ("usdjpy", "달러/엔", "Currencies", "usdjpy", "level", "¥", 2, "mixed",
     "엔화 약세(캐리 트레이드 확대)", "엔화 강세 - 캐리 트레이드 청산 위험"),
    ("gold", "금", "Commodities", "gold", "level", "$", 1, "mixed",
     "안전자산 수요 / 실질금리 하락", "위험선호 또는 실질금리 상승"),
    ("wti", "WTI 원유", "Commodities", "oil", "level", "$", 2, "bad",
     "에너지 비용 상승으로 인플레이션 압력, 에너지 업종에 유리", "에너지 비용 하락으로 인플레이션 완화, 소비자에 유리"),
    ("brent", "브렌트유", "Commodities", "brent", "level", "$", 2, "bad", "국제 유가 상승", "국제 유가 하락"),
    ("copper", "구리", "Commodities", "copper", "level", "$", 3, "good",
     "'닥터 코퍼' 상승: 글로벌 성장 수요", "구리 하락: 성장 우려"),
    ("m2", "미국 M2 통화량(전년비)", "Liquidity", "M2SL", "yoy", "%", 1, "good",
     "통화 증가율 가속", "통화 증가율 둔화/감소"),
    ("net_liquidity", "연준 순유동성", "Liquidity", "net_liquidity", "level", "$T", 2, "good",
     "연준 자산 - TGA - 역레포 증가: 유동성 순풍", "유동성 흡수"),
    ("fed_balance", "연준 대차대조표", "Liquidity", "WALCL", "level_tn", "$T", 2, "good", "대차대조표 확대", "양적긴축(QT) 지속"),
    ("rrp", "역레포(ON RRP) 잔액", "Liquidity", "RRPONTSYD", "level", "$B", 0, "bad",
     "연준에 묶인 현금 증가(유동성 대기)", "역레포 감소로 시장에 유동성 방출"),
    ("nfci", "금융여건지수(NFCI)", "Liquidity", "NFCI", "level", "", 3, "bad",
     "금융여건 긴축", "금융여건 완화"),
    ("hy_oas", "하이일드 신용스프레드", "Liquidity", "BAMLH0A0HYM2", "level", "%", 2, "bad",
     "신용 스트레스 확대 - 위험회피 신호", "신용스프레드 축소 - 위험선호"),
    ("cpi", "CPI(전년비)", "Inflation", "CPIAUCSL", "yoy", "%", 1, "bad", "인플레이션 재가속", "디스인플레이션"),
    ("core_cpi", "근원 CPI(전년비)", "Inflation", "CPILFESL", "yoy", "%", 1, "bad", "근원 물가 고착", "근원 물가 둔화"),
    ("pce", "PCE(전년비)", "Inflation", "PCEPI", "yoy", "%", 1, "bad", "PCE 물가 상승", "PCE 물가 둔화"),
    ("core_pce", "근원 PCE(전년비)", "Inflation", "PCEPILFE", "yoy", "%", 1, "bad",
     "연준 선호 지표가 2%에서 멀어짐", "연준 선호 지표가 2%에 근접"),
    ("unrate", "실업률", "Economy", "UNRATE", "level", "%", 1, "bad", "고용시장 둔화", "고용시장 과열"),
    ("payrolls", "비농업 고용(월간 증감)", "Economy", "PAYEMS", "diff", "k", 0, "good", "고용 강세", "고용 약세"),
    ("claims", "신규 실업수당 청구", "Economy", "ICSA", "level_k", "k", 0, "bad", "해고 증가", "해고 감소"),
    ("philly", "필라델피아연은 제조업지수(ISM 대체)", "Economy", "GACDFSA066MSFRBPHI", "level", "", 1, "good",
     "제조업 경기 개선", "제조업 경기 악화"),
    ("cfnai", "시카고연은 활동지수(CFNAI)", "Economy", "CFNAI", "level", "", 2, "good",
     "추세 이상 성장", "추세 이하 성장"),
    ("indpro", "산업생산(전년비)", "Economy", "INDPRO", "yoy", "%", 1, "good", "생산 확대", "생산 위축"),
    ("sentiment", "미시간대 소비자심리", "Economy", "UMCSENT", "level", "", 1, "good", "소비심리 개선", "소비심리 악화"),
    ("vix", "VIX(변동성지수)", "Sentiment", "vix", "level", "", 2, "bad", "내재변동성 상승 - 헤지 수요 증가", "변동성 진정"),
    ("vix_term", "VIX / VIX3M", "Sentiment", "vix_term", "level", "", 3, "bad",
     "기간구조 평탄화/역전 - 단기 불안", "콘탱고 - 안정적 시장"),
]
UNAVAILABLE = [
    {"key": "ism_mfg", "label": "ISM 제조업 PMI", "group": "Economy",
     "reason": "ISM 데이터는 유료(FRED 미제공) - 필라델피아연은·CFNAI로 대체"},
    {"key": "ism_services", "label": "ISM 서비스업 PMI", "group": "Economy",
     "reason": "ISM 데이터는 유료 - 라이선스 데이터 공급자 필요"},
    {"key": "put_call", "label": "풋/콜 비율", "group": "Sentiment",
     "reason": "신뢰할 무료 소스 없음(Cboe 유료) - VIX 기간구조로 대체"},
]


def _fred_one(sid: str) -> pd.Series | None:
    def load():
        try:
            s = fred.series(sid, start=dt.date.today() - dt.timedelta(days=365 * 12))
        except Exception as e:
            log.warning("FRED %s failed: %s", sid, e)
            return _fred_from_db(sid)
        try:
            with SessionLocal() as db:
                upsert(db, MacroData, [{"series_id": sid, "date": d.date(), "value": float(v), "source": f"fred:{fred.mode}",
                                        "fetched_at": utcnow()} for d, v in s.items()], ["series_id", "date"])
                db.commit()
        except Exception as e:
            log.debug("macro persist failed: %s", e)
        return s
    return cached(f"fred:{sid}", 6 * 3600, load)


def _fred_from_db(sid: str) -> pd.Series | None:
    with SessionLocal() as db:
        rows = db.execute(select(MacroData.date, MacroData.value).where(MacroData.series_id == sid)
                          .order_by(MacroData.date)).all()
    if not rows:
        return None
    return pd.Series([r[1] for r in rows], index=pd.to_datetime([r[0] for r in rows]), name=sid)


def load_all() -> tuple[dict[str, pd.Series], dict]:
    """Return {key: series} for every FRED + market series, plus fetch metadata."""
    def build():
        out: dict[str, pd.Series] = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            for sid, s in zip(FRED_SERIES, ex.map(_fred_one, FRED_SERIES)):
                if s is not None and not s.empty:
                    out[sid] = s
        mk = cached("macro:yahoo", 600, lambda: yahoo.history_parallel(list(YAHOO_SERIES.values()), period="10y"))
        for key, sym in YAHOO_SERIES.items():
            d = mk.get(sym)
            if d is not None and not d.empty:
                out[key] = d["close"]
        if {"WALCL", "WTREGEN", "RRPONTSYD"} <= out.keys():
            w = out["WALCL"]
            nl = (w - out["WTREGEN"].reindex(w.index, method="ffill") - out["RRPONTSYD"].reindex(w.index, method="ffill").fillna(0) * 1000) / 1e6
            out["net_liquidity"] = nl.dropna()
        if "vix" in out and "vix3m" in out:
            out["vix_term"] = (out["vix"] / out["vix3m"].reindex(out["vix"].index)).dropna()
        meta = {"fred_mode": fred.mode, "fetched_at": utcnow().isoformat(),
                "missing": [s for s in FRED_SERIES if s not in out] + [k for k in YAHOO_SERIES if k not in out]}
        return out, meta
    return cached("macro:all", 600, build)


def _transform(s: pd.Series, how: str) -> pd.Series:
    s = s.dropna()
    if how == "yoy":
        return ((s / s.shift(12) - 1) * 100).dropna()
    if how == "diff":
        return s.diff().dropna()
    if how == "level_tn":
        return s / 1e6
    if how == "level_k":
        return s / 1000
    return s


def _freq(s: pd.Series) -> str:
    if len(s) < 3:
        return "unknown"
    d = (s.index[-1] - s.index[-min(len(s), 11)]).days / (min(len(s), 11) - 1)
    return "daily" if d < 3.5 else "weekly" if d < 10 else "monthly" if d < 40 else "quarterly"


def indicator_cards() -> dict:
    series, meta = load_all()
    cards = []
    for key, label, group, src, how, unit, dec, up_is, imp_up, imp_dn in INDICATORS:
        raw = series.get(src)
        if raw is None or raw.dropna().empty:
            cards.append({"key": key, "label": label, "group": group, "status": "Data unavailable"})
            continue
        s = _transform(raw, how)
        if s.empty:
            cards.append({"key": key, "label": label, "group": group, "status": "Data unavailable"})
            continue
        freq = _freq(s)
        lag1, lag2 = {"daily": (5, 21), "weekly": (1, 4), "monthly": (1, 12), "quarterly": (1, 4)}.get(freq, (1, 4))
        cur = float(s.iloc[-1])
        prev = float(s.iloc[-1 - lag1]) if len(s) > lag1 else None
        prev2 = float(s.iloc[-1 - lag2]) if len(s) > lag2 else None
        hist5 = s[s.index >= s.index[-1] - pd.DateOffset(years=5)]
        pct = float((hist5 < cur).mean() * 100) if len(hist5) > 10 else None
        z = float((cur - hist5.mean()) / hist5.std()) if len(hist5) > 10 and hist5.std() > 0 else None
        ch = None if prev is None else cur - prev
        tol = 1e-9 if prev is None else max(abs(prev) * 0.002, 10 ** -dec)
        direction = None if ch is None else ("up" if ch > tol else "down" if ch < -tol else "flat")
        if direction in ("up", "down"):
            effect = ("negative" if up_is == "bad" else "positive" if up_is == "good" else "mixed") if direction == "up" else \
                ("positive" if up_is == "bad" else "negative" if up_is == "good" else "mixed")
            impact = imp_up if direction == "up" else imp_dn
        else:
            effect, impact = "neutral", "변화 거의 없음"
        spark_src = s.iloc[-60:] if freq == "daily" else s.iloc[-36:]
        cards.append({
            "key": key, "label": label, "group": group, "unit": unit, "decimals": dec, "frequency": freq,
            "value": cur, "date": str(s.index[-1].date()), "previous": prev, "previous_label":
                {"daily": "1주 전", "weekly": "전주", "monthly": "전월", "quarterly": "전분기"}.get(freq, "이전"),
            "previous2": prev2, "previous2_label": {"daily": "1개월 전", "weekly": "4주 전", "monthly": "1년 전",
                                                    "quarterly": "1년 전"}.get(freq, ""),
            "change": ch, "direction": direction, "avg_5y": float(hist5.mean()) if len(hist5) else None,
            "percentile_5y": pct, "zscore_5y": z, "equity_effect": effect, "impact": impact,
            "source": "FRED" if src.isupper() or src in ("net_liquidity",) else "Yahoo Finance",
            "series_id": src, "sparkline": [round(float(v), 4) for v in spark_src],
        })
    return clean({"cards": cards, "unavailable": UNAVAILABLE, "meta": meta, "updated_at": utcnow().isoformat()})


def regime(breadth_pct: float | None = None) -> dict:
    def build():
        series, meta = load_all()
        res = compute_regime(series, breadth_pct)
        res["factor_trends"] = factor_trends(series, res["subscores_raw"])
        res["data_as_of"] = {k: str(v.dropna().index[-1].date()) for k, v in series.items() if not v.dropna().empty}
        res["updated_at"] = utcnow().isoformat()
        res["fred_mode"] = meta["fred_mode"]
        return res
    key = f"macro:regime:{None if breadth_pct is None else round(breadth_pct)}"
    return clean(cached(key, 600, build))


def series_history(key: str, years: int = 5) -> dict:
    series, _ = load_all()
    spec = next((i for i in INDICATORS if i[0] == key), None)
    if spec is None:
        return {"status": "unknown indicator"}
    raw = series.get(spec[3])
    if raw is None:
        return {"status": "Data unavailable"}
    s = _transform(raw, spec[4])
    s = s[s.index >= s.index[-1] - pd.DateOffset(years=years)]
    return clean({"key": key, "label": spec[1], "unit": spec[5],
                  "points": [{"date": str(d.date()), "value": float(v)} for d, v in s.items()]})


def factor_frame(years: int = 3) -> pd.DataFrame:
    """Daily factor moves for sensitivity regressions: market %, 10Y bp change, USD %, oil %."""
    series, _ = load_all()
    need = {"spx": "market", "tnx": "rate_bp", "dxy": "usd", "oil": "oil"}
    cols = {}
    for k, name in need.items():
        s = series.get(k)
        if s is None:
            continue
        s = s.dropna()
        cols[name] = s.diff() * 100 if name == "rate_bp" else s.pct_change()  # ^TNX is quoted in percent
    f = pd.DataFrame(cols).dropna()
    return f[f.index >= f.index[-1] - pd.DateOffset(years=years)] if len(f) else f


def factor_moves(days: int = 60) -> dict[str, float]:
    series, _ = load_all()
    out = {}
    for k, name in (("tnx", "rate_bp"), ("dxy", "usd"), ("oil", "oil"), ("spx", "market")):
        s = series.get(k)
        if s is None or len(s.dropna()) <= days:
            continue
        s = s.dropna()
        out[name] = float((s.iloc[-1] - s.iloc[-1 - days]) * 100) if name == "rate_bp" else float((s.iloc[-1] / s.iloc[-1 - days] - 1) * 100)
    return out

