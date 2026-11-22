"""Rule strategies for automated (paper) trading, and the simulator the backtest, the strategy study and the
live paper engine all share - so a rule behaves the same everywhere.

Timing:
  * Only completed bars are read (the bar still forming is ignored); intraday rules use regular-session bars.
  * A signal on bar t fills at the next price available: the open of bar t+1 in a backtest, the live quote
    in paper trading. "Intrabar" rules (volatility breakout) fill when price crosses a level set in advance.
  * Stops: a level from the rule (e.g. the opening-range low) or entry − ATR × k; k = 0 means no stop, as in
    the published versions of most mean-reversion rules. Day-trade rules close at the session's last bar.
  * Trades are recorded at raw prices; fees and slippage are applied afterwards (trade_metrics), so the same
    run can be scored under several cost assumptions. Each trade uses a fixed dollar amount (no compounding).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import atr, bollinger, ema, macd, rsi, sma

INTERVALS = {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}
HISTORY = {"5m": "60d", "15m": "60d", "1h": "730d"}      # what Yahoo serves for intraday backtests
LIVE_HISTORY = {"5m": "1mo", "15m": "1mo", "1h": "3mo"}
ET = "America/New_York"


@dataclass(frozen=True)
class Kind:
    id: str
    name: str
    family: str                 # 평균회귀 / 추세추종 / 돌파
    timeframe: str              # daily / intraday
    intervals: tuple[str, ...]
    description: str
    defaults: dict
    labels: dict
    signals: Callable[[pd.DataFrame, dict], pd.DataFrame]
    day_trade: bool = False     # always flat at the close
    intrabar: bool = False      # enters when price crosses `level` during the bar
    source: tuple[str, str] | None = None   # (title, url) of a published description
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------------------ helpers
def _et(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_convert(ET) if index.tz is not None else index


def _day(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(_et(index).date)


def _mins_from_open(index: pd.DatetimeIndex) -> np.ndarray:
    e = _et(index)
    return np.asarray(e.hour * 60 + e.minute - 570)


def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def _frame(bars: pd.DataFrame, entry, exit_, why_entry, why_exit, **extra) -> pd.DataFrame:
    out = pd.DataFrame(index=bars.index)
    out["atr"] = atr(bars["high"], bars["low"], bars["close"], 14)
    out["entry"] = pd.Series(entry, index=bars.index).fillna(False).astype(bool)
    out["exit"] = pd.Series(exit_, index=bars.index).fillna(False).astype(bool)
    out["why_entry"], out["why_exit"] = why_entry, why_exit
    for k, v in extra.items():
        out[k] = v
    return out


_tl = threading.local()


@contextmanager
def quiet():
    """Skip the per-bar Korean reasons (bulk studies only need the flags). Thread-local."""
    _tl.quiet = True
    try:
        yield
    finally:
        _tl.quiet = False


def _fmt(vals, f: Callable) -> list[str]:
    if getattr(_tl, "quiet", False):
        return [""] * len(vals[0])
    return [f(*v) if all(np.isfinite(x) for x in v) else "" for v in zip(*vals)]


def supertrend(h: pd.Series, lo: pd.Series, c: pd.Series, n: int = 10, mult: float = 3.0) -> pd.Series:
    """+1 in an up trend, −1 in a down trend (classic final-band algorithm)."""
    a = atr(h, lo, c, n).to_numpy()
    hl2 = ((h + lo) / 2).to_numpy()
    cc = c.to_numpy()
    up, dn = hl2 + mult * a, hl2 - mult * a
    fu, fd = up.copy(), dn.copy()
    trend = np.ones(len(cc))
    for i in range(1, len(cc)):
        if not np.isfinite(a[i]) or not np.isfinite(a[i - 1]):
            trend[i] = trend[i - 1]
            continue
        fu[i] = up[i] if up[i] < fu[i - 1] or cc[i - 1] > fu[i - 1] else fu[i - 1]
        fd[i] = dn[i] if dn[i] > fd[i - 1] or cc[i - 1] < fd[i - 1] else fd[i - 1]
        trend[i] = 1 if cc[i] > fu[i - 1] else -1 if cc[i] < fd[i - 1] else trend[i - 1]
    s = pd.Series(trend, index=c.index)
    s[~np.isfinite(a)] = np.nan
    return s


# ------------------------------------------------------------------ daily rules
def _rsi2(b, p):
    c = b["close"]
    r, s200, s5 = rsi(c, 2), sma(c, 200), sma(c, int(p["exit_sma"]))
    return _frame(b, (c > s200) & (r < p["rsi_entry"]), c > s5,
                  _fmt((r,), lambda x: f"RSI(2) {x:.1f} < {p['rsi_entry']} · 200일선 위"),
                  _fmt((c, s5), lambda x, y: f"종가 {x:,.2f} > {int(p['exit_sma'])}일선 {y:,.2f}"))


def _cum_rsi2(b, p):
    c = b["close"]
    r = rsi(c, 2)
    cum = r + r.shift(1)
    return _frame(b, (c > sma(c, 200)) & (cum < p["cum_entry"]), r > p["rsi_exit"],
                  _fmt((cum,), lambda x: f"2일 누적 RSI(2) {x:.1f} < {p['cum_entry']} · 200일선 위"),
                  _fmt((r,), lambda x: f"RSI(2) {x:.1f} > {p['rsi_exit']}"))


def _r3(b, p):
    c = b["close"]
    r = rsi(c, 2)
    falling = (r < r.shift(1)) & (r.shift(1) < r.shift(2))
    entry = (c > sma(c, 200)) & falling & (r.shift(2) < 60) & (r < p["rsi_entry"])
    return _frame(b, entry, r > p["rsi_exit"],
                  _fmt((r,), lambda x: f"RSI(2) 3일 연속 하락 → {x:.1f} · 200일선 위"),
                  _fmt((r,), lambda x: f"RSI(2) {x:.1f} > {p['rsi_exit']}"))


def _double7(b, p):
    c, n = b["close"], int(p["days"])
    lo_n, hi_n = c.rolling(n, min_periods=n).min(), c.rolling(n, min_periods=n).max()
    return _frame(b, (c > sma(c, 200)) & (c <= lo_n), c >= hi_n,
                  [f"종가가 {n}일 최저 · 200일선 위"] * len(b), [f"종가가 {n}일 최고"] * len(b))


def _ibs(b, p):
    rng = (b["high"] - b["low"]).replace(0, np.nan)
    ibs = (b["close"] - b["low"]) / rng
    return _frame(b, ibs < p["entry"], ibs > p["exit"],
                  _fmt((ibs,), lambda x: f"IBS {x:.2f} < {p['entry']} (저가 부근 마감)"),
                  _fmt((ibs,), lambda x: f"IBS {x:.2f} > {p['exit']} (고가 부근 마감)"))


def _bb(b, p):
    c = b["close"]
    bb = bollinger(c, 20, float(p["k"]))
    return _frame(b, (c < bb["bb_lower"]) & (c > sma(c, 200)), c > bb["bb_mid"],
                  _fmt((c, bb["bb_lower"]), lambda x, y: f"종가 {x:,.2f} < 볼린저 하단 {y:,.2f} · 200일선 위"),
                  _fmt((c, bb["bb_mid"]), lambda x, y: f"종가 {x:,.2f} > 중심선 {y:,.2f}"))


def _williams(b, p):
    c, n = b["close"], int(p["period"])
    hh, ll = b["high"].rolling(n).max(), b["low"].rolling(n).min()
    wr = -100 * (hh - c) / (hh - ll).replace(0, np.nan)
    return _frame(b, (c > sma(c, 200)) & (wr < p["entry"]), wr > p["exit"],
                  _fmt((wr,), lambda x: f"%R({n}) {x:.0f} < {p['entry']} · 200일선 위"),
                  _fmt((wr,), lambda x: f"%R({n}) {x:.0f} > {p['exit']}"))


def _vol_breakout(b, p):
    level = b["open"] + float(p["k"]) * (b["high"].shift(1) - b["low"].shift(1))
    why = _fmt((level,), lambda x: f"시가 + 전일 변동폭 × {p['k']} = {x:,.2f} 돌파")
    return _frame(b, False, False, why, [""] * len(b), level=level)


def _turtle(b, p):
    c, ne, nx = b["close"], int(p["entry_days"]), int(p["exit_days"])
    hh = b["high"].shift(1).rolling(ne, min_periods=ne).max()
    ll = b["low"].shift(1).rolling(nx, min_periods=nx).min()
    return _frame(b, c > hh, c < ll,
                  _fmt((c, hh), lambda x, y: f"종가 {x:,.2f} > {ne}일 고점 {y:,.2f}"),
                  _fmt((c, ll), lambda x, y: f"종가 {x:,.2f} < {nx}일 저점 {y:,.2f}"))


def _golden(b, p):
    c = b["close"]
    f, s = sma(c, int(p["fast"])), sma(c, int(p["slow"]))
    return _frame(b, _cross_up(f, s), _cross_up(s, f),
                  [f"{int(p['fast'])}일선이 {int(p['slow'])}일선 상향 돌파 (골든크로스)"] * len(b),
                  [f"{int(p['fast'])}일선이 {int(p['slow'])}일선 하향 돌파 (데드크로스)"] * len(b))


def _macd(b, p):
    c = b["close"]
    m = macd(c)
    return _frame(b, _cross_up(m["macd"], m["macd_signal"]) & (c > sma(c, 200)), _cross_up(m["macd_signal"], m["macd"]),
                  ["MACD가 시그널선 상향 돌파 · 200일선 위"] * len(b), ["MACD가 시그널선 하향 돌파"] * len(b))


def _supertrend(b, p):
    st = supertrend(b["high"], b["low"], b["close"], int(p["period"]), float(p["mult"]))
    return _frame(b, (st == 1) & (st.shift(1) == -1), (st == -1) & (st.shift(1) == 1),
                  [f"슈퍼트렌드({int(p['period'])}, {p['mult']}) 상승 전환"] * len(b),
                  [f"슈퍼트렌드({int(p['period'])}, {p['mult']}) 하락 전환"] * len(b))


# ------------------------------------------------------------------ intraday rules
def _trend_breakout(b, p):
    c, h = b["close"], b["high"]
    f, s = ema(c, int(p["fast"])), ema(c, int(p["slow"]))
    hh = h.shift(1).rolling(int(p["breakout"]), min_periods=int(p["breakout"])).max()
    return _frame(b, (f > s) & (c > hh), c < f,
                  _fmt((c, hh), lambda x, y: f"종가 {x:,.2f} > 직전 {int(p['breakout'])}봉 고점 {y:,.2f} · EMA{int(p['fast'])} > EMA{int(p['slow'])}"),
                  _fmt((c, f), lambda x, y: f"종가 {x:,.2f} < EMA{int(p['fast'])} {y:,.2f}"))


def _rsi2_intraday(b, p):
    c = b["close"]
    t, r2 = ema(c, int(p["trend"])), rsi(c, 2)
    return _frame(b, (c > t) & (r2 < p["rsi_entry"]), r2 > p["rsi_exit"],
                  _fmt((r2,), lambda x: f"RSI(2) {x:.1f} < {p['rsi_entry']} · 종가가 EMA{int(p['trend'])} 위"),
                  _fmt((r2,), lambda x: f"RSI(2) {x:.1f} > {p['rsi_exit']}"))


def _orb(b, p):
    """Opening range = the first N minutes; buy the first close above its high, stop at its low."""
    n = int(p["minutes"])
    day, mins = _day(b.index), _mins_from_open(b.index)
    in_range = pd.Series(mins < n, index=b.index)
    g = pd.Series(day, index=b.index)
    hi = b["high"].where(in_range).groupby(g).transform("max")
    lo = b["low"].where(in_range).groupby(g).transform("min")
    after = pd.Series(mins >= n, index=b.index)
    brk = (b["close"] > hi) & after
    first = brk & (brk.astype(int).groupby(g).cumsum() == 1)
    return _frame(b, first, False,
                  _fmt((b["close"], hi), lambda x, y: f"종가 {x:,.2f} > 시초 {n}분 고가 {y:,.2f} (첫 돌파)"),
                  [""] * len(b), stop=lo)


def _vwap_reclaim(b, p):
    day = pd.Series(_day(b.index), index=b.index)
    tp = (b["high"] + b["low"] + b["close"]) / 3
    v = b["volume"].fillna(0)
    vw = (tp * v).groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)
    day_open = b["open"].groupby(day).transform("first")
    c = b["close"]
    same_day = day.eq(day.shift(1))
    entry = (c > vw) & (c.shift(1) <= vw.shift(1)) & same_day & (c > day_open) & pd.Series(_mins_from_open(b.index) >= int(p["skip_minutes"]), index=b.index)
    return _frame(b, entry, c < vw,
                  _fmt((c, vw), lambda x, y: f"종가 {x:,.2f}가 VWAP {y:,.2f} 재돌파 · 당일 상승 중"),
                  _fmt((c, vw), lambda x, y: f"종가 {x:,.2f} < VWAP {y:,.2f}"))


QS = "QuantifiedStrategies"
KINDS: dict[str, Kind] = {k.id: k for k in [
    Kind("rsi2", "코너스 RSI(2)", "평균회귀", "daily", ("1d",),
         "200일선 위에서 RSI(2)가 10 아래로 떨어지면 매수, 종가가 5일선을 넘으면 매도. 손절 없음(원전 규칙).",
         {"rsi_entry": 10, "exit_sma": 5, "atr_stop": 0.0}, {"rsi_entry": "RSI(2) 진입", "exit_sma": "청산 이평", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _rsi2, source=(f"{QS} - RSI 2 Strategy", "https://www.quantifiedstrategies.com/rsi-2-strategy/")),
    Kind("cum_rsi2", "코너스 누적 RSI(2)", "평균회귀", "daily", ("1d",),
         "200일선 위에서 최근 2일 RSI(2) 합이 35 아래면 매수, RSI(2)가 65를 넘으면 매도.",
         {"cum_entry": 35, "rsi_exit": 65, "atr_stop": 0.0}, {"cum_entry": "2일 합 진입", "rsi_exit": "RSI(2) 청산", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _cum_rsi2, source=("Connors & Alvarez, Short Term Trading Strategies That Work (2008)", "https://www.quantifiedstrategies.com/connors-rsi/")),
    Kind("r3", "코너스 R3", "평균회귀", "daily", ("1d",),
         "200일선 위, RSI(2)가 3일 연속 하락(첫날 60 미만)해 10 아래면 매수, RSI(2) 70 초과에 매도.",
         {"rsi_entry": 10, "rsi_exit": 70, "atr_stop": 0.0}, {"rsi_entry": "RSI(2) 진입", "rsi_exit": "RSI(2) 청산", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _r3, source=("Connors - R3 strategy", "https://quantifiedstrategies.substack.com/p/larry-connors-r3-strategy-it-still-795")),
    Kind("double7", "더블 7s", "평균회귀", "daily", ("1d",),
         "200일선 위에서 종가가 7일 최저면 매수, 7일 최고면 매도.",
         {"days": 7, "atr_stop": 0.0}, {"days": "기준 일수", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _double7, source=("Connors & Alvarez - Double 7s", "https://www.quantifiedstrategies.com/double-7-trading-strategy/")),
    Kind("ibs", "IBS 평균회귀", "평균회귀", "daily", ("1d",),
         "IBS = (종가 − 저가) / (고가 − 저가). 0.2 미만(저가 부근 마감)이면 매수, 0.8 초과면 매도.",
         {"entry": 0.2, "exit": 0.8, "atr_stop": 0.0}, {"entry": "IBS 진입", "exit": "IBS 청산", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _ibs, source=(f"{QS} - IBS indicator", "https://www.quantifiedstrategies.com/ibs-internal-bar-strength-indicator-strategies/")),
    Kind("bb_reversion", "볼린저 하단 반등", "평균회귀", "daily", ("1d",),
         "200일선 위에서 종가가 볼린저 하단(20일, 2σ) 아래면 매수, 중심선 위로 오면 매도.",
         {"k": 2.0, "atr_stop": 0.0}, {"k": "밴드 폭 (σ)", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _bb, source=(f"{QS} - Bollinger Bands strategy", "https://www.quantifiedstrategies.com/bollinger-bands-trading-strategy/")),
    Kind("williams_r", "윌리엄스 %R(2)", "평균회귀", "daily", ("1d",),
         "200일선 위에서 %R(2)가 −90 아래면 매수, −20 위면 매도.",
         {"period": 2, "entry": -90, "exit": -20, "atr_stop": 0.0}, {"period": "기간", "entry": "진입", "exit": "청산", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _williams, source=(f"{QS} - Williams %R", "https://www.quantifiedstrategies.com/williams-r-trading-strategy/")),
    Kind("vol_breakout", "래리 윌리엄스 변동성 돌파", "돌파", "daily", ("1d",),
         "오늘 시가 + 전일 (고가 − 저가) × k를 장중에 넘으면 매수, 그날 종가에 매도 (당일 매매).",
         {"k": 0.5, "atr_stop": 0.0}, {"k": "k", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _vol_breakout, day_trade=True, intrabar=True,
         source=("WH SelfInvest - Larry Williams Volatility Break-out", "https://whselfinvest.com/en-be/trading-platform/free-trading-strategies/tradingsystem/56-volatility-break-out-larry-williams-free")),
    Kind("turtle", "터틀 돌파 (20/10)", "추세추종", "daily", ("1d",),
         "종가가 20일 고점을 넘으면 매수, 10일 저점 아래면 매도, 손절 2 ATR (터틀 System 1).",
         {"entry_days": 20, "exit_days": 10, "atr_stop": 2.0}, {"entry_days": "진입 일수", "exit_days": "청산 일수", "atr_stop": "손절 ATR 배수"},
         _turtle, source=("Turtle trading rules (Faith, Way of the Turtle)", "https://www.quantifiedstrategies.com/turtle-trading-strategy/")),
    Kind("golden_cross", "골든크로스 (50/200)", "추세추종", "daily", ("1d",),
         "50일선이 200일선을 상향 돌파하면 매수, 하향 돌파하면 매도.",
         {"fast": 50, "slow": 200, "atr_stop": 0.0}, {"fast": "단기", "slow": "장기", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _golden, source=(f"{QS} - Golden cross", "https://www.quantifiedstrategies.com/golden-cross-trading-strategy/")),
    Kind("macd", "MACD 교차", "추세추종", "daily", ("1d",),
         "200일선 위에서 MACD(12,26,9)가 시그널선을 상향 돌파하면 매수, 하향 돌파하면 매도.",
         {"atr_stop": 0.0}, {"atr_stop": "손절 ATR 배수 (0=없음)"},
         _macd, source=(f"{QS} - MACD strategy", "https://www.quantifiedstrategies.com/macd-trading-strategy/")),
    Kind("supertrend", "슈퍼트렌드 (10, 3)", "추세추종", "daily", ("1d",),
         "슈퍼트렌드(10, 3)가 상승으로 바뀌면 매수, 하락으로 바뀌면 매도.",
         {"period": 10, "mult": 3.0, "atr_stop": 0.0}, {"period": "ATR 기간", "mult": "배수", "atr_stop": "손절 ATR 배수 (0=없음)"},
         _supertrend, source=(f"{QS} - Supertrend", "https://www.quantifiedstrategies.com/supertrend-indicator-trading-strategy/")),
    Kind("orb", "시초가 범위 돌파 (ORB 30분)", "돌파", "intraday", ("5m",),
         "개장 후 30분 고가를 5분봉 종가로 처음 넘으면 매수, 손절은 30분 저가, 장 마감에 청산 (당일 매매).",
         {"minutes": 30}, {"minutes": "시초 범위 (분)"},
         _orb, day_trade=True, source=(f"{QS} - Opening Range Breakout", "https://www.quantifiedstrategies.com/opening-range-breakout-strategy/")),
    Kind("vwap_reclaim", "VWAP 재돌파", "추세추종", "intraday", ("5m", "15m"),
         "당일 상승 중(종가 > 당일 시가)에 종가가 VWAP를 다시 넘으면 매수, VWAP 아래로 마감하면 매도, 장 마감 청산.",
         {"skip_minutes": 15, "atr_stop": 2.0}, {"skip_minutes": "개장 후 제외(분)", "atr_stop": "손절 ATR 배수"},
         _vwap_reclaim, day_trade=True),
    Kind("trend_breakout", "추세 돌파 (분봉)", "추세추종", "intraday", ("5m", "15m", "1h"),
         "EMA(단기) > EMA(장기) 정배열에서 종가가 직전 N봉 고점을 넘으면 매수, 종가가 EMA(단기) 아래거나 손절선(진입가 − ATR×k)이면 매도.",
         {"fast": 20, "slow": 50, "breakout": 20, "atr_stop": 2.0}, {"fast": "EMA 단기", "slow": "EMA 장기", "breakout": "돌파 기준 봉 수", "atr_stop": "손절 ATR 배수"},
         _trend_breakout),
    Kind("rsi2_pullback", "상승 추세 눌림목 (분봉 RSI2)", "평균회귀", "intraday", ("5m", "15m", "1h"),
         "종가가 EMA(추세) 위인 상승 추세에서 RSI(2)가 진입값 아래면 매수, 청산값 위거나 손절선이면 매도.",
         {"trend": 100, "rsi_entry": 10, "rsi_exit": 70, "atr_stop": 2.5}, {"trend": "추세 EMA", "rsi_entry": "RSI2 진입", "rsi_exit": "RSI2 청산", "atr_stop": "손절 ATR 배수"},
         _rsi2_intraday),
]}


# ------------------------------------------------------------------ bars
def regular(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["session"] == "regular"] if "session" in df else df
    return out[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "high", "low", "close"])


def adjusted_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC scaled by adj_close / close so dividends count (splits are already in Yahoo's prices)."""
    out = df[["open", "high", "low", "close", "volume"]].copy()
    if "adj_close" in df and df["adj_close"].notna().all():
        f = df["adj_close"] / df["close"]
        for c in ("open", "high", "low", "close"):
            out[c] = out[c] * f
    return out.dropna(subset=["open", "high", "low", "close"])


def completed(df: pd.DataFrame, interval: str, now: pd.Timestamp) -> pd.DataFrame:
    """Drop the bar that is still forming (intraday bars are stamped with their start; a daily bar is complete
    once its session is over, i.e. any date before today in New York)."""
    if interval == "1d":
        today = now.tz_convert(ET).date()
        return df[pd.Index(df.index.date) < today]
    end = df.index + pd.Timedelta(minutes=INTERVALS[interval])
    return df[end <= now]


def signals(kind: str, bars: pd.DataFrame, p: dict) -> pd.DataFrame:
    k = KINDS[kind]
    out = k.signals(bars, {**k.defaults, **p})
    if float(p.get("atr_stop", k.defaults.get("atr_stop", 0)) or 0) > 0:
        out["entry"] &= out["atr"].notna()  # never enter without a computable stop
    return out


def session_last(index: pd.DatetimeIndex) -> np.ndarray:
    """True on the last bar of each trading day."""
    d = pd.Index(_day(index))
    return np.r_[d[1:] != d[:-1], True]


# ------------------------------------------------------------------ simulation
def simulate(bars: pd.DataFrame, kind: str, params: dict, *, notional: float = 1000.0, fee_bps: float = 25.0,
             slippage_bps: float = 5.0, flatten: bool | None = None) -> dict:
    """Backtest one symbol: the rule's trades (raw prices) plus metrics after the given costs."""
    trades, n_bars, years, bh = run(bars, kind, params, flatten=flatten)
    if trades is None:
        return {"error": "봉 데이터가 부족합니다", "trades": [], "metrics": None}
    return {"trades": priced(trades, fee_bps, slippage_bps, notional),
            "metrics": trade_metrics(trades, fee_bps, slippage_bps, notional=notional, n_bars=n_bars, years=years, buy_hold_pct=bh,
                                     start=bars.index[0], end=bars.index[-1])}


def run(bars: pd.DataFrame, kind: str, params: dict, *, flatten: bool | None = None):
    """The rule over `bars`. Returns (trades, bars used, years, buy-and-hold %) or (None, ...) if too short."""
    k = KINDS[kind]
    daily = k.timeframe == "daily"
    bars = bars if daily else regular(bars)
    if len(bars) < (210 if daily else 60):
        return None, 0, 0.0, None
    p = {**k.defaults, **params}
    # day-trade rules always end flat; the "close before the bell" option only applies to intraday rules
    flat = k.day_trade or (not daily and (flatten if flatten is not None else True))
    sig = signals(kind, bars, p)
    o, h, lo, c = (bars[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    en, ex, a = sig["entry"].to_numpy(bool), sig["exit"].to_numpy(bool), sig["atr"].to_numpy(float)
    level = sig["level"].to_numpy(float) if "level" in sig else None
    rule_stop = sig["stop"].to_numpy(float) if "stop" in sig else None
    k_atr = float(p.get("atr_stop", 0) or 0)
    last_of_day = np.ones(len(bars), bool) if daily else session_last(bars.index)
    trades: list[dict] = []
    pos = None   # (entry_i, entry_px, stop)
    pending = None

    def stop_for(i_sig: int, px: float) -> float | None:
        if rule_stop is not None and np.isfinite(rule_stop[i_sig]):
            return float(rule_stop[i_sig])
        if k_atr > 0 and np.isfinite(a[i_sig]):
            return px - k_atr * a[i_sig]
        return None

    def close(i: int, px: float, why: str) -> None:
        nonlocal pos
        trades.append({"entry_time": bars.index[pos[0]].isoformat(), "exit_time": bars.index[i].isoformat(),
                       "entry": pos[1], "exit": px, "bars": i - pos[0], "why": why})
        pos = None

    for i in range(len(bars)):
        if pending == "buy" and pos is None:
            pos = (i, o[i], stop_for(i - 1, o[i]))
        elif pending == "sell" and pos is not None:
            close(i, o[i], "청산 신호")
        pending = None
        if level is not None and pos is None and np.isfinite(level[i]) and h[i] >= level[i] and i > 0:
            px = max(o[i], level[i])
            pos = (i, px, stop_for(i - 1, px))
        if pos is not None and pos[2] is not None and lo[i] <= pos[2]:
            close(i, min(o[i], pos[2]), "손절")
        if pos is not None and flat and last_of_day[i]:
            close(i, c[i], "장 마감 청산")
        if i == len(bars) - 1:
            break
        if flat and last_of_day[i] and not daily:
            continue  # intraday: no new order on the session's last bar
        if pos is None and en[i]:
            pending = "buy"
        elif pos is not None and ex[i]:
            pending = "sell"
    if pos is not None:
        close(len(bars) - 1, c[-1], "기간 끝 (미청산)")
    span = (bars.index[-1] - bars.index[0]).days / 365.25
    bh = (c[-1] / o[0] - 1) * 100
    return trades, len(bars), span, bh


def net_return(entry: float, exit_: float, fee_bps: float, slippage_bps: float) -> float:
    f, s = fee_bps / 1e4, slippage_bps / 1e4
    return (exit_ * (1 - s) * (1 - f)) / (entry * (1 + s) * (1 + f)) - 1


def priced(trades: list[dict], fee_bps: float, slippage_bps: float, notional: float) -> list[dict]:
    out = []
    for t in trades:
        r = net_return(t["entry"], t["exit"], fee_bps, slippage_bps)
        g = t["exit"] / t["entry"] - 1
        out.append({**t, "ret_pct": r * 100, "pl": notional * r, "gross": notional * g, "fees": notional * (g - r)})
    return out


def trade_metrics(trades: list[dict], fee_bps: float, slippage_bps: float, *, notional: float = 1000.0, n_bars: int = 0,
                  years: float = 0.0, buy_hold_pct: float | None = None, start=None, end=None) -> dict:
    rs = np.array([net_return(t["entry"], t["exit"], fee_bps, slippage_bps) for t in trades])
    n = len(rs)
    wins, losses = rs[rs > 0], rs[rs <= 0]
    curve = np.cumsum(rs) * notional
    dd = float((curve - np.maximum.accumulate(np.r_[0.0, curve])[1:]).min()) if n else 0.0
    held = sum(t["bars"] + 1 for t in trades)
    days = years * 252 if years else None
    return {"trades": n, "win_rate": float((rs > 0).mean() * 100) if n else None,
            "avg_ret_pct": float(rs.mean() * 100) if n else None,
            "avg_win_pct": float(wins.mean() * 100) if len(wins) else None, "avg_loss_pct": float(losses.mean() * 100) if len(losses) else None,
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
            "total_pl": float(rs.sum() * notional), "gross_pl": float(sum(t["exit"] / t["entry"] - 1 for t in trades) * notional),
            "fees": float(sum((t["exit"] / t["entry"] - 1) - r for t, r in zip(trades, rs)) * notional),
            "total_ret_pct": float(rs.sum() * 100), "ann_ret_pct": float(rs.sum() * 100 / years) if years else None,
            "max_dd": dd, "max_dd_pct": dd / notional * 100, "exposure_pct": held / n_bars * 100 if n_bars else None,
            "trades_per_day": n / days if days else None, "days": int(round(days)) if days else None,
            "buy_hold_pct": buy_hold_pct, "from": start.isoformat() if start is not None else None,
            "to": end.isoformat() if end is not None else None}
