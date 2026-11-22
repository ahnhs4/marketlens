import pandas as pd

from app.analysis import user_rules
from app.services import recommendations, universe


def _row(sym, tech, med, lng, macro=60.0):
    return {"symbol": sym, "name": sym, "market": "US", "price": 10.0, "technical": tech, "macro": macro, "signal": "BUY",
            "universes": ["SP500"], "dollar_volume": 1e9, "stoch_k": {"short": 50, "medium": med, "long": lng},
            "stoch_ml_overbought": med >= 80 and lng >= 80}


def test_medium_and_long_overbought_stocks_never_rank(monkeypatch):
    rows = [_row("HOT", 90, 85, 88), _row("OK1", 80, 85, 60), _row("OK2", 70, 40, 82)]
    monkeypatch.setattr(universe, "results", lambda: rows)
    tech = recommendations.technical_ranking("ALL")
    assert [i["symbol"] for i in tech["items"]] == ["OK1", "OK2"]          # only one of the two overbought: still ranked
    assert tech["excluded_overbought"]["symbols"] == ["HOT"]
    assert [i["symbol"] for i in recommendations.combined_ranking("ALL")["items"]] == ["OK1", "OK2"]
    assert "HOT" not in [i["symbol"] for i in recommendations.screen([], "ALL")["items"]]


def test_stochastic_rule_points():
    r = pd.Series({"close": 10.0, "stoch_medium_k": 86.0, "stoch_long_k": 91.0})
    assert user_rules.stoch_ml_overbought(r)
    assert not user_rules.stoch_ml_overbought(pd.Series({"stoch_medium_k": 86.0, "stoch_long_k": 70.0}))
    assert not user_rules.stoch_ml_overbought({"stoch_medium_k": None, "stoch_long_k": 90.0})


def test_rsi_oversold_or_rising_near_oversold_never_rank(monkeypatch):
    rows = [_row("OK", 70, 50, 50), _row("OS", 90, 50, 50), _row("NEAR_UP", 85, 50, 50), _row("NEAR_DOWN", 60, 50, 50)]
    rows[0]["rsi_ml_oversold"] = []
    rows[1]["rsi_ml_oversold"] = ["중기 RSI(14) 27.0 과매도"]
    rows[2]["rsi_ml_oversold"] = ["장기 RSI(28) 33.0 과매도 직전에서 상승 중 (3일 전 31.0)"]
    rows[3]["rsi_ml_oversold"] = []
    monkeypatch.setattr(universe, "results", lambda: rows)
    tech = recommendations.technical_ranking("ALL")
    assert [i["symbol"] for i in tech["items"]] == ["OK", "NEAR_DOWN"]
    g = {x["key"]: x for x in tech["excluded_overbought"]["groups"]}
    assert g["rsi_os"]["count"] == 2 and g["stoch_ob"]["count"] == 0
    assert g["rsi_os"]["items"][0]["why"] == ["중기 RSI(14) 27.0 과매도"]


def test_rsi_rule_reads_level_and_direction():
    def ind(med, lng):
        return pd.DataFrame({"rsi": med, "rsi_long": lng})
    assert user_rules.rsi_ml_oversold(ind([40, 40, 40, 45], [50, 50, 50, 50])) == []
    assert user_rules.rsi_ml_oversold(ind([40, 40, 40, 29.5], [50, 50, 50, 50])) == ["중기 RSI(14) 29.5 과매도"]
    up = user_rules.rsi_ml_oversold(ind([50, 50, 50, 50], [31, 32, 33, 34]))          # 30~35 and above 3 bars ago
    assert up == ["장기 RSI(28) 34.0 과매도 직전에서 상승 중 (3일 전 31.0)"]
    assert user_rules.rsi_ml_oversold(ind([50, 50, 50, 50], [38, 36, 35, 34])) == []   # falling toward it: not this rule
