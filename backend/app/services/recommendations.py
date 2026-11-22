"""Macro, technical and combined rankings + the screener (all read the latest universe scan)."""
from __future__ import annotations

import operator

from ..analysis import macro_scoring
from ..analysis.scoring import band
from . import macro, universe
from .store import clean, utcnow

HORIZON_LABEL = {"short": "단기 (1~20일)", "medium": "중기 (20~120일)", "long": "장기 (120일+)"}


def _scan_meta() -> dict:
    st = universe.status()
    return {"scan": st, "results": len(universe.results()),
            "note": None if universe.results() else "종목 스캔이 아직 끝나지 않았습니다 - 잠시 후 결과가 표시됩니다."}


def macro_ranking() -> dict:
    b = universe.latest_breadth()
    reg = macro.regime(b.get("pct_above_sma200") if b else None)
    ft = reg["factor_trends"]
    rs = universe.group_rs()
    perf = rs.get("_perf", {})
    rows = []
    for g, kind, proxy in macro_scoring.GROUP_PROXIES:
        res = macro_scoring.group_score(g, ft, rs.get(g))
        rows.append({**res, "kind": kind, "proxy": proxy, "perf_3m": perf.get(g)})
    rows.sort(key=lambda r: -r["score"])
    # Single stocks with the strongest macro alignment from the latest scan.
    rankable, excluded = _rankable(universe.results())
    stocks = sorted([r for r in rankable if r.get("macro") is not None], key=lambda r: -r["macro"])[:20]
    return clean({"regime": {k: reg[k] for k in ("label", "score", "quadrant", "overlays", "subscores")},
                  "factors": ft, "groups": rows,
                  "stocks": [{"symbol": s["symbol"], "name": s["name"], "group": s["group"], "macro": s["macro"],
                              "technical": s["technical"], "reasons": s["macro_reasons"], "risks": s["macro_risks"]}
                             for s in stocks],
                  "method": "섹터·자산 점수 = 50 + 35×매크로 정합도(민감도×팩터 추세) + 15×3개월 상대강도",
                  "excluded_overbought": excluded,
                  "updated_at": utcnow().isoformat(), **_scan_meta()})


def _rankable(rows: list[dict]) -> tuple[list[dict], dict]:
    """The user's ranking exclusions - those stocks never appear in any ranking:
      * medium (10-6-6) AND long (20-12-12) slow stochastic %K both overbought (≥ 80)
      * medium (14) or long (28) RSI oversold (≤ 30), or 30~35 and rising (higher than 3 bars ago)
    Returns (kept rows, what was left out, per rule)."""
    def hit(r: dict) -> list[str]:
        why = []
        if r.get("stoch_ml_overbought"):
            why.append("stoch_ob")
        if r.get("rsi_ml_oversold"):
            why.append("rsi_os")
        return why
    out = [r for r in rows if hit(r)]
    kept = [r for r in rows if not hit(r)]
    pending = sum(1 for r in rows if "stoch_ml_overbought" not in r or "rsi_ml_oversold" not in r)

    def group(key: str, label: str, rule: str) -> dict:
        g = sorted((r for r in out if key in hit(r)), key=lambda r: -(r.get("technical") or 0))
        return {"key": key, "label": label, "rule": rule, "count": len(g),
                "items": [{"symbol": r["symbol"], "name": r.get("name_ko") or r.get("name"),
                           "why": r.get("rsi_ml_oversold") if key == "rsi_os" else None} for r in g[:40]]}
    groups = [group("stoch_ob", "중기·장기 스토캐스틱 동시 과매수", "중기(10-6-6) · 장기(20-12-12) 스토캐스틱 %K가 둘 다 80 이상"),
              group("rsi_os", "중기·장기 RSI 과매도권", "중기 RSI(14)나 장기 RSI(28)가 30 이하이거나, 30~35에서 3일 전보다 오르는 중")]
    return kept, {"count": len(out), "symbols": [r["symbol"] for r in sorted(out, key=lambda r: -(r.get("technical") or 0))][:30],
                  "rule": " / ".join(f"{g['label']}: {g['rule']}" for g in groups) + " → 순위에서 제외",
                  "groups": groups, "pending": pending}


def _filter_universe(rows: list[dict], uni: str) -> list[dict]:
    if uni in ("ALL", "", None):
        return rows
    if uni == "WATCHLIST":
        wl = set(universe.watchlist_symbols())
        return [r for r in rows if r["symbol"] in wl]
    return [r for r in rows if uni in (r.get("universes") or [])]


def _card(r: dict, score_key: str) -> dict:
    hz = r.get("best_horizon") or "medium"
    return {"symbol": r["symbol"], "name": r["name"], "name_ko": r.get("name_ko"), "market": r["market"],
            "group": r.get("group"), "price": r["price"], "change_pct": r.get("change_pct"),
            "score": r.get(score_key), "technical": r["technical"], "macro": r.get("macro"), "signal": r["signal"],
            "trend": r.get("trend"), "rsi": r.get("rsi"), "mom20": r.get("mom20"), "vol_ratio": r.get("vol_ratio"),
            "pattern": (r.get("patterns") or [None])[0], "volatility": r.get("volatility"),
            "risk": _risk_label(r), "reason": (r.get("bullish") or [])[:3], "risks": (r.get("bearish") or [])[:2],
            "macro_reasons": r.get("macro_reasons"), "time_horizon": HORIZON_LABEL.get(hz, hz),
            "horizons": r.get("horizons"), "as_of": r.get("as_of")}


def _risk_label(r: dict) -> str:
    v = r.get("volatility") or 0
    return "High" if v > 0.55 else "Medium" if v > 0.30 else "Low"


def technical_ranking(uni: str = "ALL", limit: int = 30, min_dollar_volume: float = 0) -> dict:
    rows = [r for r in _filter_universe(universe.results(), uni) if (r.get("dollar_volume") or 0) >= min_dollar_volume]
    rows, excluded = _rankable(rows)
    rows.sort(key=lambda r: -(r["technical"] or 0))
    return clean({"universe": uni, "items": [_card(r, "technical") for r in rows[:limit]], "excluded_overbought": excluded,
                  "count": len(rows), "updated_at": utcnow().isoformat(), **_scan_meta()})


def combined_ranking(uni: str = "ALL", w_tech: float = 0.6, w_macro: float = 0.4, limit: int = 30) -> dict:
    tot = w_tech + w_macro or 1
    w_tech, w_macro = w_tech / tot, w_macro / tot
    rows = []
    rankable, excluded = _rankable(_filter_universe(universe.results(), uni))
    for r in rankable:
        if r.get("macro") is None:
            continue
        comb = w_tech * r["technical"] + w_macro * r["macro"]
        rating = ("Strong Buy" if comb >= 78 else "Buy" if comb >= 62 else "Neutral" if comb > 40 else
                  "Sell" if comb > 28 else "Strong Sell")
        rows.append({**_card(r, "technical"), "combined": round(comb, 1), "rating": rating, "band": band(comb),
                     "overlap": r["technical"] >= 62 and r["macro"] >= 60,
                     "breakdown": {"technical": r["technical"], "macro": r["macro"], "w_tech": w_tech, "w_macro": w_macro}})
    rows.sort(key=lambda x: -x["combined"])
    return clean({"universe": uni, "weights": {"technical": w_tech, "macro": w_macro}, "items": rows[:limit],
                  "overlap": [x for x in rows if x["overlap"]][:15], "count": len(rows), "excluded_overbought": excluded,
                  "updated_at": utcnow().isoformat(), **_scan_meta()})


# ---------------------------------------------------------------- screener
FIELDS = {
    "price": "price", "market_cap": "f_market_cap_usd", "pe": "f_pe", "forward_pe": "f_forward_pe",
    "revenue_growth": "f_revenue_growth", "eps_growth": "f_earnings_growth", "rsi": "rsi", "mom20": "mom20",
    "mom60": "mom60", "vol_ratio": "vol_ratio", "technical": "technical", "macro": "macro", "overall": "_overall",
    "dist_sma200": "dist_sma200", "atr_pct": "atr_pct", "change_pct": "change_pct", "dist_52w_high": "dist_52w_high",
    "pbr": "f_pbr", "roe": "f_roe", "fin_score": "f_dart_score",     # Korean companies (OpenDART)
}
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "=": operator.eq}


def screen(filters: list[dict], uni: str = "ALL", sort: str = "technical", desc: bool = True, limit: int = 100,
           booleans: dict | None = None) -> dict:
    rows, excluded = _rankable(_filter_universe(universe.results(), uni))
    out, missing_fields = [], set()
    for r in rows:
        r = dict(r)
        r["_overall"] = (0.6 * r["technical"] + 0.4 * r["macro"]) if r.get("macro") is not None else None
        ok = True
        for f in filters:
            key = FIELDS.get(f.get("field"))
            op = OPS.get(f.get("op"))
            if not key or not op:
                continue
            v = r.get(key)
            if v is None:
                missing_fields.add(f["field"])
                ok = False
                break
            if not op(float(v), float(f["value"])):
                ok = False
                break
        for k, want in (booleans or {}).items():
            if want is not None and k in ("above_sma200", "sma20_gt_sma60", "near_breakout") and bool(r.get(k)) != bool(want):
                ok = False
        if ok:
            out.append(r)
    sk = FIELDS.get(sort, sort)
    out.sort(key=lambda x: (x.get(sk) is None, -(x.get(sk) or 0) if desc else (x.get(sk) or 0)))
    cov = {}
    for fname in ("market_cap", "pe", "revenue_growth", "eps_growth"):
        key = FIELDS[fname]
        cov[fname] = round(sum(1 for r in rows if r.get(key) is not None) / max(1, len(rows)) * 100, 1)
    return clean({"universe": uni, "total_scanned": len(rows), "matches": len(out),
                  "items": [{**_card(r, "technical"), "overall": r.get("_overall"),
                             "market_cap": r.get("f_market_cap_usd"), "pe": r.get("f_pe"),
                             "forward_pe": r.get("f_forward_pe"), "revenue_growth": r.get("f_revenue_growth"),
                             "eps_growth": r.get("f_earnings_growth"), "dist_sma200": r.get("dist_sma200"),
                             "above_sma200": r.get("above_sma200"), "near_breakout": r.get("near_breakout"),
                             "pbr": r.get("f_pbr"), "roe": r.get("f_roe"), "fin_score": r.get("f_dart_score")}
                            for r in out[:limit]],
                  "field_coverage_pct": cov,
                  "excluded_missing_data": sorted(missing_fields), "excluded_overbought": excluded,
                  "updated_at": utcnow().isoformat(), **_scan_meta()})


def top_opportunities(prefs: dict | None = None) -> dict:
    prefs = prefs or {}
    market = prefs.get("preferred_market", "Global")
    uni = {"US": "SP500", "Korea": "KOSPI"}.get(market, "ALL")
    tech = technical_ranking(uni, limit=5)
    comb = combined_ranking(uni, prefs.get("w_tech", 0.6), prefs.get("w_macro", 0.4), limit=5)
    mac = macro_ranking()
    return clean({"technical": tech["items"], "combined": comb["items"], "excluded_overbought": tech.get("excluded_overbought"),
                  "macro_groups": mac["groups"][:5], "macro_stocks": mac["stocks"][:5], **_scan_meta()})
