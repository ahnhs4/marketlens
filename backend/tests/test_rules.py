import numpy as np
import pandas as pd
import pytest

from app.analysis import rules
from app.services import strategy_study

from .conftest import make_ohlcv


def _daily(n=900, seed=5, drift=0.0005, vol=0.015):
    return make_ohlcv(n=n, seed=seed, drift=drift, vol=vol)


@pytest.mark.parametrize("kind", [k for k, v in rules.KINDS.items() if v.timeframe == "daily"])
def test_every_daily_rule_runs_and_fills_after_the_signal(kind):
    df = _daily()
    trades, n, years, bh = rules.run(df, kind, {})
    assert trades is not None and n == len(df) and years > 3
    sig = rules.signals(kind, df, {})
    k = rules.KINDS[kind]
    for t in trades:
        i = df.index.get_loc(pd.Timestamp(t["entry_time"]))
        if k.intrabar:
            level = sig["level"].iloc[i]
            assert t["entry"] == pytest.approx(max(df["open"].iloc[i], level))
            assert t["exit_time"] == t["entry_time"]  # day trade: out at that day's close
        else:
            assert sig["entry"].iloc[i - 1] and t["entry"] == pytest.approx(df["open"].iloc[i])


def test_rsi2_buys_a_sharp_dip_in_an_uptrend_and_sells_above_the_5_day_line():
    close = list(np.linspace(100, 160, 260)) + [150, 141, 143, 150, 162, 163]
    idx = pd.bdate_range("2023-01-02", periods=len(close))
    c = pd.Series(close, index=idx)
    df = pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.005, "low": c * 0.995, "close": c, "volume": 1e6})
    trades, *_ = rules.run(df, "rsi2", {})
    sig = rules.signals("rsi2", df, {})
    first_signal = int(np.argmax(sig["entry"].to_numpy()))
    assert first_signal == 260                                  # the first sharp down day (RSI(2) 2.3)
    assert len(trades) == 1 and pd.Timestamp(trades[0]["entry_time"]) == idx[261]   # filled at the next open
    exit_signal = next(i for i in range(262, len(idx)) if sig["exit"].iloc[i])
    assert pd.Timestamp(trades[0]["exit_time"]) == idx[exit_signal + 1]


def test_orb_stops_at_the_range_low_and_trades_once_a_day():
    idx = pd.date_range("2026-10-05 13:30", periods=78, freq="5min", tz="UTC")  # one session of 5-minute bars
    c = np.r_[np.full(6, 100.0), [101.0, 102.0], np.linspace(102, 98, 70)]
    df = pd.DataFrame({"open": np.r_[100, c[:-1]], "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 1e5,
                       "session": "regular"}, index=idx)
    trades, *_ = rules.run(df, "orb", {})
    assert len(trades) == 1 and trades[0]["why"] == "손절"
    assert trades[0]["exit"] == pytest.approx(100 - 0.05)  # the opening-range low


def test_costs_only_change_the_score_not_the_trades():
    df = _daily()
    gross = rules.simulate(df, "double7", {}, fee_bps=0, slippage_bps=0)
    net = rules.simulate(df, "double7", {}, fee_bps=25, slippage_bps=5)
    assert gross["metrics"]["trades"] == net["metrics"]["trades"]
    assert net["metrics"]["avg_ret_pct"] < gross["metrics"]["avg_ret_pct"]
    assert rules.net_return(100, 100, 25, 5) == pytest.approx(0.9995 * 0.9975 / (1.0005 * 1.0025) - 1)


def test_study_eligibility_rules():
    good = {"event": {"trades": 900, "avg_ret_pct": 0.3, "first_half_avg_pct": 0.2, "second_half_avg_pct": 0.4, "pct_symbols_positive": 70}}
    assert strategy_study._eligible(good, "daily")[0]
    for k, v in (("avg_ret_pct", -0.1), ("second_half_avg_pct", -0.01), ("pct_symbols_positive", 50), ("trades", 100)):
        bad = {"event": {**good["event"], k: v}}
        assert not strategy_study._eligible(bad, "daily")[0]


def test_flatten_option_does_not_cut_daily_swing_rules():
    df = _daily()
    held = rules.run(df, "double7", {}, flatten=True)[0]
    assert held == rules.run(df, "double7", {}, flatten=False)[0]
    assert any(t["bars"] > 0 for t in held)
