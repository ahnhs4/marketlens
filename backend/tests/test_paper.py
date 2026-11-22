import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.analysis import rules
from app.db import Base, SessionLocal, engine
from app.models import PaperAccount, PaperPosition, PaperStrategy, PaperTrade, User
from app.services import clock, paper, prices


def _bars(closes, start="2026-10-05 13:30", freq="15min", session_days=None):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999, "close": c,
                         "volume": 1e5, "session": "regular"}, index=idx)


# ------------------------------------------------------------------ backtest
def test_breakout_enters_next_bar_and_fees_cut_the_result():
    rng = np.random.default_rng(1)
    closes = 100 + np.cumsum(rng.normal(0.05, 0.3, 300))
    df = _bars(closes, freq="15min")
    res = rules.simulate(df, "trend_breakout", rules.KINDS["trend_breakout"].defaults, notional=1000, fee_bps=25,
                            slippage_bps=5, flatten=False)
    free = rules.simulate(df, "trend_breakout", rules.KINDS["trend_breakout"].defaults, notional=1000, fee_bps=0,
                             slippage_bps=0, flatten=False)
    assert res["metrics"]["trades"] == free["metrics"]["trades"] > 0
    assert res["metrics"]["total_pl"] < free["metrics"]["total_pl"]
    assert res["metrics"]["fees"] == pytest.approx(sum(t["fees"] for t in res["trades"]))
    sig = rules.signals("trend_breakout", rules.regular(df), rules.KINDS["trend_breakout"].defaults)
    first = res["trades"][0]
    i = df.index.get_loc(pd.Timestamp(first["entry_time"]))
    assert sig["entry"].iloc[i - 1]  # filled on the bar after the signal, never the signal bar itself


def test_flatten_closes_every_trade_the_same_day():
    closes = list(np.linspace(100, 130, 26 * 4))  # 4 sessions of 26 fifteen-minute bars, steady rise
    frames = [_bars(closes[d * 26:(d + 1) * 26], start=f"2026-10-0{5 + d} 13:30") for d in range(4)]
    df = pd.concat(frames)
    res = rules.simulate(df, "trend_breakout", {"fast": 5, "slow": 10, "breakout": 5, "atr_stop": 50}, flatten=True)
    for t in res["trades"]:
        assert pd.Timestamp(t["entry_time"]).date() == pd.Timestamp(t["exit_time"]).date()


def test_stop_is_hit_on_a_drop():
    closes = list(np.linspace(100, 110, 80)) + [111, 112, 90, 89, 88]
    res = rules.simulate(_bars(closes), "trend_breakout", {"fast": 5, "slow": 10, "breakout": 5, "atr_stop": 1.0}, flatten=False)
    assert any(t["why"] == "손절" for t in res["trades"])


def test_completed_drops_the_forming_bar():
    df = _bars([100, 101, 102], start="2026-10-05 13:30")
    now = pd.Timestamp("2026-10-05 14:05", tz="UTC")  # the 14:00 bar is still forming
    assert rules.completed(df, "15m", now).index[-1] == pd.Timestamp("2026-10-05 13:45", tz="UTC")


# ------------------------------------------------------------------ live engine
@pytest.fixture
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    u = User(email=f"paper{dt.datetime.now().timestamp()}@example.com", password_hash="x")
    s.add(u)
    s.commit()
    yield s, u
    s.close()


def test_buy_sell_accounting(db):
    s, u = db
    a = paper.account(s, u)
    a.fee_bps, a.slippage_bps = 10, 0
    paper.buy(s, a, "AAPL", 1000, 100.0, "test")
    s.flush()
    pos = paper.positions(s, a)[0]
    assert a.cash == pytest.approx(9000, abs=0.01)
    assert pos.avg_price == pytest.approx(100.1, rel=1e-6)  # the buy fee is part of the cost
    qty, avg = pos.qty, pos.avg_price
    t = paper.sell(s, a, pos, 110.0, "test")
    proceeds = qty * 110 * (1 - 0.001)  # 0.1% sell fee
    assert t.realized_pl == pytest.approx(proceeds - qty * avg, rel=1e-9)
    assert a.cash == pytest.approx(9000 + proceeds, rel=1e-9)


def test_engine_buys_on_signal_then_stops_out_and_halts_on_daily_loss(db, monkeypatch):
    s, u = db
    a = paper.account(s, u)
    st = PaperStrategy(account_id=a.id, name="t", kind="trend_breakout", interval="15m",
                       params={"fast": 5, "slow": 10, "breakout": 5, "atr_stop": 2.0}, symbols=["AAPL"],
                       max_position_usd=5000, flatten_at_close=True, enabled=True, state={})
    s.add(st)
    s.commit()
    closes = list(np.linspace(100, 110, 79)) + [112]
    bars = _bars(closes, start="2026-10-05 00:00")
    now = bars.index[-1] + pd.Timedelta(minutes=16)
    quote = {"p": 112.0}
    monkeypatch.setattr(clock, "status", lambda m, n=None: {"state": "open", "session_close": (now + pd.Timedelta(hours=3)).isoformat()})
    monkeypatch.setattr(prices, "get_intraday", lambda sym, iv, per: bars)
    monkeypatch.setattr(paper, "_price", lambda sym: quote["p"])
    r = paper.run_account(s, a, now.to_pydatetime())
    s.commit()
    assert r["orders"] == 1
    pos = s.execute(paper.select(PaperPosition).where(PaperPosition.account_id == a.id)).scalar_one()
    assert pos.stop_price and pos.stop_price < 112
    # same bar again: nothing new to evaluate, no second buy
    assert paper.run_account(s, a, (now + pd.Timedelta(minutes=1)).to_pydatetime())["orders"] == 0
    # price falls through the stop -> stop-loss sell, and the 50% drop of the position trips the daily limit
    quote["p"] = 60.0
    paper.run_account(s, a, (now + pd.Timedelta(minutes=2)).to_pydatetime())
    s.commit()
    sells = s.execute(paper.select(PaperTrade).where(PaperTrade.account_id == a.id, PaperTrade.side == "sell")).scalars().all()
    assert len(sells) == 1 and "손절" in sells[0].reason
    assert s.get(PaperAccount, a.id).halted_day == (now + pd.Timedelta(minutes=2)).tz_convert(paper.ET).date()


def test_emergency_stop_blocks_everything(db, monkeypatch):
    s, u = db
    a = paper.account(s, u)
    a.paused = True
    monkeypatch.setattr(clock, "status", lambda m, n=None: {"state": "open", "session_close": None})
    assert paper.run_account(s, a)["state"] == "paused"


def test_daily_rule_acts_once_on_yesterdays_bar(db, monkeypatch):
    s, u = db
    a = paper.account(s, u)
    st = PaperStrategy(account_id=a.id, name="d7", kind="double7", interval="1d", params={}, symbols=["SPY"],
                       max_position_usd=2000, flatten_at_close=False, enabled=True, state={})
    s.add(st)
    s.commit()
    close = list(np.linspace(100, 160, 260)) + [150.0]          # yesterday closed at a 7-day low, above the 200-day line
    idx = pd.bdate_range("2025-09-01", periods=len(close))
    c = pd.Series(close, index=idx)
    daily = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6})
    now = pd.Timestamp(idx[-1].date() + pd.Timedelta(days=1)).tz_localize("America/New_York") + pd.Timedelta(hours=10)
    while now.weekday() >= 5:
        now += pd.Timedelta(days=1)
    today_bar = daily.iloc[-1:].copy()
    today_bar.index = [pd.Timestamp(now.date())]
    monkeypatch.setattr(clock, "status", lambda m, n=None: {"state": "open", "session_close": (now + pd.Timedelta(hours=6)).isoformat()})
    monkeypatch.setattr(prices, "get_daily", lambda sym, years=2: type("P", (), {"df": pd.concat([daily, today_bar])})())  # incl. today's forming bar
    monkeypatch.setattr(paper, "_price", lambda sym: 151.0)
    assert paper.run_account(s, a, now.tz_convert("UTC").to_pydatetime())["orders"] == 1
    s.commit()
    t = s.execute(paper.select(PaperTrade).where(PaperTrade.account_id == a.id)).scalar_one()
    assert t.side == "buy" and t.price == pytest.approx(151.0 * 1.0005)  # live quote + slippage
    # later the same day: yesterday's bar was already evaluated -> no second order, no flattening at the close
    later = now + pd.Timedelta(hours=5, minutes=55)
    assert paper.run_account(s, a, later.tz_convert("UTC").to_pydatetime())["orders"] == 0
