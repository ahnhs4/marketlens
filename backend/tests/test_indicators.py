import numpy as np
import pandas as pd
import pytest

from app.analysis import indicators as ind


def test_sma_matches_naive_mean(ohlcv):
    s = ind.sma(ohlcv["close"], 20)
    assert np.isnan(s.iloc[18])
    assert s.iloc[19] == pytest.approx(ohlcv["close"].iloc[:20].mean())
    assert s.iloc[-1] == pytest.approx(ohlcv["close"].iloc[-20:].mean())


def test_rsi_first_value_follows_wilder_definition():
    close = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                       46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64])
    d = close.diff().iloc[1:15]
    ag, al = d.clip(lower=0).mean(), (-d).clip(lower=0).mean()
    expected_first = 100 - 100 / (1 + ag / al)
    r = ind.rsi(close, 14)
    assert r.iloc[:14].isna().all()
    assert r.iloc[14] == pytest.approx(expected_first, rel=1e-9)
    # next value uses Wilder smoothing
    g, l_ = max(close.iloc[15] - close.iloc[14], 0), max(close.iloc[14] - close.iloc[15], 0)
    ag2, al2 = (ag * 13 + g) / 14, (al * 13 + l_) / 14
    assert r.iloc[15] == pytest.approx(100 - 100 / (1 + ag2 / al2), rel=1e-9)


def test_rsi_bounds_and_edge_cases(ohlcv):
    r = ind.rsi(ohlcv["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    assert ind.rsi(pd.Series(np.arange(1, 40, dtype=float))).iloc[-1] == 100.0
    assert ind.rsi(pd.Series([10.0] * 40)).iloc[-1] == 50.0


def test_macd_definition(ohlcv):
    c = ohlcv["close"]
    m = ind.macd(c)
    line = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    assert m["macd"].iloc[-1] == pytest.approx(line.iloc[-1])
    assert m["macd"].iloc[:25].isna().all()
    sig = m["macd"].ewm(span=9, adjust=False).mean()
    assert m["macd_signal"].iloc[-1] == pytest.approx(sig.iloc[-1], rel=1e-6)
    assert (m["macd_hist"] - (m["macd"] - m["macd_signal"])).abs().max() < 1e-12


def test_bollinger(ohlcv):
    b = ind.bollinger(ohlcv["close"], 20, 2)
    w = ohlcv["close"].iloc[-20:]
    assert b["bb_mid"].iloc[-1] == pytest.approx(w.mean())
    assert (b["bb_upper"] - b["bb_lower"]).iloc[-1] == pytest.approx(4 * w.std(ddof=0))


def test_true_range_and_atr():
    h = pd.Series([10.0, 11.0, 12.0])
    l_ = pd.Series([9.0, 9.5, 10.5])
    c = pd.Series([9.5, 10.8, 11.0])
    tr = ind.true_range(h, l_, c)
    assert list(tr) == [1.0, 1.5, 1.5]  # max(H-L, |H-Cprev|, |L-Cprev|)
    a = ind.atr(h, l_, c, 2)
    assert a.iloc[1] == pytest.approx(1.25)
    assert a.iloc[2] == pytest.approx(1.25 + (1.5 - 1.25) / 2)


def test_realized_vol(ohlcv):
    v = ind.realized_vol(ohlcv["close"], 20)
    r = np.log(ohlcv["close"]).diff().iloc[-20:]
    assert v.iloc[-1] == pytest.approx(r.std() * np.sqrt(252))


def test_stochastic_range(ohlcv):
    st = ind.stochastic(ohlcv["high"], ohlcv["low"], ohlcv["close"]).dropna()
    assert ((st >= 0) & (st <= 100)).all().all()


def test_indicators_are_causal(ohlcv):
    """Values at bar t must not change when later bars are appended (no look-ahead)."""
    full = ind.compute_all(ohlcv)
    cut = 450
    part = ind.compute_all(ohlcv.iloc[:cut])
    cols = [c for c in part.columns if c not in ("dividends", "splits")]
    a = full.iloc[:cut][cols]
    b = part[cols]
    diff = (a - b).abs().max()
    # OBV is a cumulative sum and must match exactly as well
    assert (diff.fillna(0) < 1e-8).all(), diff[diff > 1e-8]
