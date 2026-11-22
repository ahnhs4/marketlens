import datetime as dt

import numpy as np
import pandas as pd

from app.analysis import data_quality, indicators, news_rules, regime, similarity

from .conftest import make_ohlcv


# ------------------------------------------------------------------ data quality
def test_detects_unadjusted_split_and_ohlc_issues():
    df = make_ohlcv(n=300, seed=1, start_date="2023-01-03")
    df.iloc[150:, :4] = df.iloc[150:, :4] / 4  # 4:1 split never adjusted
    df.iloc[100, df.columns.get_loc("high")] = df["low"].iloc[100] * 0.9  # high below low
    df.iloc[120, df.columns.get_loc("volume")] = -5
    clean, rep = data_quality.validate(df, "TEST")
    checks = {i["check"]: i for i in rep.issues}
    assert "possible_unadjusted_split" in checks
    assert "ohlc_inconsistent" in checks and (clean["high"] >= clean[["open", "close"]].max(axis=1) - 1e-9).all()
    assert "negative_volume" in checks and np.isnan(clean["volume"].iloc[120])


def test_missing_sessions_against_exchange_calendar():
    df = make_ohlcv(n=120, seed=2, start_date="2024-01-02")
    df.index = pd.bdate_range("2024-01-02", periods=120)
    df = df.drop(df.index[[30, 31, 32]])
    _, rep = data_quality.validate(df, "AAPL")
    miss = [i for i in rep.issues if i["check"] == "missing_sessions"]
    assert miss and miss[0]["count"] >= 3


# ------------------------------------------------------------------ similarity
def test_analogs_only_use_realised_outcomes():
    df = make_ohlcv(n=1200, seed=33, drift=0.0004, vol=0.017)
    ind = indicators.compute_all(df)
    res = similarity.find_analogs(ind, lookback_years=3.0, embargo=20, min_separation=10)
    T = len(ind) - 1
    pos = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(ind.index)}
    for m in res["matches"]:
        p = pos[m["date"]]
        assert p <= T - 20  # embargo respected
        for h in similarity.HORIZONS:
            if p + h > T:
                assert m["forward"][f"{h}d"] is None
            else:
                expected = ind["adj_close"].iloc[p + h] / ind["adj_close"].iloc[p] - 1
                assert abs(m["forward"][f"{h}d"] - expected) < 1e-12
    dates = sorted(pos[m["date"]] for m in res["matches"])
    assert all(b - a >= 10 for a, b in zip(dates, dates[1:]))  # de-clustered


def test_analogs_flag_small_samples():
    df = make_ohlcv(n=1200, seed=34)
    ind = indicators.compute_all(df)
    res = similarity.find_analogs(ind, lookback_years=3.0, max_distance=0.05, min_sample=10)
    assert res["status"] == "insufficient_sample"
    assert "insufficient sample size" in res["message"]
    assert all(not h["sufficient"] for h in res["horizons"].values())


def test_wilson_interval():
    lo, hi = similarity.wilson(19, 27)
    assert lo < 19 / 27 < hi and 0 <= lo and hi <= 1


# ------------------------------------------------------------------ news
def test_dedup_merges_syndicated_headlines():
    now = dt.datetime(2026, 10, 5, 12, tzinfo=dt.timezone.utc)
    items = [
        {"title": "Fed signals slower pace of rate cuts as inflation stays sticky - Reuters", "source": "Reuters", "published_at": now},
        {"title": "Fed signals slower pace of rate cuts as inflation stays sticky", "source": "Yahoo Finance", "published_at": now + dt.timedelta(hours=1)},
        {"title": "Fed signals a slower pace of rate cuts, inflation sticky", "source": "CNBC", "published_at": now + dt.timedelta(hours=2)},
        {"title": "Samsung Electronics unveils new HBM4 memory chips", "source": "Yonhap", "published_at": now},
    ]
    cl = news_rules.cluster(items)
    assert len(cl) == 2
    fed = next(c for c in cl if "Fed" in c["title"])
    assert fed["cluster_size"] == 3 and set(fed["sources"]) == {"Reuters", "Yahoo Finance", "CNBC"}


def test_fingerprint_distinguishes_days_and_filings():
    d1 = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    d2 = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
    assert news_rules.fingerprint("Stock market today", d1) != news_rules.fingerprint("Stock market today", d2)
    assert news_rules.fingerprint("10-Q filed", d1, "u1", filing=True) != news_rules.fingerprint("10-Q filed", d1, "u2", filing=True)


def test_rule_classifier_reads_macro_direction():
    c = lambda t: news_rules.classify({"title": t, "source": "Reuters", "cluster_size": 1})  # noqa: E731
    assert c("Treasury yields rise to start the week")["sentiment"] in ("Bearish", "Very Bearish")
    assert c("Fed signals rate cuts ahead as inflation cools")["sentiment"] in ("Bullish", "Very Bullish")
    assert c("Hotter CPI inflation dents rate cut bets")["sentiment"] in ("Bearish", "Very Bearish")
    a = c("Nvidia beats estimates and raises guidance")
    assert a["sentiment"].endswith("Bullish") and a["impact"] in ("High", "Critical")
    assert c("3 Stocks to Buy Now Before They Soar")["sentiment"] == "Neutral"
    assert c("Fed officials signal slower rate cuts")["chain"][0]["node"] == "연준"


# ------------------------------------------------------------------ regime
def _series(values, freq="D", start="2015-01-01"):
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx, dtype=float)


def test_regime_risk_on_vs_risk_off():
    n = 2000
    up = np.linspace(3000, 6000, n)
    calm = {
        "spx": _series(up), "vix": _series(np.r_[np.full(n - 30, 18.0), np.full(30, 12.0)]),
        "vix3m": _series(np.full(n, 16.0)), "DGS10": _series(np.linspace(4.5, 3.8, n)),
        "DGS2": _series(np.linspace(4.5, 3.4, n)), "DFF": _series(np.full(n, 4.0)),
        "BAMLH0A0HYM2": _series(np.linspace(4.5, 3.0, n)), "NFCI": _series(np.full(n, -0.5)),
        "dxy": _series(np.linspace(105, 98, n)),
    }
    stress = {
        "spx": _series(up[::-1]), "vix": _series(np.r_[np.full(n - 30, 18.0), np.full(30, 35.0)]),
        "vix3m": _series(np.full(n, 28.0)), "DGS10": _series(np.linspace(3.8, 4.8, n)),
        "DGS2": _series(np.linspace(3.5, 5.0, n)), "DFF": _series(np.full(n, 4.0)),
        "BAMLH0A0HYM2": _series(np.linspace(3.0, 6.5, n)), "NFCI": _series(np.full(n, 0.6)),
        "dxy": _series(np.linspace(98, 108, n)),
    }
    on, off = regime.compute_regime(calm, 70), regime.compute_regime(stress, 20)
    assert on["label"] == "Risk-On" and off["label"] == "Risk-Off"
    assert on["score"] > off["score"] + 30
    assert any("VIX" in r["text"] for r in on["reasons"])
    assert any(o["name"] == "Rate-Cut Expectation" for o in on["overlays"])
    assert any(o["name"] == "Rate-Hike Expectation" for o in off["overlays"])
    assert abs(sum(on["quadrant_probabilities"].values()) - 1) < 1e-9
