"""Event-driven daily backtester with walk-forward / out-of-sample validation.

Timing rules (no look-ahead):
  * Signals are evaluated on bar t using indicator values at the close of t (all causal).
  * Orders fill at the OPEN of bar t+1 with slippage; commission is charged per side.
  * Equity is marked to market at each close. Prices are dividend+split adjusted
    (OHLC scaled by adj_close/close) so returns are total returns.
  * A period backtest starts flat on its first day; indicator warm-up may use earlier
    history (which is the past, so it is allowed).

Walk-forward:
  * "split": choose parameters on TRAIN, confirm on VALIDATION, report once on OUT-OF-SAMPLE,
    then show LIVE (period after OOS) with the frozen parameters.
  * "rolling": repeated train->test folds; the stitched test segments form the OOS record.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import compute_all, sma


# ------------------------------------------------------------------ strategies
@dataclass
class Strategy:
    id: str
    name: str
    description: str
    defaults: dict
    grid: dict
    signals: Callable[[pd.DataFrame, dict, pd.Series | None], tuple[pd.Series, pd.Series]]
    needs_regime: bool = False


def _sig_trend(ind: pd.DataFrame, p: dict, _reg) -> tuple[pd.Series, pd.Series]:
    fast, slow = sma(ind["close"], p["fast"]), sma(ind["close"], p["slow"])
    entry = (fast > slow) & (ind["rsi"] > p["rsi_min"]) & (ind["close"] > ind["sma200"])
    exit_ = (fast < slow) | (ind["close"] < ind["sma200"])
    return entry.fillna(False), exit_.fillna(False)


def _sig_golden(ind: pd.DataFrame, p: dict, _reg) -> tuple[pd.Series, pd.Series]:
    fast, slow = sma(ind["close"], p["fast"]), sma(ind["close"], p["slow"])
    above = fast > slow
    crossed_recent = (above & ~above.shift(1, fill_value=False)).astype(int).rolling(p["cross_window"], min_periods=1).max() > 0
    entry = crossed_recent & above & (ind["rsi"] > p["rsi_min"]) & (ind["volume"] > ind["vol_avg20"])
    exit_ = (fast < slow) | (ind["close"] < slow * (1 - p["stop_below_slow"]))
    return entry.fillna(False), exit_.fillna(False)


def _sig_breakout(ind: pd.DataFrame, p: dict, regime: pd.Series | None) -> tuple[pd.Series, pd.Series]:
    hh = ind["high"].shift(1).rolling(p["entry_window"], min_periods=p["entry_window"]).max()
    ll = ind["low"].shift(1).rolling(p["exit_window"], min_periods=p["exit_window"]).min()
    entry = (ind["close"] > hh) & (ind["vol_ratio"] >= p["vol_mult"])
    if regime is not None:
        entry &= regime.reindex(ind.index).fillna(False).astype(bool)
    exit_ = ind["close"] < ll
    return entry.fillna(False), exit_.fillna(False)


def _sig_pullback(ind: pd.DataFrame, p: dict, _reg) -> tuple[pd.Series, pd.Series]:
    entry = (ind["close"] > ind["sma200"]) & (ind["rsi"] < p["rsi_entry"]) & (ind["sma50"] > ind["sma200"])
    exit_ = (ind["rsi"] > p["rsi_exit"]) | (ind["close"] < ind["sma200"] * 0.95)
    return entry.fillna(False), exit_.fillna(False)


STRATEGIES: dict[str, Strategy] = {s.id: s for s in [
    Strategy("A", "추세 추종",
             "SMA(단기) > SMA(장기), RSI > 기준값, 주가 > SMA200일 때 매수. "
             "SMA(단기) < SMA(장기) 또는 주가 < SMA200이면 매도.",
             {"fast": 20, "slow": 60, "rsi_min": 50},
             {"fast": [10, 20, 30], "slow": [50, 60, 100], "rsi_min": [45, 50, 55]}, _sig_trend),
    Strategy("B", "골든크로스 모멘텀",
             "SMA(단기)가 SMA(장기)를 상향 돌파(골든크로스)한 뒤 N일 이내, RSI > 기준값이고 거래량이 20일 평균 이상이면 매수. "
             "데드크로스 또는 SMA(장기)보다 X% 이상 아래에서 마감하면 매도.",
             {"fast": 50, "slow": 200, "rsi_min": 55, "cross_window": 10, "stop_below_slow": 0.03},
             {"fast": [20, 50], "slow": [100, 200], "rsi_min": [50, 55], "cross_window": [10, 20],
              "stop_below_slow": [0.03]}, _sig_golden),
    Strategy("C", "돌파 + 거래량 + 위험선호",
             "직전 N일 고점을 종가로 돌파하고 거래량이 20일 평균의 k배 이상이며, 시장 국면이 위험선호"
             "(지수 > SMA200, VIX < 25)일 때만 매수. 직전 M일 저점 아래로 마감하면 매도.",
             {"entry_window": 55, "exit_window": 20, "vol_mult": 1.5},
             {"entry_window": [20, 55], "exit_window": [10, 20], "vol_mult": [1.2, 1.5]}, _sig_breakout,
             needs_regime=True),
    Strategy("D", "상승 추세 속 RSI 눌림목",
             "상승 추세에서만 역추세 매수: 주가 > SMA200, SMA50 > SMA200이고 RSI < 진입값이면 매수. "
             "RSI > 청산값이거나 SMA200보다 5% 아래로 마감하면 매도. (RSI < 30 단독으로는 사용하지 않음)",
             {"rsi_entry": 35, "rsi_exit": 60},
             {"rsi_entry": [30, 35, 40], "rsi_exit": [55, 60, 70]}, _sig_pullback),
]}


# ------------------------------------------------------------------ engine
def _adjusted(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "adj_close" in out and out["adj_close"].notna().all():
        f = out["adj_close"] / out["close"]
        for c in ("open", "high", "low", "close"):
            out[c] = out[c] * f
    return out


def run_period(ind: pd.DataFrame, entry: pd.Series, exit_: pd.Series, start: pd.Timestamp, end: pd.Timestamp, *,
               commission_bps: float = 5.0, slippage_bps: float = 5.0, initial: float = 10_000.0) -> dict:
    """Simulate one period. `ind` must already be adjusted; signals aligned to ind.index."""
    mask = (ind.index >= start) & (ind.index <= end)
    idx = np.where(mask)[0]
    if len(idx) < 2:
        return {"error": "기간이 너무 짧습니다"}
    o, c = ind["open"].to_numpy(float), ind["close"].to_numpy(float)
    en, ex = entry.to_numpy(bool), exit_.to_numpy(bool)
    cost, slip = commission_bps / 1e4, slippage_bps / 1e4
    cash, shares = initial, 0.0
    equity = []
    trades = []
    pending: str | None = None
    entry_px = entry_i = None
    for i in idx:
        # 1) fill orders decided at the previous close, at today's open
        if pending == "buy" and shares == 0:
            px = o[i] * (1 + slip)
            shares = cash * (1 - cost) / px
            cash = 0.0
            entry_px, entry_i = px, i
        elif pending == "sell" and shares > 0:
            px = o[i] * (1 - slip)
            cash = shares * px * (1 - cost)
            trades.append({"entry_date": str(ind.index[entry_i].date()), "exit_date": str(ind.index[i].date()),
                           "entry_price": entry_px, "exit_price": px, "return": (px * (1 - cost)) / (entry_px / (1 - cost)) - 1,
                           "bars": int(i - entry_i), "open": False})
            shares = 0.0
        pending = None
        # 2) mark to market at the close
        equity.append(cash + shares * c[i])
        # 3) decide at the close (executed next bar); no new orders on the period's last bar
        if i == idx[-1]:
            break
        if shares == 0 and en[i]:
            pending = "buy"
        elif shares > 0 and ex[i]:
            pending = "sell"
    if shares > 0:
        last = idx[-1]
        trades.append({"entry_date": str(ind.index[entry_i].date()), "exit_date": str(ind.index[last].date()),
                       "entry_price": entry_px, "exit_price": c[last],
                       "return": c[last] / (entry_px / (1 - cost)) - 1, "bars": int(last - entry_i), "open": True})
    eq = pd.Series(equity, index=ind.index[idx[:len(equity)]])
    in_mkt = _exposure(eq.index, trades)
    return {"equity": eq, "trades": trades, "metrics": metrics(eq, trades, in_mkt)}


def _exposure(index: pd.DatetimeIndex, trades: list[dict]) -> float:
    if not len(index):
        return 0.0
    m = np.zeros(len(index), dtype=bool)
    for t in trades:
        m |= (index >= pd.Timestamp(t["entry_date"])) & (index <= pd.Timestamp(t["exit_date"]))
    return float(m.mean())


def metrics(eq: pd.Series, trades: list[dict] | None = None, exposure: float | None = None) -> dict:
    if len(eq) < 2:
        return {}
    r = eq.pct_change().dropna()
    days = (eq.index[-1] - eq.index[0]).days or 1
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (365.25 / days) - 1 if eq.iloc[-1] > 0 else -1.0
    vol = r.std() * math.sqrt(252)
    sharpe = (r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else None
    downside = r[r < 0]
    dd_std = math.sqrt((downside ** 2).mean()) if len(downside) else 0
    sortino = (r.mean() / dd_std * math.sqrt(252)) if dd_std > 0 else None
    peak = eq.cummax()
    dd = eq / peak - 1
    mdd = float(dd.min())
    out = {"total_return": float(total), "cagr": float(cagr), "volatility": float(vol),
           "sharpe": None if sharpe is None else float(sharpe), "sortino": None if sortino is None else float(sortino),
           "max_drawdown": mdd, "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
           "days": int(len(eq))}
    if trades is not None:
        rets = np.array([t["return"] for t in trades])
        wins, losses = rets[rets > 0], rets[rets <= 0]
        out.update({
            "trades": int(len(rets)), "win_rate": float((rets > 0).mean()) if len(rets) else None,
            "avg_win": float(wins.mean()) if len(wins) else None, "avg_loss": float(losses.mean()) if len(losses) else None,
            "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else
            (None if not len(wins) else float("inf")),
            "avg_holding_days": float(np.mean([t["bars"] for t in trades])) if trades else None,
            "best_trade": float(rets.max()) if len(rets) else None, "worst_trade": float(rets.min()) if len(rets) else None,
            "exposure": exposure,
        })
    return out


def buy_and_hold(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict | None:
    d = df[(df.index >= start) & (df.index <= end)]
    if len(d) < 2:
        return None
    eq = d["close"] / d["close"].iloc[0] * 10_000
    return {"metrics": metrics(eq), "equity": eq}


def regime_series(bench_close: pd.Series | None, vix_close: pd.Series | None) -> pd.Series | None:
    """Historical Risk-On proxy computable at each date: benchmark > SMA200 and VIX < 25."""
    if bench_close is None or bench_close.dropna().empty:
        return None
    on = bench_close > bench_close.rolling(200, min_periods=200).mean()
    if vix_close is not None and not vix_close.dropna().empty:
        on &= vix_close.reindex(bench_close.index).ffill() < 25
    return on


def _score(m: dict) -> float:
    """Selection objective: Sharpe, penalised when there are too few trades to trust it."""
    s = m.get("sharpe")
    if s is None or (m.get("trades") or 0) < 3:
        return -9.0
    return s


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    return compute_all(_adjusted(df))


def backtest(df: pd.DataFrame, strategy_id: str, params: dict | None, start: str, end: str, *,
             benchmarks: dict[str, pd.DataFrame] | None = None, regime: pd.Series | None = None,
             commission_bps: float = 5.0, slippage_bps: float = 5.0, ind: pd.DataFrame | None = None) -> dict:
    st = STRATEGIES[strategy_id]
    p = {**st.defaults, **(params or {})}
    ind = ind if ind is not None else prepare(df)
    en, ex = st.signals(ind, p, regime if st.needs_regime else None)
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    res = run_period(ind, en, ex, s, e, commission_bps=commission_bps, slippage_bps=slippage_bps)
    if "error" in res:
        return res
    bench = {"단순 보유": buy_and_hold(ind, s, e)}
    for name, bdf in (benchmarks or {}).items():
        bench[name] = buy_and_hold(bdf, s, e)
    return {"strategy": {"id": st.id, "name": st.name, "description": st.description, "params": p},
            "metrics": res["metrics"], "trades": res["trades"], "equity": res["equity"],
            "benchmarks": {k: v for k, v in bench.items() if v}}


def walk_forward_split(df: pd.DataFrame, strategy_id: str, periods: dict[str, tuple[str, str]], *,
                       regime: pd.Series | None = None, commission_bps: float = 5.0, slippage_bps: float = 5.0,
                       top_k: int = 3) -> dict:
    """periods: {'train': (s,e), 'validation': (s,e), 'test': (s,e), 'live': (s,e) optional}."""
    st = STRATEGIES[strategy_id]
    ind = prepare(df)
    keys, vals = zip(*st.grid.items())
    combos = [dict(zip(keys, v)) for v in itertools.product(*vals)]
    combos = [c for c in combos if not ("fast" in c and "slow" in c and c["fast"] >= c["slow"])]

    def run(p, period):
        en, ex = st.signals(ind, {**st.defaults, **p}, regime if st.needs_regime else None)
        s, e = (pd.Timestamp(x) for x in periods[period])
        return run_period(ind, en, ex, s, e, commission_bps=commission_bps, slippage_bps=slippage_bps)

    train = []
    for p in combos:
        r = run(p, "train")
        if "error" not in r:
            train.append((p, r["metrics"]))
    if not train:
        return {"error": "학습 기간이 너무 짧습니다"}
    train.sort(key=lambda x: _score(x[1]), reverse=True)
    finalists = train[:top_k]
    val = []
    for p, m_tr in finalists:
        r = run(p, "validation")
        if "error" not in r:
            val.append((p, m_tr, r["metrics"]))
    if not val:
        return {"error": "검증 기간이 너무 짧습니다"}
    val.sort(key=lambda x: _score(x[2]), reverse=True)
    chosen, m_train, m_val = val[0]
    out = {"strategy": {"id": st.id, "name": st.name, "description": st.description},
           "chosen_params": {**st.defaults, **chosen},
           "grid_size": len(combos),
           "train_ranking": [{"params": p, "sharpe": m.get("sharpe"), "cagr": m.get("cagr"), "trades": m.get("trades")}
                             for p, m in train[:10]],
           "periods": {}}
    out["periods"]["train"] = {"range": periods["train"], "metrics": m_train, "role": "파라미터 선정 (표본 내)"}
    out["periods"]["validation"] = {"range": periods["validation"], "metrics": m_val,
                                    "role": f"학습 상위 {len(finalists)}개 후보 중 선택"}
    for key, role in (("test", "표본 외 - 고정 파라미터로 1회 평가"),
                      ("live", "실전 모니터링 - 고정 파라미터")):
        if key in periods and periods[key][0] and periods[key][1]:
            r = run(chosen, key)
            if "error" not in r:
                bh = buy_and_hold(ind, *(pd.Timestamp(x) for x in periods[key]))
                out["periods"][key] = {"range": periods[key], "metrics": r["metrics"], "role": role,
                                       "trades": r["trades"], "equity": r["equity"],
                                       "buy_and_hold": bh["metrics"] if bh else None}
    tr_s, te_s = m_train.get("sharpe"), out["periods"].get("test", {}).get("metrics", {}).get("sharpe")
    out["degradation"] = {"train_sharpe": tr_s, "test_sharpe": te_s,
                          "note": "학습 구간 대비 표본 외 성과가 크게 떨어지면 과최적화 신호입니다."}
    return out


def walk_forward_rolling(df: pd.DataFrame, strategy_id: str, *, train_years: int = 3, test_months: int = 6,
                         start: str | None = None, end: str | None = None, regime: pd.Series | None = None,
                         commission_bps: float = 5.0, slippage_bps: float = 5.0) -> dict:
    st = STRATEGIES[strategy_id]
    ind = prepare(df)
    keys, vals = zip(*st.grid.items())
    combos = [dict(zip(keys, v)) for v in itertools.product(*vals)]
    combos = [c for c in combos if not ("fast" in c and "slow" in c and c["fast"] >= c["slow"])]
    first = pd.Timestamp(start) if start else ind.index[0] + pd.DateOffset(years=1)  # indicator warm-up
    last = pd.Timestamp(end) if end else ind.index[-1]
    folds, stitched = [], []
    t0 = first
    sig_cache = {}
    while True:
        tr_s, tr_e = t0, t0 + pd.DateOffset(years=train_years) - pd.Timedelta(days=1)
        te_s, te_e = tr_e + pd.Timedelta(days=1), tr_e + pd.DateOffset(months=test_months)
        if te_s > last:
            break
        te_e = min(te_e, last)
        best, best_m = None, None
        for p in combos:
            key = tuple(sorted(p.items()))
            if key not in sig_cache:
                sig_cache[key] = st.signals(ind, {**st.defaults, **p}, regime if st.needs_regime else None)
            en, ex = sig_cache[key]
            r = run_period(ind, en, ex, tr_s, tr_e, commission_bps=commission_bps, slippage_bps=slippage_bps)
            if "error" in r:
                continue
            if best_m is None or _score(r["metrics"]) > _score(best_m):
                best, best_m = p, r["metrics"]
        if best is None:
            break
        en, ex = sig_cache[tuple(sorted(best.items()))]
        r = run_period(ind, en, ex, te_s, te_e, commission_bps=commission_bps, slippage_bps=slippage_bps)
        if "error" not in r:
            folds.append({"train": [str(tr_s.date()), str(tr_e.date())], "test": [str(te_s.date()), str(te_e.date())],
                          "params": best, "train_sharpe": best_m.get("sharpe"), "test_metrics": r["metrics"]})
            stitched.append(r["equity"].pct_change().fillna(0))
        t0 = t0 + pd.DateOffset(months=test_months)
    if not stitched:
        return {"error": "롤링 워크포워드에 필요한 이력이 부족합니다"}
    rets = pd.concat(stitched)
    rets = rets[~rets.index.duplicated()]
    eq = (1 + rets).cumprod() * 10_000
    return {"strategy": {"id": st.id, "name": st.name}, "folds": folds, "oos_equity": eq, "oos_metrics": metrics(eq),
            "config": {"train_years": train_years, "test_months": test_months}}
