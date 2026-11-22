import numpy as np
import pytest

from app.analysis import kr_financials as kf
from app.providers.krx import KrxKind


def row(code, reprt, sj, nm, cur, add=None, prev=None, padd=None, fs="CFS", dt_="2026.06.30 현재"):
    return {"stock_code": code, "reprt_code": reprt, "fs_div": fs, "sj_div": sj, "account_nm": nm, "thstrm_dt": dt_,
            "thstrm_amount": cur, "thstrm_add_amount": add, "frmtrm_amount": prev, "frmtrm_add_amount": padd,
            "rcept_no": "20260814000123", "currency": "KRW"}


def company(code="000010", fs="CFS"):
    """Half-year report: Q2 revenue 320, H1 600 (a year ago 280 / 500); equity 2,000 (FY-end 1,800)."""
    return [
        row(code, "11012", "BS", "자산총계", "5,000", prev="4,600", fs=fs),
        row(code, "11012", "BS", "부채총계", "3,000", prev="2,800", fs=fs),
        row(code, "11012", "BS", "유동자산", "1,500", prev="1,400", fs=fs),
        row(code, "11012", "BS", "유동부채", "1,000", prev="900", fs=fs),
        row(code, "11012", "BS", "자본총계", "2,000", prev="1,800", fs=fs),
        row(code, "11012", "IS", "매출액", "320", "600", "280", "500", fs=fs),
        row(code, "11012", "IS", "영업이익(손실)", "40", "70", "-10", "-20", fs=fs),
        row(code, "11012", "IS", "당기순이익(손실)", "30", "55", "20", "45", fs=fs),
    ]


def annual(code="000010"):
    return [
        row(code, "11011", "BS", "자본총계", "1,800", prev="1,700", dt_="2025.12.31 현재"),
        row(code, "11011", "BS", "부채총계", "2,800", prev="2,600", dt_="2025.12.31 현재"),
        row(code, "11011", "IS", "매출액", "1,000", prev="900") | {"bfefrmtrm_amount": "800"},
        row(code, "11011", "IS", "영업이익", "60", prev="50") | {"bfefrmtrm_amount": "40"},
        row(code, "11011", "IS", "당기순이익", "90", prev="70") | {"bfefrmtrm_amount": "60"},
    ]


def periods():
    h1 = kf.parse_main(company() + company(fs="OFS"))["000010"]
    fy = kf.parse_main(annual())["000010"]
    q1 = {"is": {"revenue": {"q": 280, "ytd": 280}, "operating_income": {"q": 30, "ytd": 30}}, "bs": {}}
    q3_25 = {"is": {"revenue": {"q": 240, "ytd": 740}, "operating_income": {"q": 30, "ytd": 10}}, "bs": {}}
    return {(2025, "11011"): fy, (2026, "11012"): h1, (2026, "11013"): q1, (2025, "11014"): q3_25}


def test_parse_main_prefers_consolidated_and_reads_ytd():
    d = kf.parse_main(company(fs="OFS") + company())["000010"]
    assert d["fs"] == "CFS" and d["end"] == "2026-06-30"
    assert d["is"]["revenue"] == {"q": 320, "ytd": 600, "q_prev": 280, "ytd_prev": 500}
    assert d["is"]["operating_income"]["ytd_prev"] == -20       # "(손실)" in the account name is ignored
    assert d["bs"]["equity"] == {"v": 2000, "prev_fy": 1800}
    assert kf.parse_main(company(fs="OFS"))["000010"]["fs"] == "OFS"


def test_ttm_growth_and_quarters():
    p = periods()
    assert kf.latest(p) == (2026, "11012")
    assert kf.ttm(p, "revenue") == 1000 + 600 - 500
    assert kf.growth(*kf.yoy(p, "revenue"))["pct"] == pytest.approx(20)
    assert kf.growth(*kf.yoy(p, "operating_income"))["state"] == "흑자전환"
    qs = {q["label"]: q for q in kf.quarters(p)}
    assert qs["25.4Q"]["revenue"] == 1000 - 740                # Q4 = annual - Q3 YTD
    assert qs["26.2Q"]["revenue"] == 320


@pytest.mark.parametrize("cur,prev,state", [(10, 5, "흑자"), (-1, 5, "적자전환"), (3, -2, "흑자전환"), (-5, -2, "적자지속"),
                                            (-1, -4, "적자축소")])
def test_growth_states(cur, prev, state):
    assert kf.growth(cur, prev)["state"] == state


def test_metrics_ratios_and_valuation():
    m = kf.metrics(periods(), market_cap=1500, close=10)
    ni = 90 + 55 - 45
    assert m["roe"] == pytest.approx(ni / ((2000 + 1800) / 2) * 100)
    assert m["debt_ratio"] == pytest.approx(150)
    assert m["current_ratio"] == pytest.approx(150)
    assert m["pbr"] == pytest.approx(0.75) and m["per"] == pytest.approx(15)
    assert m["rev_cagr_years"] == 2 and m["rev_cagr"] == pytest.approx(((1000 / 800) ** 0.5 - 1) * 100)   # 2023 -> 2025
    assert m["basis"] == "total" and not m["capital_impaired"]


def test_capital_impairment_is_flagged_not_priced():
    p = periods()
    p[(2026, "11012")]["bs"]["equity"] = {"v": -50, "prev_fy": 10}
    m = kf.metrics(p, market_cap=1500)
    assert m["capital_impaired"] and m["pbr"] is None and m["debt_ratio"] is None
    sc = kf.score(m, kf.distributions([m] * 30))
    pbr = next(i for i in sc["pillars"]["valuation"]["items"] if i["key"] == "pbr")
    assert pbr["score"] == 0 and pbr["note"] == "자본잠식"


def _peer(roe, opm, financial=False):
    return {"available": True, "financial": financial, "roe": roe, "op_margin": opm, "pbr": 1.0, "per": 10.0,
            "debt_ratio": 100.0, "current_ratio": 150.0}


def test_score_is_a_market_percentile_and_skips_bank_metrics():
    peers = [_peer(r, r / 2) for r in np.linspace(-10, 30, 50)]
    dist = kf.distributions(peers)
    best, worst = kf.score(_peer(30, 15), dist), kf.score(_peer(-10, -5), dist)
    assert best["pillars"]["profitability"]["score"] > 95 > 5 > worst["pillars"]["profitability"]["score"]
    bank = kf.score(_peer(12, 40, financial=True), dist)
    keys = {i["key"] for p in bank["pillars"].values() for i in p["items"]}
    assert "op_margin" not in keys and "debt_ratio" not in keys and "roe" in keys
    assert bank["pillars"]["stability"]["score"] is None


def test_turnaround_scores_without_a_percentage():
    m = {**_peer(10, 5), "op_growth": {"state": "흑자전환", "pct": None}, "rev_growth": {"state": "흑자", "pct": 10.0}}
    peers = [{**_peer(5, 5), "rev_growth": {"state": "흑자", "pct": float(x)}} for x in range(-20, 30)]
    sc = kf.score(m, kf.distributions(peers))
    op = next(i for i in sc["pillars"]["growth"]["items"] if i["key"] == "op_growth")
    assert op["score"] == 85 and op["note"] == "흑자전환"


def test_parse_full_uses_ids_then_names_and_unsigned_outflows():
    rows = [
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "account_nm": "지배기업 소유주지분",
         "thstrm_amount": "1,700", "frmtrm_amount": "1,600", "ord": "1"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계", "thstrm_amount": "2,000", "ord": "2"},
        {"sj_div": "CIS", "account_id": "-표준계정코드 미사용-", "account_nm": "지배기업의 소유주에게 귀속되는 당기순이익",
         "thstrm_amount": "25", "thstrm_add_amount": "50", "frmtrm_q_amount": "20", "frmtrm_add_amount": "40", "ord": "3"},
        {"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities", "account_nm": "영업활동현금흐름",
         "thstrm_amount": "80", "frmtrm_amount": "60", "ord": "4"},
        {"sj_div": "CF", "account_id": "ifrs-full_PurchaseOfPropertyPlantAndEquipment", "account_nm": "유형자산의 취득",
         "thstrm_amount": "-30", "frmtrm_amount": "-25", "ord": "5"},
    ]
    d = kf.parse_full(rows, "11012")
    assert d["bs"]["equity_parent"]["v"] == 1700 and d["bs"]["equity"]["v"] == 2000
    assert d["is"]["net_income_parent"] == {"q": 25, "ytd": 50, "q_prev": 20, "ytd_prev": 40}
    assert d["cf"]["capex"] == {"ytd": 30, "ytd_prev": 25} and d["cf"]["ocf"]["ytd"] == 80


def test_parse_dividends_common_shares_only():
    rows = [{"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,446", "frmtrm": "1,444", "lwfr": "1,444"},
            {"se": "주당 현금배당금(원)", "stock_knd": "우선주", "thstrm": "1,447", "frmtrm": "-", "lwfr": "-"},
            {"se": "(연결)현금배당성향(%)", "stock_knd": "", "thstrm": "29.2", "frmtrm": "67.8", "lwfr": "17.9"},
            {"se": "현금배당금총액(백만원)", "stock_knd": "", "thstrm": "9,809,438", "frmtrm": "-", "lwfr": "-"}]
    d = kf.parse_dividends(rows)
    assert d["dps"] == [1446, 1444, 1444] and d["payout"][0] == 29.2 and d["total"][0] == pytest.approx(9_809_438e6)


def test_summary_reads_like_a_sentence():
    m = kf.metrics(periods(), market_cap=1500, close=10)
    lines = kf.summary(m, kf.score(m, {}), "테스트")
    assert any("2026년 6개월 누적 기준 전년 같은 기간보다 매출은 20.0% 늘었고, 영업이익은 흑자로 돌아섰습니다." == x for x in lines)
    assert any(x.startswith("시가총액") and "PBR 0.75배" in x for x in lines)


def test_kind_low_pbr_table(monkeypatch):
    html = """<tr class="first"><td class="first txc">1</td><td><img alt='유가증권'> <a onclick="companysummary_open('00402'); return false;"
      title='현대제철'> 현대제철</a> <img alt='저PBR'> </td><td class="txc">2026-11-02</td><td class="txc">0.19</td>
      <td class="txc">소재</td><td class="txc">2026-10-22</td><td class="txc">12</td></tr>
      <tr><td class="first txc">2</td><td><img alt='코스닥'> <a onclick="companysummary_open('03294'); return false;" title='원익'>
      원익</a></td><td class="txc">2026-11-02</td><td class="txc">0.51</td><td class="txc">헬스케어</td><td class="txc">2026-10-22</td>
      <td class="txc">12</td></tr>"""
    k = KrxKind()
    monkeypatch.setattr(k, "_valueup_post", lambda *a, **kw: html)
    got = k.low_pbr()
    assert got[0] == {"isur_cd": "00402", "name": "현대제철", "market": "KOSPI", "published": "2026-11-02", "pbr": 0.19,
                      "gics_sector": "소재", "pbr_date": "2026-10-22", "fiscal_month": "12"}
    assert got[1]["market"] == "KOSDAQ"


def test_market_periods_and_open_windows():
    import datetime as dt

    from app.services import dart_fin
    today = dt.date(2026, 11, 5)
    ps = dart_fin.market_periods(today)
    assert ps[0] == (2022, "11011") and ps[-1] == (2026, "11014")
    assert (2026, "11011") not in ps and (2023, "11013") not in ps        # quarters only for the last three years
    assert dart_fin._open((2026, "11014"), today) and not dart_fin._open((2026, "11013"), today)
    assert dart_fin._open((2025, "11011"), dt.date(2026, 4, 20))       # late annual filings still arriving in April


def test_redacts_key_from_errors():
    from app.providers.dart import _redact
    assert _redact("GET https://opendart.fss.or.kr/api/x.json?crtfc_key=abcdef0123&corp_code=1 failed") == \
        "GET https://opendart.fss.or.kr/api/x.json?crtfc_key=***&corp_code=1 failed"


def test_weights_sum_to_one():
    assert sum(w for _, w, _ in kf.PILLARS.values()) == pytest.approx(1.0)
    for _, _, items in kf.PILLARS.values():
        assert sum(i[4] for i in items) == pytest.approx(1.0)


@pytest.mark.parametrize("args,cls", [
    ((0.5, 15, False, False, 12), "저평가 우량 후보"), ((0.5, 15, False, True, 12), "이익 질 확인"),
    ((0.5, 15, False, False, 2), "이익 질 확인"), ((0.5, 1, False, False, None), "가치함정 주의"),
    ((0.5, 6, False, False, None), "저PBR"), ((1.4, 15, False, False, 12), None), ((None, None, True, False, None), "자본잠식"),
])
def test_pbr_roe_buckets(args, cls):
    from app.services.valueup import classify
    assert classify(*args) == cls


def test_one_off_profit_flag():
    p = periods()
    p[(2025, "11011")]["is"]["operating_income"]["ytd"] = 10   # TTM op = 10 + 10 - (-20) = 40 against a net 100
    p[(2026, "11012")]["is"]["operating_income"] = {"q": 5, "ytd": 10, "q_prev": 10, "ytd_prev": -20}
    m = kf.metrics(p, market_cap=1500)
    assert m["operating_income_ttm"] == 40 and m["net_income_ttm"] == 100 and m["one_off"]   # net > 2x operating
    assert not kf.metrics(periods(), market_cap=1500)["one_off"]
