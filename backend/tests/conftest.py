"""Test fixtures. Unit tests use deterministic synthetic OHLCV; tests marked `network` hit real APIs.

Run fast tests:      pytest -m "not network"
Run everything:      pytest
"""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pandas as pd
import pytest

# Isolated database for tests (never touches the dev database).
_tmp = tempfile.mkdtemp(prefix="marketlens-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ.setdefault("SCHEDULER_ENABLED", "false")


def make_ohlcv(n: int = 600, seed: int = 7, drift: float = 0.0004, vol: float = 0.018, start: float = 100.0,
               start_date: str = "2020-01-02") -> pd.DataFrame:
    """Geometric random walk with plausible intraday ranges and volume, indexed by business days."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.r_[start, close[:-1]] * np.exp(rng.normal(0, vol / 4, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, vol / 2, n)))
    volume = rng.lognormal(14, 0.4, n)
    idx = pd.bdate_range(start_date, periods=n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "adj_close": close,
                       "volume": volume, "dividends": 0.0, "splits": 0.0}, index=idx)
    df.index.name = "date"
    return df


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return make_ohlcv()


@pytest.fixture
def trending() -> pd.DataFrame:
    return make_ohlcv(n=700, seed=3, drift=0.0015, vol=0.012)
