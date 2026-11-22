import numpy as np
import pandas as pd
import pytest

from app.analysis import indicators, levels, patterns, scoring, technical

from .conftest import make_ohlcv


def evaluate(df, regime=None, weights=None, style="evidence"):
    ind = indicators.compute_all(df)
    ma = technical.ma_analysis(ind)
    ri = technical.rsi_analysis(ind)
    sr = levels.support_resistance(ind)
    pt = patterns.current_patterns(ind)
    return ind, scoring.technical_signal(ind, ma=ma, rsi_info=ri, sr=sr, patterns=pt["active"], regime=regime,
                                         weights=weights, style=style)


def test_score_is_sum_of_component_points(trending):
    _, sig = evaluate(trending)
    total = 50 + sum(c["points"] for c in sig["components"])
    assert sig["score"] == pytest.approx(total, abs=0.6)  # points are rounded to 0.1 each
    assert abs(sum(sig["weights"].values()) - 1) < 1e-6


def _timing(sig):
    return next(c for c in sig["components"] if c["key"] == "timing")


def _eval(df, market, style="evidence"):
    ind = indicators.compute_all(df)
    ma, ri = technical.ma_analysis(ind), technical.rsi_analysis(ind)
    sr, pt = levels.support_resistance(ind), patterns.current_patterns(ind)
    return ind, scoring.technical_signal(ind, ma=ma, rsi_info=ri, sr=sr, patterns=pt["active"], market=market, style=style)


def _spike():
    """A quiet stock that jumps ~25% in its last five sessions (far above its 20-day line)."""
    df = make_ohlcv(n=500, seed=3, drift=0.0003, vol=0.01)
    for col in ("open", "high", "low", "close", "adj_close"):
        df.iloc[-5:, df.columns.get_loc(col)] *= np.linspace(1.05, 1.25, 5)
    return df


def test_uptrend_scores_above_downtrend():
    up = make_ohlcv(n=700, seed=11, drift=0.002, vol=0.01)
    down = make_ohlcv(n=700, seed=11, drift=-0.002, vol=0.01)
    _, s_up = evaluate(up)
    _, s_dn = evaluate(down)
    assert s_dn["score"] < 50 and s_dn["signal"] in ("SELL", "STRONG SELL", "NEUTRAL")
    assert s_up["score"] > s_dn["score"] + 15
    assert s_up["bullish_factors"] and s_dn["bearish_factors"]


def test_timing_rules_follow_each_markets_evidence():
    """Same spike: Korea's data says a stock 8%+ above its 20-day line lagged; the US data says it kept leading."""
    _, kr = _eval(_spike(), "KR")
    _, us = _eval(_spike(), "US")
    assert any("급등 후 이격" in d and "(-19)" in d for d in _timing(kr)["details"])
    assert any("(+6)" in d for d in _timing(us)["details"])
    assert _timing(kr)["score"] < _timing(us)["score"] - 15
    assert kr["signal"] not in ("BUY", "STRONG BUY") and kr["market"] == "KR"


def test_us_momentum_is_rewarded_even_when_overbought():
    ind, sig = _eval(make_ohlcv(n=700, seed=11, drift=0.002, vol=0.01), "US")
    assert ind["rsi"].iloc[-1] >= 70
    assert sig["signal"] in ("BUY", "STRONG BUY")
    assert any("모멘텀" in d for d in _timing(sig)["details"])
    _, kr = _eval(make_ohlcv(n=700, seed=11, drift=0.002, vol=0.01), "KR")
    assert any("RSI14 70 이상" in d for d in _timing(kr)["details"])  # in Korea RSI ≥ 70 lagged


def test_deep_drawdown_differs_by_market():
    down = make_ohlcv(n=700, seed=11, drift=-0.002, vol=0.01)
    _, kr = _eval(down, "KR")
    _, us = _eval(down, "US")
    assert any("약한 종목" in d and "(-7)" in d for d in _timing(kr)["details"])
    assert any("낙폭 과대" in d and "(+4)" in d for d in _timing(us)["details"])


def test_kr_pullback_from_a_high_is_rewarded():
    ind, sig = _eval(make_ohlcv(n=700, seed=21, drift=0.002, vol=0.012).iloc[:451], "KR")
    assert -8 < (ind["close"].iloc[-1] / ind["hi52"].iloc[-1] - 1) * 100 < -5
    assert any("52주 고점 5~8%" in d and "(+8)" in d for d in _timing(sig)["details"])


def test_unvalidated_signals_add_no_points():
    """Stochastic turns, MACD crosses and golden crosses showed no edge, so they never move the timing score."""
    dn = make_ohlcv(n=600, seed=21, drift=-0.0015, vol=0.012).iloc[:578]  # a cut with a stochastic turn + MACD cross
    for m in ("US", "KR"):
        _, sig = _eval(dn, m)
        text = " ".join(_timing(sig)["details"])
        assert not any(w in text for w in ("전환", "MACD", "골든크로스", "스토캐스틱"))


def test_breakout_component_neutral_near_high_in_us():
    ind = indicators.compute_all(make_ohlcv(n=700, seed=11, drift=0.002, vol=0.01).iloc[:689])
    assert scoring.breakout_component(ind, [], "US").score <= 50


def test_oversold_rsi_in_downtrend_is_not_bullish():
    """RSI < 30 below the 200-day line earned only a small edge (KR/US ~+1% over 60 days): mild, never a buy alone."""
    df = make_ohlcv(n=700, seed=5, drift=-0.0035, vol=0.01)
    ind = indicators.compute_all(df)
    ri = technical.rsi_analysis(ind)
    for m in ("US", "KR"):
        comp = scoring.rsi_component(ind, {**ri, "value": 25.0, "momentum_5d": 0.0, "divergences": []}, m)
        assert 50 <= comp.score <= 60
    _, sig = evaluate(df)
    assert sig["signal"] not in ("BUY", "STRONG BUY")


def test_risk_off_raises_buy_threshold(trending):
    w, th_on, _ = scoring.regime_adjust(scoring.DEFAULT_WEIGHTS, "Risk-On")
    _, th_off, note = scoring.regime_adjust(scoring.DEFAULT_WEIGHTS, "Risk-Off")
    assert th_off["buy"] > th_on["buy"] and th_off["strong_buy"] > th_on["strong_buy"]
    assert note and "Risk-Off" in note
    assert w["momentum"] > scoring.DEFAULT_WEIGHTS["momentum"]  # Risk-On favours momentum


def test_user_weights_change_the_score(trending):
    _, base = evaluate(trending)
    _, tilted = evaluate(trending, weights={"trend": 0.9, "momentum": 0.0, "rsi": 0.0, "volume": 0.0, "breakout": 0.0,
                                            "support_resistance": 0.0, "volatility": 0.0, "pattern": 0.1, "timing": 0.0})
    assert tilted["weights"]["trend"] > 0.8
    assert tilted["score"] != pytest.approx(base["score"])


def test_horizons_present_and_bounded(trending):
    _, sig = evaluate(trending)
    for k in ("short", "medium", "long"):
        h = sig["horizons"][k]
        assert 0 <= h["score"] <= 100
        assert h["view"] in ("Bullish", "Neutral", "Bearish")
        assert abs(sum(c["weight"] for c in h["components"]) - 1) < 0.01


@pytest.mark.parametrize("score,label", [(95, "Exceptional"), (85, "Strong"), (72, "Positive"), (65, "Moderately Positive"),
                                         (55, "Neutral"), (45, "Weak"), (35, "Very Weak"), (10, "Highly Risky")])
def test_bands(score, label):
    assert scoring.band(score) == label


# ------------------------------------------------------------------ 'user' style (내 규칙, the default)
def test_default_style_is_the_users_rules():
    _, sig = evaluate(make_ohlcv(n=500, seed=4), style=None)
    assert sig["style"] == "user"


def test_overbought_at_the_high_is_marked_down_hard():
    """DDOG case: steady climb to a new high, short/medium RSI overbought -> the user's rules call it late."""
    up = make_ohlcv(n=700, seed=11, drift=0.002, vol=0.01)
    ind, user = _eval(up, "US", "user")
    _, ev = _eval(up, "US", "evidence")
    assert ind["rsi"].iloc[-1] >= 70 and ind["close"].iloc[-1] >= ind["hi52"].iloc[-1] * 0.98
    t = _timing(user)
    text = " ".join(t["details"])
    assert "과매수" in text and "고점" in text
    assert t["score"] <= 20 and _timing(ev)["score"] > t["score"] + 25
    assert user["score"] < ev["score"] - 10 and user["signal"] not in ("BUY", "STRONG BUY")
    assert user["weights"]["timing"] == pytest.approx(0.35) and user["weights"]["breakout"] == 0


def test_oversold_turnaround_is_marked_up():
    df = make_ohlcv(n=600, seed=5, drift=-0.0035, vol=0.01)
    for col in ("open", "high", "low", "close", "adj_close"):   # two up days after the slide: RSI turning up from oversold
        df.iloc[-2:, df.columns.get_loc(col)] *= np.array([1.012, 1.025])
    ind, sig = _eval(df, "US", "user")
    t = _timing(sig)
    assert any("과매도" in d for d in t["details"]) and t["score"] > 60


def test_golden_and_dead_cross_points():
    from app.analysis import user_rules
    idx = pd.bdate_range("2025-01-01", periods=300)
    c = pd.Series(np.r_[np.linspace(120, 100, 280), np.linspace(100, 108, 20)], index=idx)
    ind = indicators.compute_all(pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "adj_close": c,
                                               "volume": 1e6, "dividends": 0.0, "splits": 0.0}))
    pts = dict((t, p) for p, t in user_rules.signals(ind, None))
    assert pts.get("골든크로스 20/60일선 (6일 전)") == 10        # 20/60 counts for 10 bars after the cross
    assert not any(k.startswith("골든크로스 5/20일선") for k in pts)  # 5/20 crossed 16 bars ago: outside its 5-bar window
    assert user_rules._last_cross(-ind["sma5"], -ind["sma20"], 20)[0] == "dead"  # mirror image = dead cross
