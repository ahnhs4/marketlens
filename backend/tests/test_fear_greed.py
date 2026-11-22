"""Fear & Greed index math (analysis/fear_greed.py) on synthetic data."""
import numpy as np
import pandas as pd
import pytest

from app.analysis import fear_greed as fg
from tests.conftest import make_ohlcv


def test_bands_follow_cnn_cutoffs():
    assert fg.band(10) == "Extreme Fear" and fg.band(30) == "Fear" and fg.band(50) == "Neutral"
    assert fg.band(60) == "Greed" and fg.band(90) == "Extreme Greed" and fg.band(None) is None


def test_score_is_centered_and_signed():
    x = pd.Series(np.r_[np.zeros(300), [3.0]])  # flat history, then a jump
    x.iloc[:300] = np.random.default_rng(1).normal(0, 1, 300)
    up = fg.to_score(x, +1)
    down = fg.to_score(x, -1)
    assert up.iloc[-1] > 95 and down.iloc[-1] < 5
    assert up.iloc[-1] + down.iloc[-1] == pytest.approx(100)
    assert up.iloc[:125].isna().all()  # needs MIN_PERIODS of history


def test_score_is_causal():
    x = pd.Series(np.random.default_rng(3).normal(0, 1, 400))
    a = fg.to_score(x, 1)
    y = x.copy()
    y.iloc[350:] += 50  # change the future only
    b = fg.to_score(y, 1)
    pd.testing.assert_series_equal(a.iloc[:350], b.iloc[:350])


def _members(n_sym=120, n=600):
    frames = {f"S{i}": make_ohlcv(n=n, seed=i) for i in range(n_sym)}
    wide = {c: pd.DataFrame({s: f[c] for s, f in frames.items()}) for c in ("close", "high", "low", "volume")}
    return wide


def test_member_breadth_counts_new_highs_and_lows():
    w = _members()
    b = fg.member_breadth(w["close"], w["high"], w["low"], w["volume"], min_members=100)
    assert b["strength"].iloc[:251].isna().all()
    last = b.iloc[-1]
    assert last["members"] == 120
    assert last["strength"] == pytest.approx((last["new_highs"] - last["new_lows"]) / 120 * 100)
    # forcing every member to a fresh high makes strength +100%
    w2 = {k: v.copy() for k, v in w.items()}
    w2["high"].iloc[-1] = w2["high"].max() * 2
    w2["low"].iloc[-1] = w2["high"].iloc[-1]  # far above any 52-week low
    b2 = fg.member_breadth(w2["close"], w2["high"], w2["low"], w2["volume"], min_members=100)
    assert b2["strength"].iloc[-1] == pytest.approx(100.0)


def test_composite_needs_enough_components():
    idx = pd.bdate_range("2020-01-01", periods=500)
    rng = np.random.default_rng(5)
    raw = pd.DataFrame({k: rng.normal(0, 1, 500) for k in fg.COMPONENTS}, index=idx)
    raw[["strength", "breadth", "junk"]] = np.nan  # only 4 left
    assert fg.compute(raw)["index"].isna().all()
    raw["junk"] = rng.normal(0, 1, 500)  # 5 available
    out = fg.compute(raw)
    assert out["index"].iloc[-1] == pytest.approx(out[[k for k in fg.COMPONENTS if k not in ("strength", "breadth")]].iloc[-1].mean())
    assert out["available"].iloc[-1] == 5
