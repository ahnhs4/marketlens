import pytest

from app.services.portfolio import krw_breakdown, parse_cost_lines, summarize


def _usd_row(**kw):
    return {"quantity": 155.0, "avg_cost": 30.03, "price": 36.86, "currency": "USD", "avg_cost_krw": 43202.0, **kw}


def test_fx_loss_offsets_a_dollar_gain():
    """NVDL from the Toss screenshots: +22.7% in dollars, but bought at 1,438.6원/$ and the rate is now 1,340.9."""
    b = krw_breakdown(_usd_row(), 1340.9)
    assert b["ret_usd_pct"] == pytest.approx(22.74, abs=0.01)
    assert b["avg_fx"] == pytest.approx(1438.63, abs=0.01)
    assert b["ret_krw_pct"] == pytest.approx(14.41, abs=0.01)
    assert b["fx_pl_krw"] == pytest.approx(155 * 30.03 * (1340.9 - 43202 / 30.03))
    assert b["fx_pl_krw"] < 0 < b["price_pl_krw"]
    assert b["price_pl_krw"] + b["fx_pl_krw"] == pytest.approx(b["pl_krw"])  # the two parts add up to the KRW P/L


def test_usd_holding_without_krw_cost_has_no_fx_split():
    b = krw_breakdown(_usd_row(avg_cost_krw=None), 1340.9)
    assert "fx_pl_krw" not in b and "pl_krw" not in b
    assert b["ret_usd_pct"] == pytest.approx(22.74, abs=0.01)


def test_krw_holding_and_estimated_cost():
    kr = krw_breakdown({"quantity": 2.0, "avg_cost": 64833.0, "price": 29850.0, "currency": "KRW"}, 1340.9)
    assert kr["fx_pl_krw"] == 0 and kr["pl_krw"] == kr["price_pl_krw"] == pytest.approx(-69966)
    assert krw_breakdown(_usd_row(avg_cost_estimated=True), 1340.9) == {}
    assert krw_breakdown(_usd_row(price=None), 1340.9) == {}


def test_summary_krw_totals_split_into_price_and_fx():
    rows = []
    for r in (_usd_row(), {"quantity": 2.0, "avg_cost": 64833.0, "price": 29850.0, "currency": "KRW"},
              _usd_row(avg_cost_krw=None, avg_cost_estimated=True)):
        r = {**r, "market_value": 1.0, "cost": 1.0}
        rows.append({**r, **krw_breakdown(r, 1340.9)})
    k = summarize(rows)["krw"]
    assert k["count"] == 2  # the estimated holding has no KRW cost, so it stays out
    assert k["pl"] == pytest.approx(k["price_pl"] + k["fx_pl"])
    assert k["ret_pct"] == pytest.approx((k["value"] / k["cost"] - 1) * 100)


def test_parse_cost_lines_reads_a_second_krw_price():
    got = parse_cost_lines("NVDL $30.03 43,202원\n005930 61,200\nSOL 조선TOP3플러스 35,470", with_krw=True)
    assert got[0][:2] == ("NVDL", 30.03) and got[0][3] == 43202
    assert got[1][:2] == ("005930", 61200) and got[1][3] is None
    assert got[2][:2] == ("SOL 조선TOP3플러스", 35470)
    assert len(parse_cost_lines("NVDL 30.03 43202")[0]) == 3  # without with_krw the old 3-tuple shape is kept
