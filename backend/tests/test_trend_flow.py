"""추세추종 flow rules (analysis/trend_flow.py), slow stochastic and oscillator states."""
import numpy as np
import pandas as pd
import pytest

from app.analysis import indicators as ind
from app.analysis import technical as tech
from app.analysis import trend_flow as tf
from tests.conftest import make_ohlcv


def _uptrend(n=320):
    df = make_ohlcv(n=n, seed=11, drift=0.003, vol=0.012, start=10_000)
    df["volume"] = 400_000.0
    return df


def test_slow_stochastic_matches_definition(ohlcv):
    k, d = ind.stochastic_slow(ohlcv["high"], ohlcv["low"], ohlcv["close"], 5, 3, 3)
    ll, hh = ohlcv["low"].rolling(5).min(), ohlcv["high"].rolling(5).max()
    fast = 100 * (ohlcv["close"] - ll) / (hh - ll)
    assert k.iloc[-1] == pytest.approx(fast.iloc[-3:].mean())
    assert d.iloc[-1] == pytest.approx(k.iloc[-3:].mean())
    assert ((k.dropna() >= 0) & (k.dropna() <= 100)).all()


def test_compute_all_has_three_horizon_oscillators(ohlcv):
    out = ind.compute_all(ohlcv)
    for hz in ("short", "medium", "long"):
        assert f"stoch_{hz}_k" in out and f"stoch_{hz}_d" in out
    assert out["stoch_long_k"].isna().sum() > out["stoch_short_k"].isna().sum()  # longer warm-up
    assert "rsi_sig" in out and "rsi_short_sig" in out


def test_oscillator_state_reports_turn_up_from_oversold():
    k = pd.Series([50, 30, 15, 10, 8, 12, 25.0])
    d = pd.Series([55, 40, 25, 15, 11, 10, 15.0])
    st = tech._osc_state(k, d, 80, 20)
    assert st["cross"]["type"] == "up" and st["cross"]["in_extreme"] and st["cross"]["bars_ago"] == 1
    assert "상향 돌파" in tech.describe_osc(st, "장기 스토캐스틱")


def test_screen_passes_a_clean_uptrend():
    df = _uptrend()
    r = tf.screen_one(df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["volume"].to_numpy(), "KR")
    c = r["checks"]
    assert c["above_ma150"] and c["off_low50"] and c["ma150_rising"] and c["value_today"] and c["volume_today"]


def test_screen_needs_history():
    df = make_ohlcv(n=150)
    assert tf.screen_one(df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["volume"].to_numpy(), "KR") is None


def test_share_checks():
    assert tf.share_checks(5e7, 3e7, "KR") == {"listed_max": True, "float_max": True, "float_ratio": True}
    assert tf.share_checks(9e7, 2e7, "KR") == {"listed_max": False, "float_max": True, "float_ratio": False}
    assert tf.share_checks(None, None, "KR")["listed_max"] is None


def test_strength_beats_market_and_holds_on_down_days():
    rng = np.random.default_rng(3)
    idx = 100 * np.cumprod(1 + rng.normal(0, 0.01, 120))
    stock = idx * np.linspace(1, 1.3, 120)  # same moves plus steady outperformance
    s = tf.strength(stock, idx)
    assert s["excess_60d"] > 0 and s["passed"]
    weak = idx * np.linspace(1, 0.8, 120)
    assert not tf.strength(weak, idx)["passed"]


def _bars(close_last: float, high_prev: float = 100.0, low10: float = 95.0, surge: float = 1.0):
    c = np.full(30, 98.0)
    h = np.full(30, 99.0)
    h[-11:-1] = high_prev
    lo = np.full(30, 97.0)
    lo[-5] = low10
    v = np.full(30, 1000.0)
    c[-1] = close_last
    h[-1] = max(close_last, 99.0)
    v[-1] = 1000.0 * surge * 98.0 / close_last
    return c, h, lo, v


def test_entry_states():
    assert tf.entry(*_bars(98.5))["state"] == "ready"                 # 1.5% under the line, stop 5% away
    assert tf.entry(*_bars(101, surge=2.0))["state"] == "breakout"    # crossed with volume
    assert tf.entry(*_bars(101, surge=1.0))["state"] == "breakout_weak"
    assert tf.entry(*_bars(107, surge=2.0))["state"] == "extended"
    assert tf.entry(*_bars(98.5, low10=90))["state"] == "wide"       # 10% stop = 아직 타점 아님
    assert tf.entry(*_bars(94, low10=93))["state"] == "waiting"


def test_trade_plan_rules():
    p = tf.trade_plan(100, 94, 98, breakout_today=False)
    assert p["entry"] == 100 and p["stop_pct"] == pytest.approx(-6)
    assert p["breakeven_at"] == pytest.approx(103) and p["half_at"] == pytest.approx(125)


def test_modes_follow_the_method():
    assert len(tf.MODES["bull"]) == 12 and len(tf.MODES["normal"]) == 8
    assert set(tf.REQUIRED) <= set(tf.MODES["bear"])


def test_outlook_builds_three_horizons_without_optional_inputs(ohlcv):
    from app.analysis import levels, patterns, scoring
    from app.services import outlook

    x = ind.compute_all(make_ohlcv(n=400, seed=5))
    ma, ri = tech.ma_analysis(x), tech.rsi_analysis(x)
    sr = levels.support_resistance(x, vp=levels.volume_profile(x.iloc[-126:]))
    pt = patterns.current_patterns(x)
    sig = scoring.technical_signal(x, ma=ma, rsi_info=ri, sr=sr, patterns=pt["active"], regime=None)
    last = x.iloc[-1]
    t = {"as_of": "2026-01-01", "signal": sig, "ma": ma, "support_resistance": sr, "patterns": pt["active"],
         "indicators": {k: (None if pd.isna(last.get(k)) else float(last[k])) for k in x.columns},
         "oscillators": tech.oscillator_states(x), "stoch_sets": {k: list(v) for k, v in ind.STOCH_SETS.items()},
         "rsi_periods": {"short": 7, "medium": 14, "long": 28}}
    o = outlook.build("TEST", t, {"macro": None, "macro_detail": {}, "fundamental_detail": {}}, None, None, None)
    assert set(o["horizons"]) == {"short", "medium", "long"}
    for h in o["horizons"].values():
        assert h["technical"] and h["conclusion"] and h["stance"] in ("positive", "mixed", "neutral", "negative")
