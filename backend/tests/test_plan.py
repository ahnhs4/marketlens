import datetime as dt

import pytest

from app.analysis import position_advice
from app.services import analyst as analyst_svc
from app.services import plan, prices
from app.services import stock as stock_svc

from .conftest import make_ohlcv

NAMES = {"NVDL": ("GraniteShares 2x Long NVDA Daily ETF", "ETF"), "SCHD": ("Schwab U.S. Dividend Equity ETF", "ETF"),
         "DDOG": ("Datadog, Inc.", "EQUITY"), "KO": ("The Coca-Cola Company", "EQUITY"), "VOO": ("Vanguard S&P 500 ETF", "ETF"),
         "006800.KS": ("Mirae Asset Securities", "EQUITY")}
SCORES = {"NVDL": (30, "SELL"), "DDOG": (70, "BUY"), "SCHD": (55, "HOLD"), "KO": (56, "HOLD"), "006800.KS": (45, "HOLD")}


def _daily(div_per_year: float):
    df = make_ohlcv(n=2600, seed=2, drift=0.0, vol=0.001, start=50.0, start_date="2016-06-01")
    df["dividends"] = 0.0
    q = df.index[df.index.is_quarter_end | (df.index.day == 15) & df.index.month.isin([3, 6, 9, 12])]
    for y in sorted(set(df.index.year)):
        days = [d for d in q if d.year == y][:4]
        for d in days:
            df.loc[d, "dividends"] = div_per_year * (1 + 0.06 * (y - 2016)) / 4  # raised every year
    return df


@pytest.fixture
def fake(monkeypatch):
    rows = [  # value: NVDL 7M (lev), DDOG 2M, 006800 0.5M, SCHD 0.5M (dividend) -> total 10M won
        {"id": 1, "symbol": "NVDL", "quantity": 100.0, "avg_cost": 40.0, "price": 50.0, "currency": "USD", "cost_krw": 5_000_000},
        {"id": 2, "symbol": "DDOG", "quantity": 10.0, "avg_cost": 150.0, "price": 142.857, "currency": "USD"},
        {"id": 3, "symbol": "006800.KS", "quantity": 10.0, "avg_cost": 60000.0, "price": 50000.0, "currency": "KRW"},
        {"id": 4, "symbol": "SCHD", "quantity": 10.0, "avg_cost": 30.0, "price": 35.714, "currency": "USD"},
    ]
    monkeypatch.setattr(plan, "_value_rows", lambda pos, base: ([dict(r) for r in rows], [], 1400.0))
    divs = {"SCHD": 1.1, "KO": 1.6, "NVDL": 0.0, "DDOG": 0.0, "006800.KS": 0.0, "VOO": 0.5}
    monkeypatch.setattr(prices, "get_daily", lambda s, years=10: prices.PriceSeries(s, _daily(divs.get(s, 0)), {}, dt.datetime(2026, 10, 9), "t"))
    monkeypatch.setattr(prices, "get_quote", lambda s: type("Q", (), {"price": 50.0, "currency": "USD"})())
    monkeypatch.setattr(stock_svc, "profile", lambda s: {"name": NAMES.get(s, (s, "EQUITY"))[0], "quote_type": NAMES.get(s, (s, "EQUITY"))[1]})
    monkeypatch.setattr(analyst_svc, "view_quick", lambda s, timeout=8.0: None)
    monkeypatch.setattr(position_advice, "advise", lambda pos, sc, tech, prof, an=None: {
        "score": SCORES.get(pos["symbol"], (40, "HOLD"))[0], "action": SCORES.get(pos["symbol"], (40, "HOLD"))[1],
        "label": position_advice.LABEL[SCORES.get(pos["symbol"], (40, "HOLD"))[1]]})
    return lambda prefs: plan.build([], "USD", {}, prefs, lambda s: ({}, {}))


def test_classification_rules():
    assert plan.classify("Direxion Daily TSLA Bull 2X Shares", "ETF", {})[0] == "lev"
    assert plan.classify("Schwab U.S. Dividend Equity ETF", "ETF", {"yield": 0.032})[0] == "dividend"
    assert plan.classify("Vanguard S&P 500 ETF", "ETF", {"yield": 0.012})[0] == "index"
    assert plan.classify("Mirae Asset TIGER Securities ETF TIGER 증권", "ETF", {"yield": 0.039})[0] == "theme"  # ETFs need a dividend name
    assert plan.classify("The Coca-Cola Company", "EQUITY", {"yield": 0.024, "raise_streak": 8, "growth_5y": 0.045})[0] == "dividend"
    assert plan.classify("Datadog, Inc.", "EQUITY", {"yield": 0.0})[0] == "stock"


def test_overseas_tax_uses_the_deduction():
    assert plan.overseas_tax(2_000_000) == 0
    assert plan.overseas_tax(3_250_000) == pytest.approx(750_000 * 0.22)


def test_no_targets_means_no_trades(fake):
    res = fake({"targets": None})
    assert res["trades"] == [] and res["total_krw"] == pytest.approx(10_000_000, rel=1e-3)
    b = {x["key"]: x for x in res["buckets"]}
    assert b["lev"]["current_pct"] == pytest.approx(70, abs=0.1) and b["dividend"]["current_pct"] == pytest.approx(5, abs=0.1)


def test_raising_the_dividend_group_sells_the_worst_rated_and_buys_dividend_payers(fake):
    """The user's example: dividend payers are 5%, set 10% -> sell the least recommended, buy e.g. Coca-Cola."""
    res = fake({"targets": {"lev": 65, "dividend": 10, "index": 0, "theme": 0, "stock": 25}, "dividend_target_krw": 200_000})
    sells = [t for t in res["trades"] if t["side"] == "sell"]
    buys = [t for t in res["trades"] if t["side"] == "buy"]
    assert [t["symbol"] for t in sells] == ["NVDL"]  # the only overweight group; lowest score first
    assert sells[0]["amount_krw"] == pytest.approx(500_000, rel=0.02)
    assert sells[0]["realized_krw"] == pytest.approx(sells[0]["amount_krw"] - 5_000_000 * sells[0]["shares"] / 100, rel=1e-6)
    div_buys = [t for t in buys if t["bucket"] == "dividend"]
    assert div_buys and sum(t["amount_krw"] for t in div_buys) == pytest.approx(500_000, rel=0.02)
    assert all(t["symbol"] in ("SCHD", "KO") for t in div_buys)
    b = {x["key"]: x for x in res["buckets"]}
    assert b["dividend"]["after_pct"] == pytest.approx(10, abs=0.3) and b["lev"]["after_pct"] == pytest.approx(65, abs=0.3)
    d = res["dividend"]
    assert d["after_net_krw"] > d["net_krw"] and d["gap_krw"] == pytest.approx(200_000 - d["net_krw"])


def test_low_rated_names_are_not_bought(fake):
    res = fake({"targets": {"lev": 50, "dividend": 5, "index": 0, "theme": 0, "stock": 45}})
    buys = [t for t in res["trades"] if t["side"] == "buy"]
    assert buys and all(t["symbol"] == "DDOG" for t in buys)  # 006800.KS rates 보유 45: not added to


def test_goal_hint_uses_the_best_buyable_payer_including_holdings(fake):
    res = fake({"dividend_target_krw": 1_000_000})
    need = res["dividend"]["need"]
    assert need["symbol"] in ("SCHD", "KO")
    y = need["yield"]
    assert need["amount_krw"] == pytest.approx(res["dividend"]["gap_krw"] / y)
