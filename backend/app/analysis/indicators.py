"""Technical indicators.

Every function is causal: the value at bar t uses only bars <= t. That property is
what makes it safe to compute indicators once over a full history and then use
them in backtests (verified by tests/test_backtest.py::test_no_lookahead).

Conventions follow the original definitions:
  RSI / ATR / ADX - Wilder smoothing seeded with a simple average of the first n values.
  MACD           - EMA(12) - EMA(26), signal EMA(9), EMAs seeded at the first value (adjust=False).
  Bollinger      - SMA(20) +/- 2 * population std (ddof=0).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SMA_PERIODS = (5, 20, 50, 60, 120, 200)


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing: seed = mean of first n valid values, then x_t = x_{t-1} + (v_t - x_{t-1}) / n."""
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    valid = np.where(~np.isnan(vals))[0]
    if len(valid) < n:
        return pd.Series(out, index=s.index)
    start = valid[0]
    seed_end = start + n
    if seed_end > len(vals) or np.isnan(vals[start:seed_end]).any():
        return pd.Series(out, index=s.index)
    out[seed_end - 1] = vals[start:seed_end].mean()
    for i in range(seed_end, len(vals)):
        v = vals[i]
        out[i] = out[i - 1] if np.isnan(v) else out[i - 1] + (v - out[i - 1]) / n
    return pd.Series(out, index=s.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = wilder(d.clip(lower=0), n)
    loss = wilder((-d).clip(lower=0), n)
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    out[(loss == 0) & gain.notna()] = 100.0
    out[(gain == 0) & (loss == 0)] = 50.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    line.iloc[: slow - 1] = np.nan
    sig = line.ewm(span=signal, adjust=False).mean()
    sig[line.isna()] = np.nan
    sig.iloc[: slow - 1 + signal - 1] = np.nan
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return pd.DataFrame({"bb_mid": mid, "bb_upper": up, "bb_lower": lo,
                         "bb_pctb": (close - lo) / (up - lo), "bb_width": (up - lo) / mid})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), n)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    tr = true_range(high, low, close)
    tr.iloc[0] = np.nan
    atr_ = wilder(tr, n)
    pdi = 100 * wilder(plus_dm, n) / atr_
    mdi = 100 * wilder(minus_dm, n) / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return pd.DataFrame({"plus_di": pdi, "minus_di": mdi, "adx": wilder(dx, n)})


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume.fillna(0)).cumsum()


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, smooth_k: int = 3,
               d: int = 3) -> pd.DataFrame:
    ll = low.rolling(k, min_periods=k).min()
    hh = high.rolling(k, min_periods=k).max()
    raw = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    kk = raw.rolling(smooth_k, min_periods=smooth_k).mean()
    return pd.DataFrame({"stoch_k": kk, "stoch_d": kk.rolling(d, min_periods=d).mean()})


def stochastic_slow(high: pd.Series, low: pd.Series, close: pd.Series, n: int, m: int, t: int) -> tuple[pd.Series, pd.Series]:
    """Slow stochastic as Korean HTS define (n, m, t): slow %K = SMA_m(fast %K over n), %D = SMA_t(slow %K)."""
    ll = low.rolling(n, min_periods=n).min()
    hh = high.rolling(n, min_periods=n).max()
    fast = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    k = fast.rolling(m, min_periods=m).mean()
    return k, k.rolling(t, min_periods=t).mean()


# Short / medium / long oscillator sets shown as three lower panes (HTS defaults).
STOCH_SETS = {"short": (5, 3, 3), "medium": (10, 6, 6), "long": (20, 12, 12)}
OSC_SIGNAL = 9  # RSI signal line (SMA) for the RSI version of the three panes


def vwap(df: pd.DataFrame, intraday: bool, window: int = 20) -> pd.Series:
    """Intraday: session-anchored VWAP (resets each day). Daily: rolling `window`-day VWAP."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    if intraday:
        day = pd.Series(df.index.date, index=df.index)
        return pv.groupby(day).cumsum() / df["volume"].groupby(day).cumsum().replace(0, np.nan)
    return pv.rolling(window, min_periods=window).sum() / df["volume"].rolling(window, min_periods=window).sum()


def realized_vol(close: pd.Series, n: int = 20, periods_per_year: int = 252) -> pd.Series:
    r = np.log(close).diff()
    return r.rolling(n, min_periods=n).std() * np.sqrt(periods_per_year)


def compute_all(df: pd.DataFrame, *, rsi_period: int = 14, rsi_short: int = 7, rsi_long: int = 28,
                stoch_sets: dict[str, tuple[int, int, int]] | None = None, intraday: bool = False) -> pd.DataFrame:
    """Return a copy of OHLCV `df` with all indicator columns appended.

    RSI comes in three horizons: `rsi_short` / `rsi` (= rsi_period, the one scoring uses) / `rsi_long`.
    """
    out = df.copy()
    c, h, low, v = out["close"], out["high"], out["low"], out["volume"]
    for p in SMA_PERIODS:
        out[f"sma{p}"] = sma(c, p)
    for p in (12, 26, 50):
        out[f"ema{p}"] = ema(c, p)
    out["ema20"] = ema(c, 20)
    out["rsi"] = rsi(c, rsi_period)
    out["rsi_short"] = rsi(c, rsi_short)
    out["rsi_long"] = rsi(c, rsi_long)
    for col in ("rsi_short", "rsi", "rsi_long"):
        out[f"{col}_sig"] = out[col].rolling(OSC_SIGNAL, min_periods=OSC_SIGNAL).mean()
    for hz, (n, m, t) in (stoch_sets or STOCH_SETS).items():
        out[f"stoch_{hz}_k"], out[f"stoch_{hz}_d"] = stochastic_slow(h, low, c, n, m, t)
    out = out.join(macd(c)).join(bollinger(c)).join(adx(h, low, c)).join(stochastic(h, low, c))
    out["atr"] = atr(h, low, c, 14)
    out["atr_pct"] = out["atr"] / c * 100
    out["obv"] = obv(c, v)
    out["vwap"] = vwap(out, intraday)
    out["vol20"] = realized_vol(c, 20)
    out["vol_avg20"] = v.rolling(20, min_periods=20).mean()
    out["vol_avg50"] = v.rolling(50, min_periods=50).mean()
    out["vol_ratio"] = v / out["vol_avg20"]
    out["ret1"] = c.pct_change()
    for n in (5, 20, 60, 120, 250):
        out[f"mom{n}"] = c / c.shift(n) - 1
    out["hi52"] = h.rolling(252, min_periods=60).max()
    out["lo52"] = low.rolling(252, min_periods=60).min()
    out["hi20"] = h.rolling(20, min_periods=20).max()
    out["lo20"] = low.rolling(20, min_periods=20).min()
    out["hi55_prev"] = h.shift(1).rolling(55, min_periods=55).max()
    out["lo55_prev"] = low.shift(1).rolling(55, min_periods=55).min()
    return out
