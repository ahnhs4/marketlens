import numpy as np
import pandas as pd

from app.analysis import trendlines


def _channel(n=200, slope=0.25, width=8.0, period=25, broken=False):
    """Price zig-zagging inside a rising channel: lows touch base + slope·i, highs touch it + width."""
    i = np.arange(n)
    base = 100 + slope * i
    wave = (np.sin(2 * np.pi * i / period) + 1) / 2          # 0..1
    close = base + 1 + wave * (width - 2)
    if broken:
        close[-15:] = base[-15:] - 6                         # closes well below the support line
    high, low = close + 0.8, close - 0.8
    low = np.where(np.isclose(wave, 0, atol=0.02), base, low)
    high = np.where(np.isclose(wave, 1, atol=0.02), base + width, high)
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "atr": 2.0}, index=idx)


def test_rising_channel_is_found_and_named():
    out = trendlines.find(_channel())
    kinds = {ln["kind"] for ln in out["lines"]}
    assert kinds == {"support", "resistance"}
    best = next(ln for ln in out["lines"] if ln["kind"] == "support")
    assert best["confirmed"] and best["touches"] >= 3 and best["name"] == "상승 지지선"
    assert out["pattern"]["name"] == "상승 채널"


def test_a_broken_line_is_not_drawn():
    out = trendlines.find(_channel(broken=True))
    assert not any(ln["kind"] == "support" and ln["slope_pct_per_20"] > 0 and ln["distance_pct"] < 0 for ln in out["lines"])


def test_too_little_data_returns_nothing():
    assert trendlines.find(_channel(n=30))["lines"] == []
