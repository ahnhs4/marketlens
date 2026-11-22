"""Holding-level opinion rules (analysis/position_advice.py) and the three-horizon RSI columns."""
import pytest

from app.analysis import indicators as ind
from app.analysis import position_advice as pa


def _hz(score: float) -> dict:
    return {"score": score, "view": "Bullish" if score >= 60 else "Bearish" if score < 42 else "Neutral"}


def _inputs(overall=70.0, hz=(60.0, 70.0, 80.0), signal="BUY", close=100.0, sma200=90.0, rsi=55.0, rsi_short=60.0):
    horizons = {"short": _hz(hz[0]), "medium": _hz(hz[1]), "long": _hz(hz[2])}
    scores = {"overall": overall, "technical": overall, "macro": 60.0, "fundamental": 70.0, "horizons": horizons,
              "weights_used": {"technical": 0.5, "macro": 0.3, "fundamental": 0.2},
              "macro_detail": {"reasons": ["금리 하락"], "risks": []}, "fundamental_detail": {"pillars": {}}}
    tech = {"as_of": "2026-10-05", "signal": {"signal": signal, "bullish_factors": ["정배열"], "bearish_factors": [],
                                              "horizons": horizons},
            "indicators": {"close": close, "sma200": sma200, "rsi": rsi, "rsi_short": rsi_short, "rsi_long": 50.0},
            "support_resistance": {"supports": [{"label": "S1", "price": 95.0, "distance_pct": -5.0}],
                                   "resistances": [{"label": "R1", "price": 110.0, "distance_pct": 10.0}]}}
    return scores, tech


def _pos(price=100.0, avg=90.0, weight=10.0):
    return {"symbol": "TEST", "price": price, "avg_cost": avg, "unrealized_pl_pct": (price / avg - 1) * 100, "weight": weight}


PROFILE = {"holding_period": "Medium", "risk_tolerance": "Moderate", "max_drawdown": 0.25}


@pytest.mark.parametrize("score,action", [(85, "STRONG_BUY"), (70, "BUY"), (50, "HOLD"), (35, "TRIM"), (20, "SELL")])
def test_base_action_uses_signal_thresholds(score, action):
    assert pa.base_action(score) == action


def test_score_blends_overall_with_preferred_horizon():
    scores, tech = _inputs(overall=70, hz=(40, 90, 50))
    out = pa.advise(_pos(), scores, tech, {**PROFILE, "holding_period": "Medium"})
    assert out["score"] == pytest.approx(80.0)  # 0.5*70 + 0.5*90
    assert out["horizon_used"] == "medium"
    out_short = pa.advise(_pos(), scores, tech, {**PROFILE, "holding_period": "Short"})
    assert out_short["score"] == pytest.approx(55.0)
    assert out_short["action"] == "HOLD"


def test_concentration_caps_buying_and_trims_when_far_over_limit():
    scores, tech = _inputs(overall=75, hz=(70, 75, 75))
    assert pa.advise(_pos(weight=10), scores, tech, PROFILE)["action"] == "BUY"
    capped = pa.advise(_pos(weight=30), scores, tech, PROFILE)
    assert capped["action"] == "HOLD" and capped["overrides"][0]["rule"] == "비중 한도"
    trimmed = pa.advise(_pos(weight=40), scores, tech, PROFILE)  # > 1.5 x 25%
    assert trimmed["action"] == "TRIM"


def test_loss_beyond_limit_with_weak_trend_is_sell():
    scores, tech = _inputs(overall=50, hz=(45, 50, 50), signal="SELL", close=60, sma200=80)
    out = pa.advise(_pos(price=60, avg=100), scores, tech, PROFILE)  # -40% vs 25% limit
    assert out["action"] == "SELL"
    assert any(o["rule"] == "손실 한도" for o in out["overrides"])


def test_loss_beyond_limit_with_intact_trend_only_blocks_adding():
    scores, tech = _inputs(overall=80, hz=(80, 80, 80), signal="BUY", close=70, sma200=60)
    out = pa.advise(_pos(price=70, avg=100), scores, tech, PROFILE)
    assert out["action"] == "HOLD"


def test_hot_timing_blocks_adding_but_rsi_alone_does_not():
    scores, tech = _inputs(overall=85, hz=(85, 85, 85), rsi=76)
    assert pa.advise(_pos(), scores, tech, PROFILE)["action"] == "STRONG_BUY"  # RSI 76 alone: no measured penalty in the US
    tech["signal"]["components"] = [{"key": "timing", "score": 20.0, "details": ["20일선보다 8% 넘게 위 (급등 후 이격)"]}]
    out = pa.advise(_pos(), scores, tech, PROFILE)
    assert out["action"] == "HOLD" and out["overrides"][-1]["rule"] == "과열 구간"


def test_timing_enters_the_decision_score():
    scores, tech = _inputs(overall=60, hz=(60, 60, 60))
    tech["signal"]["components"] = [{"key": "timing", "score": 90.0, "details": ["장기 스토캐스틱 과매도권에서 상승 전환"]}]
    out = pa.advise(_pos(), scores, tech, PROFILE)
    assert out["score"] == pytest.approx(0.40 * 60 + 0.35 * 60 + 0.25 * 90)
    assert out["action"] == "BUY"  # 67.5: a turn lifts a borderline holding into 추가 매수
    tech["signal"]["components"] = [{"key": "timing", "score": 15.0, "details": ["RSI 78 과매수"]}]
    assert pa.advise(_pos(), scores, tech, PROFILE)["action"] == "HOLD"


def test_missing_data_gives_no_opinion():
    out = pa.advise({"symbol": "X", "price": None}, None, None, PROFILE)
    assert out["action"] == "NA" and "Data unavailable" in out["summary"]


def test_reasons_and_levels_are_reported():
    scores, tech = _inputs()
    out = pa.advise(_pos(), scores, tech, PROFILE)
    assert "정배열" in out["positives"] and any(x.startswith("매크로") for x in out["positives"])
    labels = [lv["label"] for lv in out["levels"]]
    assert "지지선 S1" in labels and "200일선" in labels and any(lb.startswith("허용 낙폭") for lb in labels)
    assert out["steps"][2]["label"] == "판단 점수"


def test_three_horizon_rsi_columns(ohlcv):
    out = ind.compute_all(ohlcv, rsi_period=14, rsi_short=7, rsi_long=28)
    assert out["rsi_short"].iloc[:7].isna().all() and out["rsi_short"].notna().iloc[7]
    assert out["rsi_long"].iloc[:28].isna().all() and out["rsi_long"].notna().iloc[28]
    # a shorter period reacts more: wider spread of values
    assert out["rsi_short"].std() > out["rsi"].std() > out["rsi_long"].std()


def test_estimated_cost_skips_pl_rules():
    scores, tech = _inputs(overall=50, hz=(45, 50, 50), signal="SELL", close=60, sma200=80)
    pos = {**_pos(price=60, avg=100), "avg_cost_estimated": True, "unrealized_pl_pct": None}
    out = pa.advise(pos, scores, tech, PROFILE)
    assert all(o["rule"] != "손실 한도" for o in out["overrides"])
    assert any(s["label"] == "평단 미입력" for s in out["steps"])
    assert not any(lv["label"].startswith("허용 낙폭") for lv in out["levels"])
    assert out["position"]["avg_cost"] is None


def test_parse_cost_lines():
    from app.services.portfolio import parse_cost_lines
    got = parse_cost_lines("NVDL 29.85\n엔비디아: $180.2\n005930 61,200원\nSOL 조선TOP3플러스 22,100\n\n텍스트만\nTSLA\t251.3")
    assert [(i, p) for i, p, _ in got] == [("NVDL", 29.85), ("엔비디아", 180.2), ("005930", 61200.0),
                                           ("SOL 조선TOP3플러스", 22100.0), ("텍스트만", None), ("TSLA", 251.3)]


def test_summarize_reports_pl_only_for_real_costs():
    from app.services.portfolio import summarize
    rows = [{"market_value": 110.0, "cost": 100.0, "avg_cost_estimated": False},
            {"market_value": 50.0, "cost": 60.0, "avg_cost_estimated": True},
            {"market_value": None, "cost": None, "avg_cost_estimated": False}]
    t = summarize(rows)
    assert t["market_value"] == 160 and t["cost"] == 100 and t["unrealized_pl"] == pytest.approx(10)
    assert t["unrealized_pl_pct"] == pytest.approx(10) and t["estimated_count"] == 1 and t["estimated_value"] == 50
    only_est = summarize([{"market_value": 50.0, "cost": 60.0, "avg_cost_estimated": True}])
    assert only_est["cost"] is None and only_est["unrealized_pl"] is None


def test_measured_analyst_signal_moves_the_score_by_its_weight():
    from app.analysis import analyst_evidence as ev
    scores, tech = _inputs(overall=70, hz=(40, 90, 50))
    an = {"available": True, "currency": "KRW", "consensus": {"mean": 90.0, "n_label": "증권사 5곳"}, "upside_pct": -10.0,
          "micro": ev.micro_score({"upside": -0.10}, "KR"), "counts": {}, "recent": [], "flow": "…"}
    base = pa.advise(_pos(), scores, tech, PROFILE)
    out = pa.advise(_pos(), scores, tech, PROFILE, an)
    assert out["score"] == pytest.approx(0.85 * base["score"] + 0.15 * 34)   # price above target in Korea: −16
    assert any(s["label"] == "기업 · 애널리스트" for s in out["steps"])
    assert any("목표가" in n for n in out["negatives"]) and out["analyst"]["flow"] == "…"



def test_users_style_weights_timing_most():
    scores, tech = _inputs(overall=70, hz=(40, 70, 50))
    tech["signal"]["components"] = [{"key": "timing", "score": 20.0, "details": ["RSI 중기 75 과매수"]}]
    tech["signal"]["style"] = "user"
    out = pa.advise(_pos(), scores, tech, PROFILE)
    assert out["score"] == pytest.approx(0.30 * 70 + 0.25 * 70 + 0.45 * 20)
    assert any("내 규칙" in s["label"] for s in out["steps"])
    tech["signal"]["style"] = "evidence"
    assert pa.advise(_pos(), scores, tech, PROFILE)["score"] == pytest.approx(0.40 * 70 + 0.35 * 70 + 0.25 * 20)
