"""Dividend target, yearly tax estimate and target-allocation rebalancing for one portfolio (all in KRW).

* Dividends: trailing-12-month dividends per share from the price history (Yahoo actions) × quantity.
  After tax: US 15% withheld (KR-US treaty; no extra Korean tax below the 20M won financial-income
  threshold), Korea 15.4%.
* Tax (Korean resident, 2026 rules): overseas shares 22% of the year's net realized gain above 2.5M won
  (gains and losses net within the year, no carry-forward); Korean listed shares by small holders have no
  capital-gains tax but a 0.20% securities transaction tax on sales (ETFs exempt).
* Rebalancing: overweight groups sell their lowest-rated holdings first (same 판단 점수 as the opinions);
  underweight groups buy their best-rated holdings or, for dividends / index funds, rated candidates.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ..analysis import position_advice
from . import prices
from .portfolio import _value_rows
from .store import clean, utcnow

log = logging.getLogger(__name__)

BUCKETS = {"lev": "레버리지·인버스 ETF", "dividend": "배당·배당성장", "index": "지수 ETF", "theme": "테마·섹터 ETF",
           "stock": "개별주"}
# Buy ideas for groups that need money; each is rated with the opinion engine before it is suggested.
CANDIDATES = {"dividend": ["KO", "PEP", "PG", "JNJ", "SCHD", "VIG", "DGRO"], "index": ["VOO", "VTI", "QQQM"]}

TAX = {"overseas_rate": 0.22, "overseas_deduction": 2_500_000, "kr_transaction": 0.0020,
       "div_withholding": {"USD": 0.15, "KRW": 0.154}, "financial_income_threshold": 20_000_000}
TAX_NOTES = [
    "해외주식 양도세: 1/1~12/31 실현 손익을 합산(손익통산)해 250만원 공제 후 22% (양도세 20% + 지방세 2%). 다음 해 5월 신고 · 손실 이월 없음.",
    "국내 상장주식(소액주주): 양도차익 비과세, 매도 금액의 0.20% 증권거래세 (2026년~ 코스피 0.05% + 농특세 0.15%, 코스닥 0.20%). ETF는 거래세 면제.",
    "국내 상장 ETF: 국내 주식형은 매매차익 비과세, 해외·채권·원자재형은 매매차익에 배당소득세 15.4%.",
    "배당: 미국 15% 원천징수 (한미 조세조약, 국내 추가 과세 없음), 국내 15.4%. 이자 + 배당이 연 2,000만원을 넘으면 금융소득종합과세 대상.",
]
DISCLAIMER = "세금은 단순 추정치이며 세무 상담이 아닙니다. 수수료 · 환전 스프레드 · 실제 체결가는 반영하지 않았습니다."

BAND = 1.0          # %p: groups within this of the target are left alone
IRREGULAR_YIELD = 0.20  # above this the payout is mostly one-off / return of capital, not a dividend to plan on
MIN_TRADE = 50_000  # won

_LEV = re.compile(r"(\b\d(?:\.\d+)?x\b|\bbull\b|\bbear\b|ultra|leveraged|inverse|레버리지|인버스)", re.I)
_DIV = re.compile(r"(dividend|income|배당|aristocrat|high yield)", re.I)
_INDEX = re.compile(r"(s&p ?500|nasdaq[- ]?100|\bqqq|total (stock )?market|russell|msci (world|acwi|eafe)|kospi ?200|코스피 ?200|"
                    r"코스닥 ?150|kosdaq ?150|dow jones industrial|(kodex|tiger|rise|ace|sol|plus|hanaro) ?200\b)", re.I)
_KR_ETF = re.compile(r"\b(ETF|TIGER|KODEX|SOL|ACE|RISE|PLUS|HANARO|KOSEF|ARIRANG|KIWOOM)\b")


def dividend_info(symbol: str, price: float | None) -> dict:
    """Trailing-12-month dividends per share, yield, 5-year dividend growth and the run of yearly raises."""
    df = prices.get_daily(symbol, years=10).df
    if "dividends" not in df or not len(df):
        return {"dps": 0.0, "yield": 0.0, "growth_5y": None, "raise_streak": 0}
    d = df["dividends"].fillna(0)
    last = df.index[-1]
    ttm = float(d[d.index > last - pd.Timedelta(days=365)].sum())
    yearly = d.groupby(d.index.year).sum()
    full = yearly[(yearly.index < last.year) & (yearly.index > df.index[0].year)]  # complete calendar years only
    growth = None
    if len(full) >= 6 and full.iloc[-6] > 0 and full.iloc[-1] > 0:
        growth = float((full.iloc[-1] / full.iloc[-6]) ** (1 / 5) - 1)
    streak = 0
    vals = full.tolist()
    for i in range(len(vals) - 1, 0, -1):
        if vals[i - 1] > 0 and vals[i] > vals[i - 1] * 1.0001:
            streak += 1
        else:
            break
    return {"dps": ttm, "yield": ttm / price if price else None, "growth_5y": growth, "raise_streak": streak}


def classify(name: str, quote_type: str | None, div: dict) -> tuple[str, str]:
    """Rule-based group (the user can override it). Returns (bucket, reason)."""
    if _LEV.search(name):
        return "lev", "이름에 2x · 3x · Bull · Bear 등 (레버리지/인버스 상품)"
    etf = quote_type == "ETF" or bool(_KR_ETF.search(name))
    y, g, streak = div.get("yield") or 0, div.get("growth_5y"), div.get("raise_streak") or 0
    if _DIV.search(name):
        return "dividend", "이름에 Dividend · Income · 배당"
    if not etf and y >= 0.03:
        return "dividend", f"배당수익률 {y * 100:.1f}% (3% 이상)"
    if not etf and y >= 0.015 and streak >= 5:
        return "dividend", f"배당 {streak}년 연속 증가 (배당수익률 {y * 100:.1f}%" + (f" · 5년 연평균 +{g * 100:.0f}%)" if g else ")")
    if etf and _INDEX.search(name):
        return "index", "시장 지수 추종 ETF"
    if etf:
        return "theme", "특정 테마 · 섹터 ETF"
    return "stock", "개별 종목"


def overseas_tax(gain_krw: float) -> float:
    return max(0.0, gain_krw - TAX["overseas_deduction"]) * TAX["overseas_rate"]


def _net_rate(ccy: str) -> float:
    return 1 - TAX["div_withholding"].get(ccy, 0.15)


def _rank(score: float | None) -> float:
    return 50.0 if score is None else score


def _buyable(p: dict) -> bool:
    """추가 매수 or better, or 보유 with a score of at least 50."""
    return p.get("action") in ("STRONG_BUY", "BUY") or (p.get("action") == "HOLD" and _rank(p.get("score")) >= 50)


def build(positions: list[dict], base: str, profile_: dict, plan: dict,
          fetch: Callable[[str], tuple[dict, dict]]) -> dict:
    """Everything the plan page shows. `plan`: the saved preferences (targets, dividend goal, ...)."""
    rows, errors, fx = _value_rows(positions, base)
    from .stock import profile

    def to_krw(amount: float, ccy: str) -> float | None:
        return amount if ccy == "KRW" else amount * fx if ccy == "USD" and fx else None

    overrides = {k: v for k, v in (plan.get("bucket_overrides") or {}).items() if v in BUCKETS}

    def rate(r: dict, held: bool = True) -> dict:
        """Dividends, profile and the same opinion score the portfolio page shows."""
        sym, price = r["symbol"], r.get("price")
        out: dict = {"div": {"dps": 0.0, "yield": None, "growth_5y": None, "raise_streak": 0}, "prof": {}, "adv": None}
        try:
            out["prof"] = profile(sym)
        except Exception:
            pass
        if price is None:
            return out
        try:
            out["div"] = dividend_info(sym, price)
        except Exception as e:
            log.info("plan dividends failed %s: %s", sym, e)
        try:
            tech, sc = fetch(sym)
            pos = r if held else {"symbol": sym, "price": price, "avg_cost_estimated": True, "weight": 0.0}
            try:
                from . import analyst
                an = analyst.view_quick(sym)
            except Exception:
                an = None
            out["adv"] = position_advice.advise(pos, sc, tech, profile_, an)  # same 판단 점수 as the opinions
        except Exception as e:
            log.info("plan rating failed %s: %s", sym, e)
        return out

    with ThreadPoolExecutor(max_workers=6) as ex:
        rated = list(ex.map(rate, rows))

    holdings = []
    for r, x in zip(rows, rated):
        if r.get("price") is None:
            continue
        ccy = r.get("currency") or "USD"
        value = to_krw(r["price"] * r["quantity"], ccy)
        if value is None:
            continue
        prof, div, adv = x["prof"], x["div"], x["adv"] or {}
        name = f"{r.get('name') or prof.get('name') or ''} {r.get('name_ko') or prof.get('name_ko') or ''}".strip()
        auto, why = classify(name, prof.get("quote_type"), div)
        b = overrides.get(r["symbol"], auto)
        paid = to_krw(div["dps"] * r["quantity"], ccy) or 0.0
        # Leveraged ETFs pay irregular year-end capital-gain distributions; very high payouts are mostly
        # return of capital. Neither counts toward the dividend goal (shown separately).
        irregular = b == "lev" or (div.get("yield") or 0) > IRREGULAR_YIELD
        div_krw = 0.0 if irregular else paid
        # cost in won: the broker's KRW average when known, otherwise the USD cost at today's rate
        cost = r.get("cost_krw") if r.get("cost_krw") is not None else (
            None if r.get("avg_cost_estimated") else to_krw(r["avg_cost"] * r["quantity"], ccy))
        holdings.append({
            "id": r["id"], "symbol": r["symbol"], "name": r.get("name_ko") or r.get("name") or prof.get("name"), "currency": ccy,
            "price": r["price"], "quantity": r["quantity"], "value_krw": value, "cost_krw": cost,
            "pl_krw": value - cost if cost is not None else None, "avg_cost_estimated": bool(r.get("avg_cost_estimated")),
            "bucket": b, "bucket_auto": auto, "bucket_reason": why, "overridden": r["symbol"] in overrides,
            "etf": prof.get("quote_type") == "ETF" or bool(_KR_ETF.search(name)),
            "dps": div["dps"], "yield": div.get("yield"), "growth_5y": div.get("growth_5y"), "raise_streak": div.get("raise_streak"),
            "div_krw": div_krw, "div_net_krw": div_krw * _net_rate(ccy),
            "div_irregular_krw": paid if irregular else 0.0,
            "div_irregular_reason": (None if not irregular or not paid else "레버리지 ETF의 비정기 분배금" if b == "lev"
                                     else f"분배율 {div['yield'] * 100:.0f}% - 대부분 비정기 분배 · 원금 반환일 가능성"),
            "action": adv.get("action"), "label": adv.get("label"), "score": adv.get("score")})

    total = sum(h["value_krw"] for h in holdings)
    if not total:
        return {"holdings": [], "message": "평가할 수 있는 보유 종목이 없습니다 (Data unavailable)"}
    cur = {b: sum(h["value_krw"] for h in holdings if h["bucket"] == b) for b in BUCKETS}

    # ---- dividends
    gross = sum(h["div_krw"] for h in holdings)
    net = sum(h["div_net_krw"] for h in holdings)
    basis = plan.get("dividend_basis") if plan.get("dividend_basis") in ("net", "gross") else "net"
    target_div = plan.get("dividend_target_krw")
    have = net if basis == "net" else gross
    gap = (target_div - have) if target_div else None

    # ---- candidates (rated only when a group may need them)
    targets_raw = {k: float(v) for k, v in (plan.get("targets") or {}).items() if k in BUCKETS and v is not None}
    tsum = sum(targets_raw.values())
    targets = {b: targets_raw.get(b, 0.0) * 100 / tsum for b in BUCKETS} if tsum > 0 else None
    contribution = max(0.0, float(plan.get("contribution_krw") or 0))
    held = {h["symbol"] for h in holdings}
    cand_syms = [s for b in CANDIDATES for s in CANDIDATES[b] if s not in held]
    cand: list[dict] = []
    if targets or (gap and gap > 0):
        def quote_row(s: str) -> dict:
            try:
                q = prices.get_quote(s)
                return {"symbol": s, "price": q.price, "currency": q.currency or "USD"}
            except Exception:
                return {"symbol": s, "price": None, "currency": "USD"}
        with ThreadPoolExecutor(max_workers=6) as ex:
            qrows = list(ex.map(quote_row, cand_syms))
            crated = list(ex.map(lambda r: rate(r, held=False), qrows))
        for r, x in zip(qrows, crated):
            if r["price"] is None or not x["adv"]:
                continue
            b = next(k for k, v in CANDIDATES.items() if r["symbol"] in v)
            y = x["div"].get("yield") or 0.0
            cand.append({"symbol": r["symbol"], "name": x["prof"].get("name_ko") or x["prof"].get("name") or r["symbol"],
                         "currency": r["currency"], "price": r["price"], "bucket": b, "yield": y,
                         "net_yield": y * _net_rate(r["currency"]), "growth_5y": x["div"].get("growth_5y"),
                         "raise_streak": x["div"].get("raise_streak"), "action": x["adv"].get("action"),
                         "label": x["adv"].get("label"), "score": x["adv"].get("score")})

    # How big the dividend group would need to be to reach the goal at the best rated yield.
    div_need = None
    if gap and gap > 0:
        mine = [{"symbol": h["symbol"], "name": h["name"], "bucket": "dividend", "action": h["action"], "score": h["score"],
                 "yield": h["yield"] or 0.0, "net_yield": (h["yield"] or 0.0) * _net_rate(h["currency"])}
                for h in holdings if h["bucket"] == "dividend"]
        pool = [c for c in cand + mine if c["bucket"] == "dividend" and _buyable(c) and c["yield"] <= IRREGULAR_YIELD]
        best = max(pool, key=lambda c: c["net_yield"] if basis == "net" else c["yield"], default=None)
        if best:
            y = best["net_yield"] if basis == "net" else best["yield"]
            if y > 0:
                need = gap / y  # won to put into that holding to close the gap
                div_need = {"symbol": best["symbol"], "name": best["name"], "yield": y, "amount_krw": need,
                            # dividend-group weight that closes the gap by switching existing money (None: not enough money)
                            "bucket_pct": (cur["dividend"] + need) / total * 100 if cur["dividend"] + need <= total else None}

    trades: list[dict] = []
    notes: list[str] = []
    after = dict(cur)
    if targets:
        t2 = total + contribution
        tval = {b: targets[b] / 100 * t2 for b in BUCKETS}
        cash = contribution
        parked = 0.0  # budget left as cash for lack of a buyable name (already explained in a note)
        # sells: overweight groups, lowest 판단 점수 first
        for b in BUCKETS:
            excess = cur[b] - tval[b]
            if excess <= BAND / 100 * t2:
                continue
            for h in sorted([h for h in holdings if h["bucket"] == b], key=lambda h: _rank(h["score"])):
                if excess < MIN_TRADE:
                    break
                amt = min(h["value_krw"], excess)
                px = to_krw(h["price"], h["currency"]) or 0
                if not px:
                    continue
                if h["currency"] == "KRW":  # whole shares only in Korea
                    sh = min(h["quantity"], math.ceil(amt / px))
                    amt = sh * px
                else:
                    sh = round(min(h["quantity"], amt / px), 4)
                    amt = min(sh * px, h["value_krw"])
                if amt < MIN_TRADE:
                    continue
                frac = sh / h["quantity"] if h["quantity"] else 0
                realized = (amt - h["cost_krw"] * frac) if h["cost_krw"] is not None else None
                why = (f"{BUCKETS[b]} {cur[b] / total * 100:.1f}% → 목표 {targets[b]:.0f}% · 이 그룹에서 판단 점수가 가장 낮은 편"
                       f" ({h['score'] if h['score'] is not None else '—'}점 · {h['label'] or '의견 없음'})")
                trades.append({"side": "sell", "symbol": h["symbol"], "name": h["name"], "bucket": b, "currency": h["currency"],
                               "price": h["price"], "shares": sh, "amount_krw": amt, "all": frac >= 0.999,
                               "realized_krw": realized, "overseas": h["currency"] != "KRW",
                               "transaction_tax_krw": amt * TAX["kr_transaction"] if h["currency"] == "KRW" and not h["etf"] else 0.0,
                               "div_change_krw": -h["div_krw"] * frac, "div_net_change_krw": -h["div_net_krw"] * frac,
                               "score": h["score"], "label": h["label"], "reason": why})
                after[b] -= amt
                cash += amt
                excess -= amt
        # buys: underweight groups, in proportion to their shortfall
        need = {b: tval[b] - after[b] for b in BUCKETS if tval[b] - after[b] > 0}
        dsum = sum(need.values())
        budget = {b: min(d, cash * d / dsum) for b, d in need.items()} if dsum else {}  # split before spending
        for b, amt_b in sorted(budget.items(), key=lambda kv: -kv[1]):
            if amt_b < MIN_TRADE:
                continue
            pool = [{"symbol": h["symbol"], "name": h["name"], "currency": h["currency"], "price": h["price"],
                     "yield": h["yield"] or 0.0, "net_yield": (h["yield"] or 0.0) * _net_rate(h["currency"]),
                     "score": h["score"], "label": h["label"], "action": h["action"], "held": True}
                    for h in holdings if h["bucket"] == b]
            pool += [{**c, "held": False} for c in cand if c["bucket"] == b]
            pool = [p for p in pool if p["yield"] <= IRREGULAR_YIELD]  # not one-off / return-of-capital payers
            ok = [p for p in pool if p["action"] in ("STRONG_BUY", "BUY")]
            if not ok and b in ("dividend", "index"):  # steady payers rarely rate 추가 매수; a solid 보유 will do
                ok = [p for p in pool if _buyable(p)]
            if not ok:
                notes.append(f"{BUCKETS[b]}: 지금 추가 매수 의견(추가 매수 이상)인 종목이 없어 {amt_b:,.0f}원을 현금으로 남겼습니다.")
                parked += amt_b
                continue
            if b == "dividend" and gap and gap > 0:
                key = (lambda p: (p["net_yield"] if basis == "net" else p["yield"], _rank(p["score"])))
            else:
                key = (lambda p: (_rank(p["score"]), p["yield"]))
            picks = sorted(ok, key=key, reverse=True)[:1 if amt_b < 1_500_000 else 2 if amt_b < 5_000_000 else 3]
            each = amt_b / len(picks)
            for p in picks:
                px = to_krw(p["price"], p["currency"]) or 0
                if not px:
                    continue
                sh = math.floor(each / px) if p["currency"] == "KRW" else round(each / px, 4)
                amt = sh * px if p["currency"] == "KRW" else each
                if amt < MIN_TRADE:
                    continue
                dy = p["yield"] or 0.0
                why = (f"{BUCKETS[b]} {after[b] / t2 * 100:.1f}% → 목표 {targets[b]:.0f}%"
                       + (f" · 배당수익률 {dy * 100:.1f}%" if dy else "")
                       + f" · 판단 점수 {p['score'] if p['score'] is not None else '—'} ({p['label'] or '—'})"
                       + ("" if p["held"] else " · 신규 편입 후보"))
                trades.append({"side": "buy", "symbol": p["symbol"], "name": p["name"], "bucket": b, "currency": p["currency"],
                               "price": p["price"], "shares": sh, "amount_krw": amt, "held": p["held"],
                               "div_change_krw": amt * dy, "div_net_change_krw": amt * dy * _net_rate(p["currency"]),
                               "score": p["score"], "label": p["label"], "reason": why})
                cash -= amt
        for b in BUCKETS:
            after[b] = cur[b] - sum(t["amount_krw"] for t in trades if t["side"] == "sell" and t["bucket"] == b) \
                + sum(t["amount_krw"] for t in trades if t["side"] == "buy" and t["bucket"] == b)
        if cash - parked >= MIN_TRADE:
            notes.append(f"매수에 쓰지 않은 현금 {cash - parked:,.0f}원")
        if not trades:
            notes.append(f"모든 그룹이 목표 비중 ±{BAND:.0f}%p 안에 있어 매매가 필요 없습니다.")
    t_after = sum(after.values()) or total

    # ---- tax
    realized_ytd = float(plan.get("realized_overseas_krw") or 0)
    planned = sum(t["realized_krw"] or 0 for t in trades if t["side"] == "sell" and t.get("overseas"))
    unknown = [t["symbol"] for t in trades if t["side"] == "sell" and t.get("overseas") and t["realized_krw"] is None]
    overseas_pl = [h["pl_krw"] for h in holdings if h["currency"] != "KRW" and h["pl_krw"] is not None]
    losses = sum(p for p in overseas_pl if p < 0)
    by_ccy = {c: sum((h["div_krw"] + h["div_irregular_krw"]) * (1 - _net_rate(c)) for h in holdings if h["currency"] == c)
              for c in ("USD", "KRW")}
    tax = {
        "realized_overseas_krw": realized_ytd,
        "overseas_now_krw": overseas_tax(realized_ytd),
        "planned_realized_krw": planned, "planned_unknown": unknown,
        "overseas_with_plan_krw": overseas_tax(realized_ytd + planned),
        "transaction_with_plan_krw": sum(t.get("transaction_tax_krw") or 0 for t in trades),
        "if_all_sold_gain_krw": realized_ytd + sum(overseas_pl),
        "overseas_if_all_sold_krw": overseas_tax(realized_ytd + sum(overseas_pl)),
        "harvestable_loss_krw": losses,
        "harvest_saving_krw": overseas_tax(realized_ytd) - overseas_tax(realized_ytd + losses) if realized_ytd > 0 else 0.0,
        "dividend_withheld_krw": by_ccy, "dividend_withheld_total_krw": sum(by_ccy.values()),
        "financial_income_warning": gross + sum(h["div_irregular_krw"] for h in holdings) > TAX["financial_income_threshold"],
        "rates": TAX, "notes": TAX_NOTES,
    }
    tax["total_with_plan_krw"] = tax["overseas_with_plan_krw"] + tax["transaction_with_plan_krw"] + tax["dividend_withheld_total_krw"]
    tax["total_now_krw"] = tax["overseas_now_krw"] + tax["dividend_withheld_total_krw"]

    d_gross_after = gross + sum(t["div_change_krw"] for t in trades)
    d_net_after = net + sum(t["div_net_change_krw"] for t in trades)
    return clean({
        "fx_usdkrw": fx, "total_krw": total, "contribution_krw": contribution,
        "buckets": [{"key": b, "label": BUCKETS[b], "current_krw": cur[b], "current_pct": cur[b] / total * 100,
                     "target_pct": targets[b] if targets else None, "after_krw": after[b],
                     "after_pct": after[b] / t_after * 100, "count": sum(1 for h in holdings if h["bucket"] == b)}
                    for b in BUCKETS],
        "holdings": sorted(holdings, key=lambda h: -h["value_krw"]),
        "dividend": {"gross_krw": gross, "net_krw": net, "basis": basis, "target_krw": target_div, "gap_krw": gap,
                     "progress_pct": have / target_div * 100 if target_div else None, "need": div_need,
                     "after_gross_krw": d_gross_after, "after_net_krw": d_net_after,
                     "after_progress_pct": (d_net_after if basis == "net" else d_gross_after) / target_div * 100 if target_div else None,
                     "irregular_krw": sum(h["div_irregular_krw"] for h in holdings),
                     "irregular": [{"symbol": h["symbol"], "name": h["name"], "amount_krw": h["div_irregular_krw"],
                                    "reason": h["div_irregular_reason"]} for h in holdings if h["div_irregular_krw"] > 0],
                     "top": [{"symbol": h["symbol"], "name": h["name"], "div_krw": h["div_krw"], "yield": h["yield"]}
                             for h in sorted(holdings, key=lambda h: -h["div_krw"]) if h["div_krw"] > 0][:6]},
        "tax": tax, "trades": trades, "candidates": cand, "notes": notes, "errors": errors,
        "plan": {**plan, "targets": targets_raw or None},
        "method": __doc__, "disclaimer": DISCLAIMER, "updated_at": utcnow().isoformat()})
