"""Value-up / low-PBR overview for the Korean market.

Official data (한국거래소 KIND): the published 저PBR list (since 2026-11-02, refreshed every May and November) and every
기업가치 제고 계획 disclosure. Computed data (OpenDART + market caps): PBR, ROE, PER, growth, dividends and the KR
fundamental score for every listed company, so the official list can be read next to the whole market.
PBR-ROE map: with a cost of equity around 8-10%, a company earning ROE >= 10% while trading below book (PBR < 1) is
cheap on that logic (저평가 우량 후보); one earning ROE < 3% below book is cheap for a reason (가치함정 주의).
"""
from __future__ import annotations

import logging
import statistics

from ..analysis import kr_financials as kf
from ..cache import cached
from . import dart_fin
from .store import clean, utcnow
from .symbols import index

log = logging.getLogger(__name__)
ROE_GOOD, ROE_POOR = 10.0, 3.0


def classify(pbr: float | None, roe: float | None, impaired: bool = False, one_off: bool = False,
             roe_last_fy: float | None = None) -> str | None:
    """PBR-ROE bucket. A high ROE only counts when operations earned it and it was there last year too."""
    if impaired:
        return "자본잠식"
    if pbr is None or roe is None:
        return None
    if pbr >= 1:
        return None
    if roe >= ROE_GOOD:
        return "이익 질 확인" if one_off or (roe_last_fy is not None and roe_last_fy < 5) else "저평가 우량 후보"
    return "가치함정 주의" if roe < ROE_POOR else "저PBR"


def overview() -> dict:
    return cached("valueup:overview", 1800, _build)


def _build() -> dict:
    lists = dart_fin.kind_lists()
    live = dart_fin.listed()
    snap = dart_fin.snapshot() if dart_fin.enabled() else {"rows": [], "dist": {}, "periods": []}
    by_code = {r["code"]: r for r in snap["rows"]}
    low, vu = lists["low_pbr"], lists["valueup"]
    rows = []
    for code, lv in live.items():
        m = by_code.get(code) or {}
        sym = dart_fin.symbol_of(code, lv.get("market"))
        ix = index.get(sym) or {}
        sc = kf.score(m, snap["dist"])["score"] if m.get("available") else None
        lp, v = low.get(code), vu.get(code)
        g = m.get("rev_growth") or {}
        og = m.get("op_growth") or {}
        rows.append({
            "code": code, "symbol": sym, "name": lv.get("name"), "market": lv.get("market"), "industry": ix.get("industry"),
            "cap": lv.get("company_cap"), "close": lv.get("close"), "chg": lv.get("change_pct"),
            "pbr": m.get("pbr"), "roe": m.get("roe"), "per": m.get("per"), "opm": m.get("op_margin"),
            "rev_g": g.get("pct"), "rev_state": g.get("state"), "op_g": og.get("pct"), "op_state": og.get("state"),
            "debt": m.get("debt_ratio"), "div_y": m.get("div_yield"), "score": sc, "fin": m.get("financial"),
            "basis": m.get("basis"), "period": (m.get("period") or {}).get("end"),
            "impaired": bool(m.get("capital_impaired")),
            "cls": classify(m.get("pbr"), m.get("roe"), bool(m.get("capital_impaired")), bool(m.get("one_off")), m.get("roe_last_fy")),
            "one_off": bool(m.get("one_off")),
            "low_pbr": {k: lp[k] for k in ("pbr", "gics_sector", "published", "pbr_date")} if lp else None,
            "vu": {"last_plan": v["last_plan"], "plans": v["plans"], "progress": v["progress"], "pbr_plan": v["pbr_plan"],
                   "tags": v["tags"], "url": (v["items"][0] if v["items"] else {}).get("url")} if v else None,
        })
    with_pbr = [r for r in rows if r["pbr"] is not None]
    official = [r for r in rows if r["low_pbr"]]

    def share_below_1(mkt):
        xs = [r for r in with_pbr if r["market"] == mkt]
        return {"n": len(xs), "below_1": sum(r["pbr"] < 1 for r in xs),
                "pct": round(sum(r["pbr"] < 1 for r in xs) / len(xs) * 100, 1) if xs else None}
    sectors: dict[str, int] = {}
    for r in official:
        sectors[r["low_pbr"]["gics_sector"] or "기타"] = sectors.get(r["low_pbr"]["gics_sector"] or "기타", 0) + 1
    planned = [r for r in rows if r["vu"] and r["vu"]["last_plan"]]
    summary = {
        "official": {"n": len(official), "kospi": sum(r["market"] == "KOSPI" for r in official),
                     "kosdaq": sum(r["market"] == "KOSDAQ" for r in official),
                     "median_pbr": statistics.median([r["low_pbr"]["pbr"] for r in official if r["low_pbr"]["pbr"] is not None])
                     if official else None,
                     "published": lists.get("low_pbr_published"),
                     "sectors": sorted(({"sector": k, "n": n} for k, n in sectors.items()), key=lambda x: -x["n"]),
                     "with_plan": sum(1 for r in official if r["vu"] and r["vu"]["last_plan"])},
        "below_book": {"KOSPI": share_below_1("KOSPI"), "KOSDAQ": share_below_1("KOSDAQ")},
        "valueup": {"companies": len(planned), "pbr_plan": sum(1 for r in planned if r["vu"]["pbr_plan"]),
                    "recent": sorted(({"code": r["code"], "symbol": r["symbol"], "name": r["name"], "time": r["vu"]["last_plan"],
                                       "url": r["vu"]["url"], "pbr": r["pbr"], "low_pbr": bool(r["low_pbr"])} for r in planned),
                                     key=lambda x: x["time"], reverse=True)[:12]},
        "classes": {c: sum(1 for r in rows if r["cls"] == c) for c in ("저평가 우량 후보", "이익 질 확인", "저PBR", "가치함정 주의", "자본잠식")},
    }
    ev = dart_fin.evidence()
    return clean({
        "available": True, "dart_enabled": dart_fin.enabled(), "rows": rows, "summary": summary, "evidence": ev,
        "periods": snap.get("periods"), "errors": lists.get("errors") or [],
        "dart_status": dart_fin.status(),
        "notes": [
            "저PBR 명단은 한국거래소가 공표한 그대로입니다 (업종별 PBR 하위권이 6개 반기 이상 이어진 기업, 개선계획을 낸 기업은 원칙적으로 제외). "
            "명단의 PBR은 거래소 산출값입니다.",
            "그 밖의 PBR·ROE·PER은 OpenDART 재무제표와 시가총액(모든 주식 종류 합산)으로 직접 계산했습니다. 전체 재무제표를 받은 회사는 "
            "지배주주 기준, 나머지는 연결 자본총계·당기순이익(비지배지분 포함) 기준이라 거래소 값과 다를 수 있습니다.",
            f"PBR-ROE 구분: PBR 1배 미만이면서 ROE {ROE_GOOD:.0f}% 이상은 '저평가 우량 후보', ROE {ROE_POOR:.0f}% 미만은 '가치함정 주의'. "
            "단 ROE가 일회성 이익 때문일 수 있으면(순이익이 영업이익의 2배 초과, 영업적자 중 순이익, 직전 연도 ROE 5% 미만) "
            "'이익 질 확인'으로 따로 둡니다. 자기자본비용을 8~10%로 볼 때의 이론적 구분이며 매수 신호가 아닙니다.",
        ],
        "source": "한국거래소 KIND (저PBR 명단·기업가치 제고 계획 공시) · 금융감독원 OpenDART · 네이버 증권 (시가총액)",
        "updated_at": utcnow().isoformat(),
    })
