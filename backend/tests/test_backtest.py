"""Backtest validation: no look-ahead, correct transaction timing, adjustments, missing data, metrics."""
import numpy as np
import pandas as pd
import pytest

from app.analysis import backtest as bt
from app.analysis.data_quality import validate

from .conftest import make_ohlcv


def test_no_lookahead_future_shock_does_not_change_past():
    """Rewriting prices after day X must leave every trade and equity value up to X unchanged."""
    df = make_ohlcv(n=900, seed=12, drift=0.0006, vol=0.015)
    a = bt.backtest(df, "A", None, str(df.index[300].date()), str(df.index[-1].date()))
    shocked = df.copy()
    x = 650
    shocked.iloc[x:, :4] = shocked.iloc[x:, :4] * np.linspace(0.3, 3.0, len(shocked) - x)[:, None]
    shocked["adj_close"] = shocked["close"]
    b = bt.backtest(shocked, "A", None, str(df.index[300].date()), str(df.index[-1].date()))
    cut = df.index[x - 1]
    ea, eb = a["equity"][a["equity"].index <= cut], b["equity"][b["equity"].index <= cut]
    pd.testing.assert_series_equal(ea, eb)
    ta = [t for t in a["trades"] if pd.Timestamp(t["exit_date"]) <= cut and not t["open"]]
    tb = [t for t in b["trades"] if pd.Timestamp(t["exit_date"]) <= cut and not t["open"]]
    assert ta == tb


def test_orders_fill_at_next_open_with_costs():
    df = make_ohlcv(n=400, seed=1)
    ind = bt.prepare(df)
    entry = pd.Series(False, index=ind.index)
    exit_ = pd.Series(False, index=ind.index)
    entry.iloc[100] = True   # signal at the close of bar 100
    exit_.iloc[150] = True   # exit signal at the close of bar 150
    res = bt.run_period(ind, entry, exit_, ind.index[50], ind.index[-1], commission_bps=10, slippage_bps=20)
    t = res["trades"][0]
    assert t["entry_date"] == str(ind.index[101].date())
    assert t["exit_date"] == str(ind.index[151].date())
    assert t["entry_price"] == pytest.approx(ind["open"].iloc[101] * 1.002)
    assert t["exit_price"] == pytest.approx(ind["open"].iloc[151] * 0.998)
    expected = t["exit_price"] * 0.999 * 0.999 / t["entry_price"] - 1
    assert t["return"] == pytest.approx(expected)
    # flat before the fill: equity unchanged through the signal bar
    eq = res["equity"]
    assert eq.loc[: ind.index[100]].nunique() == 1


def test_no_order_on_last_bar_and_open_trade_marked():
    df = make_ohlcv(n=300, seed=2)
    ind = bt.prepare(df)
    entry = pd.Series(False, index=ind.index)
    entry.iloc[-1] = True  # signal on the final bar can't be executed inside the period
    res = bt.run_period(ind, entry, pd.Series(False, index=ind.index), ind.index[0], ind.index[-1])
    assert res["trades"] == []
    entry.iloc[-1] = False
    entry.iloc[-10] = True
    res = bt.run_period(ind, entry, pd.Series(False, index=ind.index), ind.index[0], ind.index[-1])
    assert res["trades"][-1]["open"] is True


def test_dividend_split_adjustment_scales_ohlc():
    df = make_ohlcv(n=50, seed=3)
    df["adj_close"] = df["close"] * 0.5  # e.g. a 2:1 split not yet reflected in "close"
    adj = bt._adjusted(df)
    assert adj["close"].iloc[10] == pytest.approx(df["close"].iloc[10] * 0.5)
    assert adj["open"].iloc[10] == pytest.approx(df["open"].iloc[10] * 0.5)
    assert adj["high"].iloc[10] == pytest.approx(df["high"].iloc[10] * 0.5)


def test_missing_data_is_cleaned_before_backtest():
    df = make_ohlcv(n=400, seed=4)
    df.iloc[200, df.columns.get_loc("close")] = -1.0        # impossible print
    df = pd.concat([df, df.iloc[[250]]])                      # duplicate timestamp
    clean, rep = validate(df, "TEST")
    checks = {i["check"] for i in rep.issues}
    assert {"non_positive_price", "duplicate_timestamp"} <= checks
    res = bt.backtest(clean, "D", None, str(clean.index[260].date()), str(clean.index[-1].date()))
    assert "metrics" in res and np.isfinite(res["metrics"]["total_return"])


def test_metrics_on_known_equity():
    idx = pd.bdate_range("2020-01-01", periods=253)
    eq = pd.Series(np.linspace(100, 121, 253), index=idx)
    eq.iloc[100] = eq.iloc[99] * 0.8  # a 20% dip that recovers
    m = bt.metrics(eq)
    assert m["total_return"] == pytest.approx(0.21)
    assert m["max_drawdown"] == pytest.approx(-0.2, abs=1e-9)
    days = (idx[-1] - idx[0]).days
    assert m["cagr"] == pytest.approx(1.21 ** (365.25 / days) - 1)


def test_walk_forward_split_keeps_periods_separate():
    df = make_ohlcv(n=1600, seed=8, drift=0.0005, vol=0.015, start_date="2019-01-01")
    periods = {"train": ("2020-01-01", "2022-06-30"), "validation": ("2022-07-01", "2023-06-30"),
               "test": ("2023-07-01", "2024-06-30")}
    res = bt.walk_forward_split(df, "A", periods)
    assert set(res["periods"]) >= {"train", "validation", "test"}
    te = res["periods"]["test"]
    assert te["metrics"]["start"] >= "2023-07-01" and te["metrics"]["end"] <= "2024-06-30"
    grid = bt.STRATEGIES["A"].grid
    for k, v in res["chosen_params"].items():
        assert v in grid.get(k, [v])


def test_regime_series_is_causal():
    df = make_ohlcv(n=500, seed=6)
    vix = pd.Series(20.0, index=df.index)
    r_full = bt.regime_series(df["close"], vix)
    r_part = bt.regime_series(df["close"].iloc[:400], vix.iloc[:400])
    pd.testing.assert_series_equal(r_full.iloc[:400], r_part)
