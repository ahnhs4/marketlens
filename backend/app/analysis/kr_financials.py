"""Korean financial statements (OpenDART) -> normalised periods -> metrics -> score, all pure functions.

Periods are keyed (year, report code): 11013 = Q1, 11012 = half-year, 11014 = Q3, 11011 = annual. Income-statement
lines carry the quarter (`q`), the year-to-date (`ytd`) and the same figures a year earlier (`q_prev`, `ytd_prev`);
balance-sheet lines carry the balance (`v`) and the previous fiscal year-end (`prev_fy`); cash-flow lines are YTD.
  TTM          = last annual + this year's YTD - last year's same-period YTD   (annual report: the year itself)
  growth       = YTD vs the same YTD a year earlier (누적 기준 YoY)
  quarter Q4   = annual - Q3 YTD
The score ranks each metric against every listed company that has it (percentile, 0-100) and averages pillars;
missing metrics are left out, never guessed.
"""
from __future__ import annotations

import math
import re

import numpy as np

MONTHS = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}
QUARTER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}

# fnlttMultiAcnt account names (parentheses and spaces removed) -> key
MAIN = {"유동자산": "current_assets", "비유동자산": "noncurrent_assets", "자산총계": "assets", "유동부채": "current_liabilities",
        "비유동부채": "noncurrent_liabilities", "부채총계": "liabilities", "자본금": "capital", "이익잉여금": "retained_earnings",
        "자본총계": "equity", "매출액": "revenue", "영업이익": "operating_income", "법인세차감전순이익": "pretax_income",
        "당기순이익": "net_income"}
IS_KEYS = {"revenue", "operating_income", "pretax_income", "net_income", "net_income_parent", "gross_profit"}
CF_KEYS = {"ocf", "capex", "capex_intangible", "dividends_paid", "buyback"}

# fnlttSinglAcntAll: IFRS account ids first, then account names as a fallback (company-specific accounts have no id)
FULL = {
    "assets": (["ifrs-full_Assets"], ["자산총계"]),
    "current_assets": (["ifrs-full_CurrentAssets"], ["유동자산"]),
    "liabilities": (["ifrs-full_Liabilities"], ["부채총계"]),
    "current_liabilities": (["ifrs-full_CurrentLiabilities"], ["유동부채"]),
    "equity": (["ifrs-full_Equity"], ["자본총계"]),
    "equity_parent": (["ifrs-full_EquityAttributableToOwnersOfParent"], ["지배기업의소유주에게귀속되는자본", "지배기업소유주지분"]),
    "cash": (["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산"]),
    "revenue": (["ifrs-full_Revenue"], ["매출액", "수익", "영업수익", "매출"]),
    "gross_profit": (["ifrs-full_GrossProfit"], ["매출총이익"]),
    "operating_income": (["dart_OperatingIncomeLoss"], ["영업이익"]),
    "net_income": (["ifrs-full_ProfitLoss"], ["당기순이익"]),
    "net_income_parent": (["ifrs-full_ProfitLossAttributableToOwnersOfParent"], ["지배기업의소유주에게귀속되는당기순이익",
                                                                              "지배기업소유주지분"]),
    "ocf": (["ifrs-full_CashFlowsFromUsedInOperatingActivities"], ["영업활동현금흐름", "영업활동으로인한현금흐름"]),
    "capex": (["ifrs-full_PurchaseOfPropertyPlantAndEquipment"], ["유형자산의취득"]),
    "capex_intangible": (["ifrs-full_PurchaseOfIntangibleAssets"], ["무형자산의취득"]),
    "dividends_paid": (["ifrs-full_DividendsPaidClassifiedAsFinancingActivities"], ["배당금의지급", "배당금지급"]),
    "buyback": (["ifrs-full_PaymentsToAcquireOrRedeemEntitysShares"], ["자기주식의취득"]),
}
FINANCIAL_WORDS = ("은행", "보험", "증권", "금융", "신탁", "집합투자", "여신", "카드", "캐피탈", "지주회사")


def amt(x) -> float | None:
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _norm(nm: str | None) -> str:
    return re.sub(r"\(.*?\)|\s|ㆍ|·", "", nm or "")


def _end(text: str | None) -> str | None:
    d = re.findall(r"(\d{4})\.(\d{2})\.(\d{2})", text or "")
    return f"{d[-1][0]}-{d[-1][1]}-{d[-1][2]}" if d else None


def _is_vals(r: dict, reprt: str, full: bool = False) -> dict:
    cur, add = amt(r.get("thstrm_amount")), amt(r.get("thstrm_add_amount"))
    prev, padd = amt(r.get("frmtrm_amount")), amt(r.get("frmtrm_add_amount"))
    if reprt == "11011":
        return {"ytd": cur, "ytd_prev": prev, "ytd_prev2": amt(r.get("bfefrmtrm_amount"))}
    q_prev = amt(r.get("frmtrm_q_amount")) if full else prev
    if reprt == "11013":
        return {"q": cur, "ytd": add if add is not None else cur, "q_prev": q_prev, "ytd_prev": padd if padd is not None else q_prev}
    return {"q": cur, "ytd": add, "q_prev": q_prev, "ytd_prev": padd}


def parse_main(rows: list[dict]) -> dict[str, dict]:
    """fnlttMultiAcnt rows -> {stock_code: period data}. Consolidated (CFS) when the company files one, else separate."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("stock_code") or "", []).append(r)
    out = {}
    for code, rs in by.items():
        if not code:
            continue
        fs = "CFS" if any(r.get("fs_div") == "CFS" for r in rs) else "OFS"
        d: dict = {"fs": fs, "bs": {}, "is": {}, "end": None, "rcept_no": None, "currency": None}
        for r in rs:
            if r.get("fs_div") != fs:
                continue
            key = MAIN.get(_norm(r.get("account_nm")))
            if not key:
                continue
            reprt = r.get("reprt_code") or "11011"
            d["rcept_no"] = d["rcept_no"] or r.get("rcept_no")
            d["currency"] = d["currency"] or r.get("currency")
            if r.get("sj_div") == "BS":
                d["end"] = d["end"] or _end(r.get("thstrm_dt"))
                d["bs"].setdefault(key, {"v": amt(r.get("thstrm_amount")), "prev_fy": amt(r.get("frmtrm_amount"))})
            else:
                d["is"].setdefault(key, _is_vals(r, reprt))
        out[code] = d
    return out


def parse_full(rows: list[dict], reprt: str) -> dict:
    """fnlttSinglAcntAll rows (one company, one period) -> period data with parent-company figures and cash flow."""
    d: dict = {"fs": None, "bs": {}, "is": {}, "cf": {}, "rcept_no": None, "currency": None}
    order = {"BS": 0, "IS": 1, "CIS": 2, "CF": 3}
    rows = sorted(rows, key=lambda r: (order.get(r.get("sj_div"), 9), int(r.get("ord") or 0)))
    for key, (ids, names) in FULL.items():
        sec = "cf" if key in CF_KEYS else "is" if key in IS_KEYS else "bs"
        sj = ("CF",) if sec == "cf" else ("IS", "CIS") if sec == "is" else ("BS",)
        cand = [r for r in rows if r.get("sj_div") in sj]
        hit = next((r for r in cand if r.get("account_id") in ids), None)
        if hit is None:
            nn = [_norm(n) for n in names]
            hit = next((r for r in cand if _norm(r.get("account_nm")) in nn), None)
        if hit is None:
            continue
        d["rcept_no"] = d["rcept_no"] or hit.get("rcept_no")
        d["currency"] = d["currency"] or hit.get("currency")
        if sec == "bs":
            d["bs"][key] = {"v": amt(hit.get("thstrm_amount")), "prev_fy": amt(hit.get("frmtrm_amount"))}
        elif sec == "is":
            d["is"][key] = _is_vals(hit, reprt, full=True)
        else:
            v, p = amt(hit.get("thstrm_amount")), amt(hit.get("frmtrm_amount"))
            if key != "ocf":       # outflows are filed with either sign
                v, p = (abs(v) if v is not None else None), (abs(p) if p is not None else None)
            d["cf"][key] = {"ytd": v, "ytd_prev": p}
    return d


def parse_dividends(rows: list[dict]) -> dict:
    """alotMatter rows -> common-share DPS / yield / payout for this and the previous two years."""
    out: dict = {}
    for r in rows:
        se, knd = _norm(r.get("se")), (r.get("stock_knd") or "").strip()
        key = ("dps" if "주당현금배당금" in se else "yield" if "현금배당수익률" in se else
               "payout" if "현금배당성향" in se else "total" if "현금배당금총액" in se else None)
        if not key or (key in ("dps", "yield") and knd and knd != "보통주"):
            continue
        mult = 1e6 if key == "total" else 1
        vals = [amt(r.get(c)) for c in ("thstrm", "frmtrm", "lwfr")]
        out.setdefault(key, [v * mult if v is not None else None for v in vals])
    return out


# ----------------------------------------------------------------------------- periods -> figures
def _months(k: tuple[int, str]) -> int:
    return k[0] * 12 + MONTHS[k[1]]


def latest(periods: dict) -> tuple[int, str] | None:
    have = [k for k, v in periods.items() if v and (v.get("is") or v.get("bs"))]
    return max(have, key=_months) if have else None


def _g(periods: dict, k, sec: str, key: str, field: str) -> float | None:
    return ((((periods.get(k) or {}).get(sec) or {}).get(key)) or {}).get(field)


def ttm(periods: dict, key: str, at: tuple[int, str] | None = None, sec: str = "is") -> float | None:
    """Trailing twelve months of an income-statement or cash-flow line, ending at `at` (default: latest period)."""
    at = at or latest(periods)
    if not at:
        return None
    y, r = at
    if r == "11011":
        return _g(periods, at, sec, key, "ytd")
    fy = _g(periods, (y - 1, "11011"), sec, key, "ytd")
    ytd = _g(periods, at, sec, key, "ytd")
    prev = _g(periods, at, sec, key, "ytd_prev")
    if prev is None:
        prev = _g(periods, (y - 1, r), sec, key, "ytd")
    return fy + ytd - prev if None not in (fy, ytd, prev) else None


def yoy(periods: dict, key: str, at: tuple[int, str] | None = None, sec: str = "is") -> tuple[float | None, float | None]:
    """(this YTD, same YTD a year earlier)."""
    at = at or latest(periods)
    if not at:
        return None, None
    cur, prev = _g(periods, at, sec, key, "ytd"), _g(periods, at, sec, key, "ytd_prev")
    if prev is None:
        prev = _g(periods, (at[0] - 1, at[1]), sec, key, "ytd")
    return cur, prev


def growth(cur: float | None, prev: float | None) -> dict | None:
    """Growth with the sign cases Korean reports name explicitly (흑자전환 / 적자전환 / 적자지속)."""
    if cur is None or prev is None:
        return None
    if prev > 0:
        return {"state": "흑자" if cur > 0 else "적자전환", "pct": (cur / prev - 1) * 100}
    if cur > 0:
        return {"state": "흑자전환", "pct": None}
    return {"state": "적자지속" if cur <= prev else "적자축소", "pct": None}


def quarters(periods: dict, keys=("revenue", "operating_income", "net_income"), n: int = 12) -> list[dict]:
    """Quarter-by-quarter figures (Q4 = annual - Q3 YTD), oldest first."""
    years = sorted({y for y, _ in periods})
    out = []
    for y in years:
        for r in ("11013", "11012", "11014", "11011"):
            if not periods.get((y, r)):
                continue
            row = {"year": y, "quarter": QUARTER[r], "label": f"{str(y)[2:]}.{QUARTER[r]}Q", "end": periods[(y, r)].get("end")}
            for k in keys:
                if r == "11011":
                    fy, q3 = _g(periods, (y, r), "is", k, "ytd"), _g(periods, (y, "11014"), "is", k, "ytd")
                    row[k] = fy - q3 if fy is not None and q3 is not None else None
                else:
                    row[k] = _g(periods, (y, r), "is", k, "q")
                    if row[k] is None and r != "11013":
                        prev_r = {"11012": "11013", "11014": "11012"}[r]
                        a, b = _g(periods, (y, r), "is", k, "ytd"), _g(periods, (y, prev_r), "is", k, "ytd")
                        row[k] = a - b if a is not None and b is not None else None
            if any(row.get(k) is not None for k in keys):
                out.append(row)
    return out[-n:]


def annuals(periods: dict, n: int = 5) -> list[dict]:
    """Fiscal years, oldest first, filling earlier years from the comparative columns of later annual reports."""
    rows: dict[int, dict] = {}
    for (y, r), d in sorted(periods.items()):
        if r != "11011" or not d:
            continue
        for off, f in ((0, "ytd"), (1, "ytd_prev"), (2, "ytd_prev2")):
            row = rows.setdefault(y - off, {"year": y - off})
            for k in ("revenue", "operating_income", "net_income", "net_income_parent"):
                v = _g(periods, (y, r), "is", k, f)
                if v is not None and (off == 0 or row.get(k) is None):
                    row[k] = v
        for off, f in ((0, "v"), (1, "prev_fy")):
            row = rows.setdefault(y - off, {"year": y - off})
            for k in ("assets", "liabilities", "equity", "equity_parent"):
                v = _g(periods, (y, r), "bs", k, f)
                if v is not None and (off == 0 or row.get(k) is None):
                    row[k] = v
        for k in ("ocf", "capex"):
            v = _g(periods, (y, r), "cf", k, "ytd")
            if v is not None:
                rows[y][k] = v
    out = []
    for y in sorted(rows)[-n:]:
        x = rows[y]
        eq = x.get("equity_parent") or x.get("equity")
        ni = x.get("net_income_parent") if x.get("net_income_parent") is not None else x.get("net_income")
        x["op_margin"] = _ratio(x.get("operating_income"), x.get("revenue"))
        x["roe"] = _ratio(ni, eq) if eq and eq > 0 else None
        x["debt_ratio"] = _ratio(x.get("liabilities"), x.get("equity")) if x.get("equity") and x["equity"] > 0 else None
        x["fcf"] = x["ocf"] - x["capex"] if x.get("ocf") is not None and x.get("capex") is not None else None
        out.append(x)
    return out


def _ratio(a, b) -> float | None:
    return a / b * 100 if a is not None and b not in (None, 0) else None


def metrics(main: dict, full: dict | None = None, market_cap: float | None = None, dividends: dict | None = None,
            close: float | None = None, financial: bool = False) -> dict:
    """Every metric the score and the page use. `full` (parent-company figures, cash flow) is used when it covers
    the same latest period as `main`; percentages are in %, money in KRW."""
    at = latest(main)
    if not at:
        return {"available": False}
    use_full = bool(full) and latest(full) == at
    src = full if use_full else main
    m: dict = {"available": True, "period": {"year": at[0], "reprt": at[1], "end": (main.get(at) or {}).get("end"),
                                             "months": MONTHS[at[1]]},
               "basis": "parent" if use_full else "total", "fs": (main.get(at) or {}).get("fs"), "financial": financial}
    for k in ("revenue", "operating_income", "net_income"):
        m[f"{k}_ttm"] = ttm(src, k, at)
    ni_parent = ttm(src, "net_income_parent", at) if use_full else None
    m["net_income_parent_ttm"] = ni_parent
    ni = ni_parent if ni_parent is not None else m["net_income_ttm"]
    bs = lambda k, f="v": _g(src, at, "bs", k, f)  # noqa: E731
    eq_total = bs("equity")
    eq = bs("equity_parent") if use_full and bs("equity_parent") is not None else eq_total
    eq_prev = (bs("equity_parent", "prev_fy") if use_full and bs("equity_parent", "prev_fy") is not None
               else bs("equity", "prev_fy"))
    m.update(equity=eq, equity_total=eq_total, assets=bs("assets"), liabilities=bs("liabilities"))
    avg_eq = (eq + eq_prev) / 2 if eq is not None and eq_prev is not None and eq_prev > 0 else eq
    m["roe"] = ni / avg_eq * 100 if ni is not None and avg_eq and avg_eq > 0 else None
    m["roa"] = _ratio(ni, bs("assets"))
    m["op_margin"] = _ratio(m["operating_income_ttm"], m["revenue_ttm"]) if (m["revenue_ttm"] or 0) > 0 else None
    m["debt_ratio"] = _ratio(bs("liabilities"), eq_total) if eq_total and eq_total > 0 else None
    m["current_ratio"] = _ratio(bs("current_assets"), bs("current_liabilities"))
    m["capital_impaired"] = eq_total is not None and eq_total <= 0
    for k, name in (("revenue", "rev"), ("operating_income", "op"), ("net_income", "ni")):
        cur, prev = yoy(main, k, at)
        m[f"{name}_ytd"], m[f"{name}_ytd_prev"] = cur, prev
        m[f"{name}_growth"] = growth(cur, prev)
    rev_by_year = {x["year"]: x.get("revenue") for x in annuals(main, n=6) if x.get("revenue")}
    if rev_by_year:
        last = max(rev_by_year)
        base = next((y for y in (last - 3, last - 2) if rev_by_year.get(y)), None)
        n = last - base if base else None
        m["rev_cagr_years"] = n
        r_now, r_0 = rev_by_year[last], rev_by_year.get(base) if base else None
        m["rev_cagr"] = ((r_now / r_0) ** (1 / n) - 1) * 100 if n and r_now > 0 and r_0 and r_0 > 0 else None
    if use_full:
        m["ocf_ttm"] = ttm(src, "ocf", at, sec="cf")
        capex = ttm(src, "capex", at, sec="cf")
        m["capex_ttm"] = capex
        m["fcf_ttm"] = m["ocf_ttm"] - capex if m["ocf_ttm"] is not None and capex is not None else None
        m["ocf_to_ni"] = m["ocf_ttm"] / ni if m["ocf_ttm"] is not None and ni and ni > 0 else None
        m["buyback_ttm"] = ttm(src, "buyback", at, sec="cf")
        m["dividends_paid_ttm"] = ttm(src, "dividends_paid", at, sec="cf")
    # Profit that operations didn't earn (asset sales, valuation gains): net profit above twice operating profit, or a
    # net profit with an operating loss. Holding companies' equity-method income stays under 2x in practice.
    op_t = m["operating_income_ttm"]
    m["one_off"] = bool(not financial and ni is not None and ni > 0 and op_t is not None and (op_t <= 0 or ni > 2 * op_t))
    fy_rows = [x for x in annuals(main, n=3) if x.get("roe") is not None]
    m["roe_last_fy"] = fy_rows[-1]["roe"] if fy_rows else None
    m["market_cap"] = market_cap
    m["pbr"] = market_cap / eq if market_cap and eq and eq > 0 else None
    m["per"] = market_cap / ni if market_cap and ni and ni > 0 else None
    m["psr"] = market_cap / m["revenue_ttm"] if market_cap and (m["revenue_ttm"] or 0) > 0 else None
    m["fcf_yield"] = m["fcf_ttm"] / market_cap * 100 if market_cap and m.get("fcf_ttm") is not None else None
    if dividends:
        dps = (dividends.get("dps") or [None])[0]
        m["dps"] = dps
        m["dps_history"] = dividends.get("dps")
        m["payout"] = (dividends.get("payout") or [None])[0]
        m["div_yield"] = dps / close * 100 if dps is not None and close else (dividends.get("yield") or [None])[0]
    return m


# ----------------------------------------------------------------------------- score
# (key, label, direction, unit, weight inside the pillar); direction +1 = higher is better.
# Weights = half the original judgement, half the 2017-2025 Korean backtest (tools/kr_factor_study.py: rank IC of each
# metric with the next year's excess return, May formation, KOSPI + KOSDAQ): PBR 0.15 > PER 0.08 > op margin 0.07 ~ ROE
# 0.07 ~ op-profit growth 0.06 > stability 0.03 > revenue growth / CAGR ~0. Cash-flow and dividend metrics need full
# statements per company, so they are not in the backtest and keep their original weight.
PILLARS = {
    "profitability": ("수익성", 0.26, [("roe", "ROE", 1, "%", 0.5), ("op_margin", "영업이익률", 1, "%", 0.5)]),
    "growth": ("성장성", 0.15, [("rev_growth", "매출 성장률 (누적 YoY)", 1, "%", 0.2), ("op_growth", "영업이익 성장률 (누적 YoY)", 1, "%", 0.7),
                              ("rev_cagr", "매출 연평균 성장률", 1, "%", 0.1)]),
    "stability": ("재무 안정성", 0.12, [("debt_ratio", "부채비율", -1, "%", 0.5), ("current_ratio", "유동비율", 1, "%", 0.5)]),
    "valuation": ("밸류에이션", 0.33, [("pbr", "PBR", -1, "배", 0.65), ("per", "PER", -1, "배", 0.35)]),
    "cash_return": ("현금흐름·주주환원", 0.14, [("ocf_to_ni", "영업현금흐름 / 순이익", 1, "배", 1 / 3),
                                          ("fcf_yield", "FCF 수익률", 1, "%", 1 / 3), ("div_yield", "배당수익률", 1, "%", 1 / 3)]),
}
NOT_FOR_FINANCIALS = {"op_margin", "debt_ratio", "current_ratio", "ocf_to_ni", "fcf_yield"}


def metric_value(m: dict, key: str) -> float | None:
    if key in ("rev_growth", "op_growth"):
        g = m.get(key)
        return g.get("pct") if g else None
    return m.get(key)


def distributions(rows: list[dict]) -> dict[str, np.ndarray]:
    """Sorted values of every scored metric across the market (financials excluded where the metric means nothing)."""
    out = {}
    for _, (_, _, items) in PILLARS.items():
        for key, *_ in items:
            vals = [metric_value(r, key) for r in rows
                    if r.get("available") and not (r.get("financial") and key in NOT_FOR_FINANCIALS)]
            vals = [v for v in vals if v is not None and math.isfinite(v)]
            if key in ("pbr", "per"):
                vals = [v for v in vals if v > 0]
            out[key] = np.sort(np.array(vals, dtype=float))
    return out


def percentile(dist: np.ndarray, x: float) -> float | None:
    if dist is None or len(dist) < 20:
        return None
    lo, hi = np.searchsorted(dist, x, "left"), np.searchsorted(dist, x, "right")
    return float((lo + hi) / 2 / len(dist) * 100)


def score(m: dict, dist: dict[str, np.ndarray]) -> dict:
    """0-100 per metric = percentile among listed companies (flipped where lower is better); pillars average their
    metrics; the total weights the pillars that have data."""
    if not m.get("available"):
        return {"score": None, "pillars": {}, "missing": [], "note": "DART 재무 데이터 없음"}
    pillars, missing = {}, []
    for pk, (pname, w, items) in PILLARS.items():
        scored = []
        for key, label, direction, unit, iw in items:
            if m.get("financial") and key in NOT_FOR_FINANCIALS:
                continue
            v = metric_value(m, key)
            s, note = None, None
            if key in ("rev_growth", "op_growth") and m.get(key) and v is None:
                state = m[key]["state"]
                s, note = {"흑자전환": 85.0, "적자축소": 30.0, "적자지속": 10.0}.get(state, 10.0), state
            elif key == "op_growth" and m.get(key) and m[key]["state"] == "적자전환":
                s, note = 5.0, "적자전환"
            elif key == "pbr" and m.get("capital_impaired"):
                s, note = 0.0, "자본잠식"
            elif key == "per" and v is None and m.get("market_cap") and (m.get("net_income_ttm") or 0) <= 0:
                missing.append(f"{label} (적자라 산출 불가)")
                continue
            elif key == "ocf_to_ni" and v is None and m.get("ocf_ttm") is not None:
                ni = m.get("net_income_parent_ttm") or m.get("net_income_ttm") or 0
                if ni <= 0:
                    s, note = (55.0, "순손실이지만 영업현금흐름 플러스") if m["ocf_ttm"] > 0 else (10.0, "순손실·영업현금 유출")
            elif key == "ocf_to_ni" and v is not None:
                s = float(np.clip((v - 0.3) / (1.2 - 0.3) * 100, 0, 100))   # 0.3x -> 0, 1.2x+ -> 100 (accrual quality)
            elif v is not None:
                p = percentile(dist.get(key), v)
                if p is not None:
                    s = p if direction > 0 else 100 - p
            if s is None:
                missing.append(label)
                continue
            pct = percentile(dist.get(key), v) if v is not None and key != "ocf_to_ni" else None
            scored.append({"key": key, "label": label, "value": v, "unit": unit, "score": round(s, 1), "note": note,
                           "weight": iw, "top_pct": round(100 - pct, 1) if pct is not None else None})
        tw_i = sum(i["weight"] for i in scored)
        pillars[pk] = {"label": pname, "weight": w, "items": scored,
                       "score": round(sum(i["weight"] * i["score"] for i in scored) / tw_i, 1) if scored else None}
    have = {k: p for k, p in pillars.items() if p["score"] is not None}
    if not have:
        return {"score": None, "pillars": pillars, "missing": missing}
    tw = sum(p["weight"] for p in have.values())
    total = sum(p["weight"] * p["score"] for p in have.values()) / tw
    return {"score": round(total, 1), "pillars": pillars, "missing": missing,
            "weights_used": {k: round(p["weight"] / tw, 3) for k, p in have.items()},
            "note": "각 지표를 국내 상장사 전체와 비교한 백분위(높을수록 좋게 환산)를 가중 평균했습니다. 가중치는 2017~2025년 국내 "
                    "백테스트에서 다음 1년 초과수익과의 상관이 컸던 지표(PBR·PER·영업이익률·ROE·영업이익 성장)에 더 줬습니다. "
                    "금융업은 영업이익률·부채비율·유동비율·현금흐름 지표를 빼고 계산하고, 없는 지표는 추정하지 않고 제외합니다."}


# ----------------------------------------------------------------------------- words
def won(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    s = f"{v / 1e12:,.1f}조원" if a >= 1e12 else f"{v / 1e8:,.0f}억원" if a >= 1e8 else f"{v / 1e4:,.0f}만원"
    return s


def _pct(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"{v:,.{d}f}%"


def _gclause(g: dict | None, what: str) -> str | None:
    if not g:
        return None
    st = g["state"]
    if st in ("흑자전환", "적자전환", "적자지속", "적자축소"):
        return {"흑자전환": f"{what}은 흑자로 돌아섰", "적자전환": f"{what}은 적자로 돌아섰",
                "적자지속": f"{what}은 적자가 이어졌", "적자축소": f"{what}은 적자가 줄었"}[st]
    p = g["pct"]
    return f"{what}은 {abs(p):.1f}% {'늘었' if p >= 0 else '줄었'}" if p is not None else None


def summary(m: dict, sc: dict, name: str, extras: dict | None = None) -> list[str]:
    """Plain sentences built from the numbers (no model, no adjectives the numbers don't support)."""
    if not m.get("available"):
        return []
    extras = extras or {}
    per = m["period"]
    plabel = f"{per['year']}년 " + ("연간" if per["months"] == 12 else f"{per['months']}개월 누적")
    out = []
    ni_t = m.get("net_income_parent_ttm") if m.get("net_income_parent_ttm") is not None else m.get("net_income_ttm")
    if m.get("revenue_ttm") is not None:
        out.append(f"최근 4개 분기 매출 {won(m.get('revenue_ttm'))}, 영업이익 {won(m.get('operating_income_ttm'))}"
                   + (f" (영업이익률 {_pct(m.get('op_margin'))})" if m.get("op_margin") is not None else "") + ".")
    else:   # banks / insurers file no revenue line
        out.append(f"최근 4개 분기 영업이익 {won(m.get('operating_income_ttm'))}, 순이익 {won(ni_t)}.")
    clauses = [c for c in (_gclause(m.get("rev_growth"), "매출"), _gclause(m.get("op_growth"), "영업이익")) if c]
    if clauses:
        out.append(f"{plabel} 기준 전년 같은 기간보다 " + "고, ".join(clauses) + "습니다.")
    pill = sc.get("pillars") or {}
    item = {i["key"]: i for p in pill.values() for i in p["items"]}
    if m.get("roe") is not None:
        t = item.get("roe", {}).get("top_pct")
        out.append(f"ROE {_pct(m['roe'])}" + (f"로 국내 상장사 상위 {t:.0f}% 수준" if t is not None else "") + "입니다.")
    if m.get("one_off"):
        out.append("순이익이 영업이익보다 크게 많거나 영업적자인데 순이익이 나 일회성 이익(자산 매각·평가이익 등)이 섞였을 수 있습니다. "
                   "ROE·PER은 그만큼 좋게 보입니다.")
    if m.get("capital_impaired"):
        out.append("자본총계가 0 이하인 자본잠식 상태입니다.")
    elif m.get("debt_ratio") is not None and not m.get("financial"):
        out.append(f"부채비율 {_pct(m['debt_ratio'], 0)}, 유동비율 {_pct(m.get('current_ratio'), 0)}.")
    if m.get("pbr") is not None:
        bits = [f"PBR {m['pbr']:.2f}배"]
        if m.get("per") is not None:
            bits.append(f"PER {m['per']:.1f}배")
        elif (m.get("net_income_ttm") or 0) <= 0:
            bits.append("최근 4개 분기 순손실로 PER 산출 불가")
        out.append(f"시가총액 {won(m.get('market_cap'))} 기준 " + ", ".join(bits) + ".")
    if m.get("ocf_to_ni") is not None and not m.get("financial"):   # a lender's operating cash flow is its lending, not earnings quality
        out.append(f"영업현금흐름이 순이익의 {m['ocf_to_ni']:.1f}배" + (" (이익이 현금으로 잘 들어옴)" if m["ocf_to_ni"] >= 1 else
                                                               " (이익보다 현금 유입이 적음)" if m["ocf_to_ni"] < 0.7 else "")
                   + (f", 잉여현금흐름 {won(m.get('fcf_ttm'))}" if m.get("fcf_ttm") is not None else "") + ".")
    if m.get("dps"):
        out.append(f"주당배당금 {m['dps']:,.0f}원" + (f" (배당수익률 {m['div_yield']:.2f}%" if m.get("div_yield") else "")
                   + (f", 배당성향 {m['payout']:.0f}%)" if m.get("payout") else ")" if m.get("div_yield") else "") + ".")
    if extras.get("low_pbr"):
        lp = extras["low_pbr"]
        out.append(f"거래소가 {lp['published']}에 공표한 저PBR 기업 명단에 포함됐습니다 (PBR {lp['pbr']}배, {lp['pbr_date']} 기준).")
    if extras.get("valueup"):
        v = extras["valueup"]
        out.append(f"기업가치 제고 계획을 {v['last_plan'][:10]}에 공시했습니다" + (f" (총 {v['count']}건)" if v["count"] > 1 else "") + ".")
    return out
