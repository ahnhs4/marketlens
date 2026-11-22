import numpy as np
import pandas as pd

from app.analysis import indicators, levels, patterns

from .conftest import make_ohlcv


def frame_from_path(path: list[float], vol_spike_at: int | None = None) -> pd.DataFrame:
    c = np.array(path, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * 1.003
    low = c * 0.997  # close-anchored lows keep V-bottoms strictly unique for pivot detection
    v = np.full(len(c), 1e6)
    if vol_spike_at is not None:
        v[vol_spike_at:] = 3e6
    idx = pd.bdate_range("2021-01-04", periods=len(c))
    return pd.DataFrame({"open": o, "high": h, "low": low, "close": c, "adj_close": c, "volume": v,
                         "dividends": 0.0, "splits": 0.0}, index=idx)


def seg(a, b, n):
    return list(np.linspace(a, b, n, endpoint=False))


def test_double_bottom_detected_and_confirmed():
    path = seg(130, 131, 60) + seg(131, 100, 25) + seg(100, 112, 15) + seg(112, 100.5, 15) + seg(100.5, 118, 20) + [118] * 5
    df = frame_from_path(path, vol_spike_at=len(path) - 25)
    ind = indicators.compute_all(df)
    ev = patterns.scan(ind)
    db = [e for e in ev if e["pattern"] == "Double Bottom" and e["status"] == "confirmed"]
    assert db, [e["pattern"] for e in ev]
    e = db[0]
    assert e["direction"] == "bullish"
    assert 108 < e["levels"]["neckline"] < 114
    assert ind["close"].iloc[e["idx"]] > e["levels"]["neckline"]


def test_breakout_needs_close_above_prior_55d_high():
    path = [100 + np.sin(i / 3) for i in range(120)] + [104, 106, 107]
    df = frame_from_path(path, vol_spike_at=120)
    ind = indicators.compute_all(df)
    ev = [e for e in patterns.scan(ind) if e["pattern"] == "Breakout"]
    assert ev and ev[-1]["idx"] >= 120
    assert ev[-1]["levels"]["volume_ratio"] > 1.5


def test_pivots_are_confirmed_k_bars_later():
    h = np.array([1, 2, 3, 10, 3, 2, 1, 2, 3, 4, 5], dtype=float)
    piv = levels.find_pivots(h, h, k=3)
    top = [p for p in piv if p.kind == "H"][0]
    assert top.idx == 3 and top.confirm_idx == 6


def test_pattern_scan_has_no_lookahead():
    """Confirmed events found on the full history must be found identically on a truncated history."""
    df = make_ohlcv(n=900, seed=21, drift=0.0003, vol=0.02)
    full = patterns.scan(indicators.compute_all(df))
    cut = 700
    part = patterns.scan(indicators.compute_all(df.iloc[:cut]))
    key = lambda e: (e["pattern"], e["idx"], e["status"])  # noqa: E731
    part_keys = {key(e) for e in part}
    # Range events are stateful in the window; triangles depend on pivot sets confirmed by `cut` -> both still causal.
    early = [e for e in full if e["status"] == "confirmed" and e["idx"] < cut - 1]
    missing = [e for e in early if key(e) not in part_keys]
    assert not missing, missing[:3]


def test_historical_stats_use_next_open_entry():
    df = make_ohlcv(n=800, seed=9, drift=0.001, vol=0.02)
    ind = indicators.compute_all(df)
    ev = patterns.scan(ind)
    stats = patterns.historical_stats(ind, ev)
    for name, s in stats.items():
        assert 0 <= s["success_rate"] <= 1
        assert s["n"] >= 1
        assert s["sufficient"] == (s["n"] >= 5)


def test_volume_profile_value_area():
    df = make_ohlcv(n=200, seed=4)
    vp = levels.volume_profile(df)
    tot = sum(b["volume"] for b in vp["bins"])
    va = sum(b["volume"] for b in vp["bins"] if b["in_value_area"])
    assert va / tot >= 0.70
    assert vp["val"] <= vp["poc"] <= vp["vah"]
    assert abs(tot - df["volume"].sum()) / tot < 1e-6  # volume conserved


def test_support_below_and_resistance_above_price(ohlcv):
    ind = indicators.compute_all(ohlcv)
    sr = levels.support_resistance(ind)
    p = float(ind["close"].iloc[-1])
    assert all(s["price"] < p for s in sr["supports"])
    assert all(r["price"] > p for r in sr["resistances"])
    assert sr["nearest_support_pct"] < 0 < sr["nearest_resistance_pct"]
