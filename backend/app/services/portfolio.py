"""Portfolio analytics, risk flags and factor-based stress tests.

Stress tests are NOT forecasts: each holding's sensitivity to market, 10Y yield,
USD and oil moves is estimated by regression on ~3 years of daily data, then a
user-defined (or preset) shock is applied: impact_i = sum_j beta_ij * shock_j.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from ..analysis import macro_scoring, position_advice
from . import macro, prices
from .store import clean, utcnow

log = logging.getLogger(__name__)

SCENARIOS = {
    "fed_slower_cuts": {"label": "연준 금리인하 지연", "market": -3.0, "rate_bp": 25, "usd": 1.5, "oil": 0.0},
    "rates_spike": {"label": "미 10년물 금리 +50bp", "market": -4.0, "rate_bp": 50, "usd": 2.0, "oil": 0.0},
    "oil_shock": {"label": "유가 +15%", "market": -2.0, "rate_bp": 10, "usd": 0.5, "oil": 15.0},
    "usd_surge": {"label": "달러 +3%", "market": -1.5, "rate_bp": 5, "usd": 3.0, "oil": -2.0},
    "equity_correction": {"label": "S&P 500 -8% 조정", "market": -8.0, "rate_bp": -15, "usd": 1.0, "oil": -5.0},
    "risk_on": {"label": "위험선호 랠리 (S&P +5%, 금리 -10bp)", "market": 5.0, "rate_bp": -10, "usd": -1.0, "oil": 2.0},
}
CONTEXT_ASSETS = [("XLK", "기술주"), ("TLT", "장기 국채"), ("GLD", "금"), ("UUP", "달러"),
                  ("XLE", "에너지"), ("EWY", "한국 주식")]
TECH_GROUPS = {"Technology", "Semiconductors", "Software", "Communication Services"}
DEFENSIVE = {"Consumer Staples", "Utilities", "Health Care"}


def _fx_usdkrw() -> float | None:
    try:
        return prices.get_quote("KRW=X").price
    except Exception:
        return None


def _to_base(value: float, ccy: str, base: str, usdkrw: float | None) -> float | None:
    if ccy == base:
        return value
    if usdkrw is None:
        return None
    if ccy == "KRW" and base == "USD":
        return value / usdkrw
    if ccy == "USD" and base == "KRW":
        return value * usdkrw
    return None


def _value_rows(positions: list[dict], base: str) -> tuple[list[dict], list[dict], float | None]:
    """Quote every position and convert to the base currency; adds `weight` (% of valued total)."""
    from .stock import profile

    usdkrw = _fx_usdkrw()
    rows, errors = [], []
    for p in positions:
        sym = p["symbol"]
        try:
            q = prices.get_quote(sym)
        except Exception as e:
            errors.append({"symbol": sym, "error": "데이터를 불러올 수 없음"})
            rows.append({**p, "price": None, "status": "데이터를 불러올 수 없음"})
            log.info("portfolio quote failed %s: %s", sym, e)
            continue
        ccy = q.currency or p.get("currency") or "USD"
        mv_local = q.price * p["quantity"]
        cost_local = p["avg_cost"] * p["quantity"]
        try:
            pr = profile(sym)
        except Exception:
            pr = {}
        grp = macro_scoring.map_group(pr.get("sector"), pr.get("industry"))
        rows.append({**p, "price": q.price, "currency": ccy, "change_pct": q.change_pct, "market_value_local": mv_local,
                     "cost_local": cost_local, "market_value": _to_base(mv_local, ccy, base, usdkrw),
                     "cost": _to_base(cost_local, ccy, base, usdkrw), "unrealized_pl_local": mv_local - cost_local,
                     "unrealized_pl_pct": (q.price / p["avg_cost"] - 1) * 100 if p["avg_cost"] else None,
                     "avg_cost_estimated": bool(p.get("avg_cost_estimated")),
                     "sector": pr.get("sector"), "group": grp, "country": "Korea" if sym.endswith((".KS", ".KQ")) else (pr.get("country") or "United States"),
                     "name": pr.get("name") or sym, "name_ko": pr.get("name_ko"), "delay_minutes": q.delay_minutes})
    valued = [r for r in rows if r.get("market_value") is not None]
    total = sum(r["market_value"] for r in valued)
    for r in valued:
        r["weight"] = r["market_value"] / total * 100 if total else None
        r["unrealized_pl"] = r["market_value"] - r["cost"] if r.get("cost") is not None else None
        r.update(krw_breakdown(r, usdkrw))
        if r["avg_cost_estimated"]:
            # The stand-in cost is a reference price, not what was paid: report the move since then
            # separately and never as a return.
            r["since_ref_pct"] = r["unrealized_pl_pct"]
            r["unrealized_pl"] = r["unrealized_pl_pct"] = r["unrealized_pl_local"] = None
    return rows, errors, usdkrw


_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def parse_cost_lines(text: str, with_krw: bool = False) -> list[tuple]:
    """'NVDL 29.85', '엔비디아: $180.2', '005930 61,200원' -> (identifier, price, line).

    With `with_krw`, a line may carry two prices - the local-currency average then the KRW
    average ('NVDL $30.03 43,202원') - and the tuple gains a 4th item (KRW average or None).
    Numbers inside the name (e.g. 'TOP3') are kept as part of the identifier.
    """
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # trailing price tokens: numbers separated only by spaces / currency marks at the end of the line
        tail = re.search(r"((?:[\s:=|,$₩원]*[-+]?\d[\d,]*(?:\.\d+)?[\s$₩원]*){1,2})$", line)
        nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", tail.group(1))] if tail else []
        ident = line[:tail.start()].strip(" \t:=$₩,-|") if tail else line
        if not ident and nums:  # a bare code like '005930 61200'
            ident, nums = str(int(nums[0])).zfill(6) if len(nums) > 1 else line, nums[1:]
        price = nums[0] if nums and nums[0] > 0 else None
        krw = nums[1] if len(nums) > 1 and nums[1] > 0 else None
        out.append((ident, price, line, krw) if with_krw else (ident, price, line))
    return out


def krw_breakdown(r: dict, usdkrw: float | None) -> dict:
    """KRW view of one holding: total P/L = price P/L (at today's rate) + FX P/L (환차손익).

    FX P/L = qty × avg cost(USD) × (today's rate − average purchase rate), where the average purchase
    rate is avg_cost_krw / avg_cost. Needs both average costs; KRW holdings have no FX part.
    """
    if r.get("avg_cost_estimated") or r.get("price") is None:
        return {}
    qty, avg, price = r["quantity"], r["avg_cost"], r["price"]
    if r.get("currency") == "KRW":
        cost = avg * qty
        return {"value_krw": price * qty, "cost_krw": cost, "pl_krw": (price - avg) * qty, "price_pl_krw": (price - avg) * qty,
                "fx_pl_krw": 0.0, "ret_krw_pct": (price / avg - 1) * 100 if avg else None}
    if r.get("currency") != "USD" or not usdkrw:
        return {}
    out = {"ret_usd_pct": (price / avg - 1) * 100 if avg else None, "price_pl_krw": qty * (price - avg) * usdkrw,
           "value_krw": qty * price * usdkrw}
    akrw = r.get("avg_cost_krw")
    if akrw:
        cost = akrw * qty
        out.update({"cost_krw": cost, "avg_fx": akrw / avg if avg else None, "fx_now": usdkrw,
                    "pl_krw": out["value_krw"] - cost, "fx_pl_krw": qty * avg * usdkrw - cost,
                    "ret_krw_pct": (out["value_krw"] / cost - 1) * 100 if cost else None})
    return out


def summarize(rows: list[dict]) -> dict | None:
    """Portfolio totals. Cost and P/L only over positions with a real average cost."""
    valued = [r for r in rows if r.get("market_value") is not None]
    if not valued:
        return None
    known = [r for r in valued if not r.get("avg_cost_estimated") and r.get("cost") is not None]
    mv_known = sum(r["market_value"] for r in known)
    cost = sum(r["cost"] for r in known)
    est = [r for r in valued if r.get("avg_cost_estimated")]
    krw = [r for r in valued if r.get("cost_krw") is not None]
    krw_tot = None
    if krw:
        v, c = sum(r["value_krw"] for r in krw), sum(r["cost_krw"] for r in krw)
        krw_tot = {"value": v, "cost": c, "pl": v - c, "ret_pct": (v / c - 1) * 100 if c else None,
                   "price_pl": sum(r["price_pl_krw"] for r in krw), "fx_pl": sum(r.get("fx_pl_krw") or 0 for r in krw),
                   "count": len(krw)}
    return {"market_value": sum(r["market_value"] for r in valued),
            "krw": krw_tot,
            "cost": cost if known else None,
            "unrealized_pl": (mv_known - cost) if known else None,
            "unrealized_pl_pct": (mv_known / cost - 1) * 100 if known and cost else None,
            "pl_basis_count": len(known), "estimated_count": len(est),
            "estimated_value": sum(r["market_value"] for r in est)}


def analyze(positions: list[dict], base: str = "USD", max_dd: float | None = None) -> dict:
    if not positions:
        return {"positions": [], "totals": None, "message": "보유 종목이 없습니다"}
    rows, errors, usdkrw = _value_rows(positions, base)
    valued = [r for r in rows if r.get("market_value") is not None]

    def expo(key):
        d: dict[str, float] = {}
        for r in valued:
            d[r.get(key) or "Unknown"] = d.get(r.get(key) or "Unknown", 0) + r["weight"]
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))

    risk = _risk(valued, base, max_dd)
    return clean({"base_currency": base, "usdkrw": usdkrw, "positions": rows, "errors": errors,
                  "totals": summarize(rows),
                  "exposure": {"sector": expo("sector"), "group": expo("group"), "country": expo("country"),
                               "currency": expo("currency")},
                  "risk": risk, "updated_at": utcnow().isoformat()})


def actions(positions: list[dict], base: str, profile_: dict,
            fetch: Callable[[str], tuple[dict, dict]]) -> dict:
    """Per-holding opinion (강력 매수 ~ 매도 추천) with its reasons. `fetch(symbol)` -> (technicals, scores)."""
    if not positions:
        return {"items": [], "counts": {}, "method": position_advice.METHOD, "updated_at": utcnow().isoformat()}
    rows, _, _ = _value_rows(positions, base)

    def one(r: dict) -> dict:
        tech = sc = None
        if r.get("price") is not None:
            try:
                tech, sc = fetch(r["symbol"])
            except Exception as e:
                log.info("portfolio action failed %s: %s", r["symbol"], e)
        an = None
        if r.get("price") is not None:
            try:
                from . import analyst
                an = analyst.view_quick(r["symbol"])
            except Exception as e:
                log.info("analyst view failed %s: %s", r["symbol"], e)
        adv = position_advice.advise(r, sc, tech, profile_, an)
        return {"position_id": r["id"], "name": r.get("name"), **adv}

    with ThreadPoolExecutor(max_workers=6) as ex:
        items = list(ex.map(one, rows))
    counts: dict[str, int] = {}
    for it in items:
        counts[it["action"]] = counts.get(it["action"], 0) + 1
    return clean({"items": items, "counts": counts, "labels": position_advice.LABEL, "method": position_advice.METHOD,
                  "profile": {k: profile_.get(k) for k in ("holding_period", "risk_tolerance", "max_drawdown")},
                  "disclaimer": "규칙 기반 의견이며 투자 권유가 아닙니다. 과거 데이터에 규칙을 적용한 결과로 미래 수익을 보장하지 않습니다.",
                  "updated_at": utcnow().isoformat()})


def _returns(symbols: list[str], years: int = 1) -> pd.DataFrame:
    cols = {}
    for s in symbols:
        try:
            c = prices.get_daily(s, years=max(3, years)).df["adj_close"]
            cols[s] = c.pct_change()
        except Exception:
            continue
    df = pd.DataFrame(cols).dropna(how="all")
    return df[df.index >= df.index[-1] - pd.DateOffset(years=years)] if len(df) else df


def _risk(valued: list[dict], base: str, max_dd: float | None) -> dict:
    if not valued:
        return {}
    w = {r["symbol"]: r["weight"] / 100 for r in valued}
    syms = list(w)
    rets = _returns(syms + ["SPY", "BTC-USD"], years=1)
    out: dict = {"flags": []}
    hold = [s for s in syms if s in rets]
    if hold:
        R = rets[hold].fillna(0)
        port = (R * pd.Series({s: w[s] for s in hold})).sum(axis=1)
        out["volatility"] = float(port.std() * np.sqrt(252))
        eq = (1 + port).cumprod()
        out["max_drawdown_1y"] = float((eq / eq.cummax() - 1).min())
        if "SPY" in rets:
            spy = rets["SPY"].reindex(port.index).fillna(0)
            out["beta"] = float(np.cov(port, spy)[0, 1] / spy.var()) if spy.var() > 0 else None
            out["betas"] = {s: float(np.cov(R[s], spy)[0, 1] / spy.var()) for s in hold} if spy.var() > 0 else {}
        if "BTC-USD" in rets:
            out["crypto_correlation"] = float(port.corr(rets["BTC-USD"].reindex(port.index)))
        corr = R.corr()
        out["correlation"] = {"assets": hold, "matrix": [[None if pd.isna(v) else round(float(v), 3) for v in row]
                                                         for row in corr.to_numpy()]}
    tech = sum(r["weight"] for r in valued if r.get("group") in TECH_GROUPS)
    defensive = sum(r["weight"] for r in valued if r.get("group") in DEFENSIVE)
    usd = sum(r["weight"] for r in valued if r.get("currency") == "USD")
    top = max(valued, key=lambda r: r["weight"])

    def lvl(v, hi, med):
        return "High" if v >= hi else "Medium" if v >= med else "Low"
    flags = [
        {"risk": "기술주 집중도", "level": lvl(tech, 40, 20), "value": f"기술·반도체·소프트웨어·커뮤니케이션 {tech:.0f}%"},
        {"risk": "단일 종목 집중도", "level": lvl(top["weight"], 25, 12), "value": f"{top['symbol']} {top['weight']:.0f}%"},
        {"risk": "달러 노출" if base != "USD" else "비달러(환율) 노출", "level": lvl(usd, 70, 30) if base != "USD" else lvl(100 - usd, 70, 30),
         "value": f"달러 자산 {usd:.0f}%" + ("" if base != "USD" else f", 비달러 {100 - usd:.0f}%")},
        {"risk": "방어주 부족", "level": "High" if defensive < 10 else "Medium" if defensive < 25 else "Low",
         "value": f"필수소비재·유틸리티·헬스케어 {defensive:.0f}%"},
    ]
    st = stress_test(valued, SCENARIOS["rates_spike"])
    if st.get("portfolio_impact_pct") is not None:
        imp = st["portfolio_impact_pct"]
        flags.append({"risk": "금리 민감도", "level": "High" if imp <= -4 else "Medium" if imp <= -2 else "Low",
                      "value": f"10년물 +50bp 시나리오 시 추정 {imp:+.1f}%"})
    cc = out.get("crypto_correlation")
    if cc is not None:
        flags.append({"risk": "가상자산 상관관계", "level": lvl(cc, 0.5, 0.3), "value": f"비트코인과 {cc:.2f} (1년 일간)"})
    if max_dd is not None and out.get("max_drawdown_1y") is not None:
        breach = abs(out["max_drawdown_1y"]) > max_dd
        flags.append({"risk": "허용 낙폭 대비", "level": "High" if breach else "Low",
                      "value": f"1년 최대낙폭 {out['max_drawdown_1y']:.1%} / 허용 -{max_dd:.0%}"})
    out["flags"] = flags
    return out


def _betas(symbols: list[str]) -> dict[str, dict]:
    ff = macro.factor_frame(3)
    out = {}
    for s in symbols:
        try:
            c = prices.get_daily(s, years=5).df["adj_close"]
            b = macro_scoring.factor_betas(c, ff, years=3)
            if b:
                out[s] = b
        except Exception:
            continue
    return out


def stress_test(valued: list[dict], shocks: dict) -> dict:
    syms = [r["symbol"] for r in valued]
    betas = _betas(syms + [s for s, _ in CONTEXT_ASSETS])
    usd_shock = shocks.get("usd", 0.0)

    def impact(sym: str, ccy_local: str | None, base: str = "USD") -> float | None:
        b = betas.get(sym)
        if not b:
            return None
        bb = b["betas"]
        r = (bb["market"] * shocks.get("market", 0) / 100 + bb["rate_bp"] * shocks.get("rate_bp", 0)
             + bb["usd"] * shocks.get("usd", 0) / 100 + bb["oil"] * shocks.get("oil", 0) / 100) * 100
        if ccy_local == "KRW" and base == "USD":
            r -= usd_shock  # translation: assume USD/KRW moves with the broad dollar (documented simplification)
        return r
    rows, tot, covered = [], 0.0, 0.0
    for r in valued:
        imp = impact(r["symbol"], r.get("currency"))
        rows.append({"symbol": r["symbol"], "weight": r.get("weight"), "impact_pct": imp,
                     "r2": betas.get(r["symbol"], {}).get("r2")})
        if imp is not None and r.get("weight"):
            tot += imp * r["weight"] / 100
            covered += r["weight"]
    ctx = [{"asset": label, "proxy": s, "impact_pct": impact(s, "USD")} for s, label in CONTEXT_ASSETS]
    return clean({"scenario": shocks, "positions": rows, "portfolio_impact_pct": tot if covered else None,
                  "coverage_pct": covered, "context_assets": ctx,
                  "method": "다중 팩터 회귀(3년 일간): 수익률 ~ 시장% + 10년물 bp + 달러% + 유가%. 과거 민감도 기반 추정이며 예측이 아닙니다."})
