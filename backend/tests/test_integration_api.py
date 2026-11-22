"""Integration tests against live providers (Yahoo, FRED, Google News, SEC, KRX).

Marked `network`; skip with:  pytest -m "not network"
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("q,expected", [("NVDA", "NVDA"), ("엔비디아", "NVDA"), ("삼성전자", "005930.KS"),
                                        ("005930", "005930.KS"), ("하이닉스", "000660.KS"), ("NVDA.US", "NVDA")])
def test_ticker_lookup(client, q, expected):
    r = client.get("/api/search", params={"q": q})
    assert r.status_code == 200
    assert r.json()["results"][0]["symbol"] == expected


def test_price_api_us_and_kr(client):
    for t in ("NVDA", "005930"):
        r = client.get(f"/api/stock/{t}")
        assert r.status_code == 200
        q = r.json()["quote"]
        assert q["price"] > 0 and q["delay_minutes"] is not None
        c = client.get(f"/api/stock/{t}/chart", params={"range": "6M"}).json()
        assert len(c["bars"]) > 100 and {"open", "high", "low", "close", "sma20", "rsi"} <= set(c["bars"][-1])


def test_invalid_ticker_is_404_not_fake_data(client):
    r = client.get("/api/stock/ZZZZQQQ")
    assert r.status_code == 404


def test_signal_generation(client):
    r = client.get("/api/stock/AAPL/technical")
    assert r.status_code == 200
    s = r.json()["signal"]
    assert s["signal"] in ("STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL")
    assert 0 <= s["score"] <= 100 and s["components"] and (s["bullish_factors"] or s["bearish_factors"])


def test_news_ingestion(client):
    r = client.get("/api/stock/MSFT/news")
    assert r.status_code == 200
    d = r.json()
    assert d["items"], "expected real news items"
    it = d["items"][0]
    assert it["published_at"] and it["source"] and it["method"] in ("ai", "rules")
    assert d["summary"]["generated_at"]


def test_macro_regime(client):
    r = client.get("/api/macro/regime")
    assert r.status_code == 200
    d = r.json()
    assert d["label"] in ("Risk-On", "Neutral", "Risk-Off") and d["reasons"]


def test_backtest_endpoint(client):
    r = client.post("/api/backtest/run", json={"ticker": "SPY", "strategy": "A", "start": "2020-01-01"})
    assert r.status_code == 200
    d = r.json()
    assert "단순 보유" in d["benchmarks"] and d["metrics"]["trades"] >= 1


def test_portfolio_actions_endpoint(client):
    """Regression: the opinion endpoint must resolve settings, positions and per-holding advice."""
    pid = client.post("/api/portfolios", json={"name": "test-actions"}).json()["id"]
    assert client.get(f"/api/portfolios/{pid}/actions").status_code == 200  # empty portfolio
    client.post(f"/api/portfolios/{pid}/positions", json={"symbol": "NVDA", "quantity": 1, "avg_cost": 100})
    r = client.get(f"/api/portfolios/{pid}/actions")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["symbol"] == "NVDA" and item["action"] in ("STRONG_BUY", "BUY", "HOLD", "TRIM", "SELL", "NA")
    assert item["steps"] and item["method"]


def test_estimated_cost_flow(client):
    """Imported stand-in costs: no P/L until a real cost is entered (single edit or bulk paste)."""
    pid = client.post("/api/portfolios", json={"name": "test-costs"}).json()["id"]
    a = client.post(f"/api/portfolios/{pid}/positions", json={"symbol": "NVDA", "quantity": 2, "avg_cost": 100,
                                                              "avg_cost_estimated": True}).json()["id"]
    client.post(f"/api/portfolios/{pid}/positions", json={"symbol": "005930", "quantity": 1, "avg_cost": 50000,
                                                          "avg_cost_estimated": True})
    pf = client.get(f"/api/portfolios/{pid}").json()
    assert pf["totals"]["estimated_count"] == 2 and pf["totals"]["unrealized_pl"] is None
    nv = next(p for p in pf["positions"] if p["symbol"] == "NVDA")
    assert nv["unrealized_pl_pct"] is None and nv["since_ref_pct"] is not None

    r = client.patch(f"/api/portfolios/{pid}/positions/{a}", json={"avg_cost": 90})
    assert r.status_code == 200 and r.json()["avg_cost_estimated"] is False
    b = client.post(f"/api/portfolios/{pid}/avg-costs", json={"text": "삼성전자 61,200\nZZZZ 10\n텍스트"}).json()
    assert [u["symbol"] for u in b["updated"]] == ["005930.KS"] and len(b["unmatched"]) == 2
    assert b["remaining_estimated"] == 0
    t = client.get(f"/api/portfolios/{pid}").json()["totals"]
    assert t["estimated_count"] == 0 and t["unrealized_pl"] is not None
