import pytest
import datetime as dt

from app.services import prices, stock

from .conftest import make_ohlcv


def _fake_daily(n):
    df = make_ohlcv(n=n, seed=4, start_date="2015-01-02")
    return lambda symbol, years=10: prices.PriceSeries(symbol, df, {}, dt.datetime(2026, 10, 9), "test")


def test_daily_range_carries_older_bars_and_opens_on_the_selected_range(monkeypatch):
    """1Y opens on its last 252 bars, but ~5 years are sent so zooming out keeps showing candles."""
    monkeypatch.setattr(prices, "get_daily", _fake_daily(2600))
    c = stock.chart("TEST", "1Y")
    times = [b["time"] for b in c["bars"]]
    assert len(times) == stock.HISTORY_BARS
    assert len(times) - times.index(c["visible_from"]) == 252
    c3 = stock.chart("TEST", "3Y")
    assert len(c3["bars"]) == 2 * 756  # longer ranges carry as much again
    assert len(c3["bars"]) - [b["time"] for b in c3["bars"]].index(c3["visible_from"]) == 756


def test_short_history_has_no_visible_marker(monkeypatch):
    monkeypatch.setattr(prices, "get_daily", _fake_daily(200))
    c = stock.chart("TEST", "1Y")
    assert len(c["bars"]) == 200 and c["visible_from"] is None  # everything fits: the chart just shows it all


def test_drawdown_from_peaks(monkeypatch):
    import pandas as pd

    from app.services import stock

    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=600)     # last bar = today, so "52 weeks" is fixed
    close = pd.Series(100.0, index=idx)
    close.iloc[100] = 200.0                     # old all-time high
    close.iloc[500] = 150.0                     # high of the last year
    close.iloc[550] = 90.0                      # low after it
    close.iloc[-1] = 120.0
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close})

    class PS:
        pass
    ps = PS()
    ps.df = df
    monkeypatch.setattr(stock.prices, "get_daily", lambda *a, **k: ps)
    dd = stock.drawdowns("X", df, 120, False, df)                          # the selected range: last 120 bars
    by = {k: p for p in dd["peaks"] for k in p["keys"]}
    assert by["range"]["peak"] == 150 and round(by["range"]["pct"], 1) == -20.0
    assert by["52w"] is by["range"]                                   # same high: one chip
    assert by["all"]["peak"] == 200 and by["all"]["pct"] == pytest.approx(-40.0)
    assert by["range"]["trough_pct"] == pytest.approx(-40.0) and by["range"]["rebound_pct"] == pytest.approx(33.33, abs=0.01)
