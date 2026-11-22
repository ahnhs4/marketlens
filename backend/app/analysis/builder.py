"""User-composed strategies: entry / exit rules assembled from a condition catalog.

A spec is plain JSON so it can be stored, named and replayed:

    {"entry": {"mode": "any" | "all", "conditions": [{"type": "rsi_rebound", "params": {...}}, ...]},
     "exit":  {"mode": "any" | "all", "conditions": [...]},
     "risk":  {"stop_loss_pct": 8, "take_profit_pct": None, "trailing_stop_pct": None, "max_hold_days": None}}

Every condition is causal (uses bar t and earlier only); the backtester fills at the next open.
A bar where the exit rule holds is never an entry bar, so a spec cannot buy what it would sell at once.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import sma

HORIZON_KO = {"short": "단기", "medium": "중기", "long": "장기"}
RSI_COL = {"short": "rsi_short", "medium": "rsi", "long": "rsi_long"}
RSI_N = {"short": 7, "medium": 14, "long": 28}
PAIRS = {"5/20": (5, 20), "20/60": (20, 60), "50/200": (50, 200)}
MA_PERIODS = ["20", "50", "60", "120", "200"]
SCOPE_KO = {"medium_long": "중기·장기 모두", "short": "단기", "medium": "중기", "long": "장기", "all": "단·중·장기 모두"}
MAX_CONDITIONS = 8


def _recent(event: pd.Series, window: int) -> pd.Series:
    """True on a bar when `event` happened on it or within the previous `window - 1` bars."""
    return event.fillna(False).astype(int).rolling(max(1, int(window)), min_periods=1).max() > 0


def _cross_up(a: pd.Series, b: pd.Series | float) -> pd.Series:
    bb = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a > bb) & (a.shift(1) <= bb.shift(1))


def _cross_down(a: pd.Series, b: pd.Series | float) -> pd.Series:
    bb = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a < bb) & (a.shift(1) >= bb.shift(1))


def _ma(ind: pd.DataFrame, n: int) -> pd.Series:
    col = f"sma{n}"
    return ind[col] if col in ind else sma(ind["close"], n)


def _stoch_scope(ind: pd.DataFrame, scope: str, test: Callable[[pd.Series], pd.Series]) -> pd.Series:
    keys = {"medium_long": ("medium", "long"), "all": ("short", "medium", "long")}.get(scope, (scope,))
    out = pd.Series(True, index=ind.index)
    for k in keys:
        out &= test(ind[f"stoch_{k}_k"]).fillna(False)
    return out


# --------------------------------------------------------------------- params
def _int(key, label, default, lo, hi, step=1):
    return {"key": key, "label": label, "kind": "int", "default": default, "min": lo, "max": hi, "step": step}


def _num(key, label, default, lo, hi, step=0.5):
    return {"key": key, "label": label, "kind": "float", "default": default, "min": lo, "max": hi, "step": step}


def _sel(key, label, default, options: dict[str, str]):
    return {"key": key, "label": label, "kind": "select", "default": default,
            "options": [{"value": k, "label": v} for k, v in options.items()]}


P_HORIZON = _sel("horizon", "기간", "medium", {k: f"{v} RSI({RSI_N[k]})" for k, v in HORIZON_KO.items()})
P_STOCH_HZ = _sel("horizon", "기간", "medium", {"short": "단기 5-3-3", "medium": "중기 10-6-6", "long": "장기 20-12-12"})
P_SCOPE = _sel("scope", "범위", "medium_long", SCOPE_KO)
P_PAIR = _sel("pair", "이동평균", "20/60", {k: f"{a}일 · {b}일" for k, (a, b) in PAIRS.items()})
P_MA = _sel("period", "이동평균", "200", {p: f"{p}일선" for p in MA_PERIODS})
P_WINDOW = _int("window", "최근 며칠 이내", 5, 1, 30)


@dataclass
class Condition:
    type: str
    side: str                      # where it is usually used: "entry" | "exit"
    name: str
    help: str
    fn: Callable[[pd.DataFrame, dict, pd.Series | None], pd.Series]
    label: Callable[[dict], str]
    value: Callable[[pd.Series, dict], str] | None = None   # today's reading, for the "지금 상태" panel
    params: list[dict] = field(default_factory=list)
    needs_regime: bool = False


def _f(x, d=1) -> str:
    return "—" if x is None or not np.isfinite(x) else f"{x:.{d}f}"


def _rsi_val(row, p):
    return f"RSI({RSI_N[p['horizon']]}) {_f(row.get(RSI_COL[p['horizon']]))}"


def _stoch_val(row, p):
    keys = {"medium_long": ("medium", "long"), "all": ("short", "medium", "long")}.get(p.get("scope", p.get("horizon")), (p.get("scope", p.get("horizon")),))
    return " · ".join(f"{HORIZON_KO[k]} %K {_f(row.get(f'stoch_{k}_k'))}" for k in keys)


def _ma_val(row, p):
    n = int(p["period"])
    m = row.get(f"sma{n}")
    return "—" if m is None or not np.isfinite(m) or not m else f"{n}일선 대비 {(row['close'] / m - 1) * 100:+.1f}%"


def _pair_val(row, p):
    a, b = PAIRS[p["pair"]]
    fa, sl = row.get(f"sma{a}"), row.get(f"sma{b}")
    if fa is None or sl is None or not np.isfinite(fa) or not np.isfinite(sl) or not sl:
        return "—"
    return f"{a}일선이 {b}일선 {'위' if fa > sl else '아래'} ({(fa / sl - 1) * 100:+.1f}%)"


def _regime_or(ind: pd.DataFrame, regime: pd.Series | None, default: bool) -> pd.Series:
    if regime is None:
        return pd.Series(default, index=ind.index)
    return regime.reindex(ind.index).ffill().fillna(default).astype(bool)


CONDITIONS: dict[str, Condition] = {c.type: c for c in [
    # ---------------------------------------------------------------- entry
    Condition("rsi_rebound", "entry", "RSI 과매도 반등", "RSI가 기준선 아래로 내려갔다가 다시 위로 올라온 직후",
              lambda ind, p, _r: _recent(_cross_up(ind[RSI_COL[p["horizon"]]], p["level"]), p["window"]) & (ind[RSI_COL[p["horizon"]]] > p["level"]),
              lambda p: f"RSI({RSI_N[p['horizon']]})가 {p['level']:g} 아래에서 반등 ({p['window']}일 이내)",
              _rsi_val, [P_HORIZON, _num("level", "과매도 기준", 30, 10, 50, 1), P_WINDOW]),
    Condition("rsi_oversold", "entry", "RSI 과매도", "RSI가 기준선 이하인 동안",
              lambda ind, p, _r: ind[RSI_COL[p["horizon"]]] <= p["level"],
              lambda p: f"RSI({RSI_N[p['horizon']]}) {p['level']:g} 이하",
              _rsi_val, [P_HORIZON, _num("level", "과매도 기준", 30, 10, 50, 1)]),
    Condition("stoch_rebound", "entry", "스토캐스틱 과매도 반등", "%K가 과매도권에서 %D를 위로 돌파한 직후",
              lambda ind, p, _r: _recent(_cross_up(ind[f"stoch_{p['horizon']}_k"], ind[f"stoch_{p['horizon']}_d"])
                                         & (ind[f"stoch_{p['horizon']}_k"].shift(1) <= p["level"]), p["window"]),
              lambda p: f"{HORIZON_KO[p['horizon']]} 스토캐스틱이 {p['level']:g} 아래에서 %D 상향 돌파 ({p['window']}일 이내)",
              _stoch_val, [P_STOCH_HZ, _num("level", "과매도 기준", 20, 5, 40, 1), P_WINDOW]),
    Condition("stoch_oversold", "entry", "스토캐스틱 과매도", "%K가 기준선 이하인 동안",
              lambda ind, p, _r: _stoch_scope(ind, p["scope"], lambda k: k <= p["level"]),
              lambda p: f"스토캐스틱 {SCOPE_KO[p['scope']]} {p['level']:g} 이하",
              _stoch_val, [P_SCOPE, _num("level", "과매도 기준", 20, 5, 40, 1)]),
    Condition("golden_cross", "entry", "골든크로스", "단기 이동평균이 장기 이동평균을 위로 돌파한 직후 (돌파 상태 유지)",
              lambda ind, p, _r: _recent(_cross_up(_ma(ind, PAIRS[p["pair"]][0]), _ma(ind, PAIRS[p["pair"]][1])), p["window"])
              & (_ma(ind, PAIRS[p["pair"]][0]) > _ma(ind, PAIRS[p["pair"]][1])),
              lambda p: f"{PAIRS[p['pair']][0]}일선이 {PAIRS[p['pair']][1]}일선 골든크로스 ({p['window']}일 이내)",
              _pair_val, [P_PAIR, _int("window", "최근 며칠 이내", 10, 1, 60)]),
    Condition("ma_above_ma", "entry", "이동평균 정배열", "단기 이동평균이 장기 이동평균 위에 있는 동안",
              lambda ind, p, _r: _ma(ind, PAIRS[p["pair"]][0]) > _ma(ind, PAIRS[p["pair"]][1]),
              lambda p: f"{PAIRS[p['pair']][0]}일선 > {PAIRS[p['pair']][1]}일선",
              _pair_val, [P_PAIR]),
    Condition("price_above_ma", "entry", "주가가 이동평균 위", "종가가 이동평균 위에 있는 동안",
              lambda ind, p, _r: ind["close"] > _ma(ind, int(p["period"])),
              lambda p: f"종가 > {p['period']}일선",
              _ma_val, [P_MA]),
    Condition("macd_up", "entry", "MACD 상향 돌파", "MACD가 시그널선을 위로 돌파한 직후",
              lambda ind, p, _r: _recent(_cross_up(ind["macd"], ind["macd_signal"]), p["window"]) & (ind["macd"] > ind["macd_signal"]),
              lambda p: f"MACD 시그널 상향 돌파 ({p['window']}일 이내)",
              lambda row, p: f"MACD 히스토그램 {_f(row.get('macd_hist'), 2)}", [P_WINDOW]),
    Condition("breakout", "entry", "신고가 돌파", "종가가 직전 N일 최고가를 넘어선 날",
              lambda ind, p, _r: ind["close"] > ind["high"].shift(1).rolling(int(p["days"]), min_periods=int(p["days"])).max(),
              lambda p: f"{p['days']}일 신고가 돌파",
              None, [_int("days", "기간(일)", 55, 10, 252)]),
    Condition("volume_surge", "entry", "거래량 급증", "거래량이 20일 평균의 몇 배 이상인 날",
              lambda ind, p, _r: ind["vol_ratio"] >= p["mult"],
              lambda p: f"거래량 20일 평균의 {p['mult']:g}배 이상",
              lambda row, p: f"거래량 {_f(row.get('vol_ratio'), 2)}배", [_num("mult", "배수", 1.5, 1, 5, 0.1)]),
    Condition("bb_lower", "entry", "볼린저 하단 터치", "종가가 볼린저밴드(20, 2σ) 하단 이하",
              lambda ind, p, _r: ind["close"] <= ind["bb_lower"],
              lambda p: "볼린저밴드 하단 이하",
              lambda row, p: f"%B {_f(row.get('bb_pctb'), 2)}"),
    Condition("risk_on", "entry", "시장 위험 선호", "기준 지수가 200일선 위이고 VIX가 25 미만인 동안",
              lambda ind, p, r: _regime_or(ind, r, True),
              lambda p: "시장 위험 선호 (지수 > 200일선 · VIX < 25)", needs_regime=True),
    # ----------------------------------------------------------------- exit
    Condition("stoch_overbought", "exit", "스토캐스틱 과매수", "%K가 기준선 이상인 동안",
              lambda ind, p, _r: _stoch_scope(ind, p["scope"], lambda k: k >= p["level"]),
              lambda p: f"스토캐스틱 {SCOPE_KO[p['scope']]} {p['level']:g} 이상",
              _stoch_val, [P_SCOPE, _num("level", "과매수 기준", 80, 60, 95, 1)]),
    Condition("rsi_overbought", "exit", "RSI 과매수", "RSI가 기준선 이상인 동안",
              lambda ind, p, _r: ind[RSI_COL[p["horizon"]]] >= p["level"],
              lambda p: f"RSI({RSI_N[p['horizon']]}) {p['level']:g} 이상",
              _rsi_val, [P_HORIZON, _num("level", "과매수 기준", 70, 55, 90, 1)]),
    Condition("dead_cross", "exit", "데드크로스", "단기 이동평균이 장기 이동평균을 아래로 돌파한 직후",
              lambda ind, p, _r: _recent(_cross_down(_ma(ind, PAIRS[p["pair"]][0]), _ma(ind, PAIRS[p["pair"]][1])), p["window"]),
              lambda p: f"{PAIRS[p['pair']][0]}일선이 {PAIRS[p['pair']][1]}일선 데드크로스 ({p['window']}일 이내)",
              _pair_val, [P_PAIR, _int("window", "최근 며칠 이내", 3, 1, 30)]),
    Condition("price_below_ma", "exit", "주가가 이동평균 아래", "종가가 이동평균 아래로 내려간 동안",
              lambda ind, p, _r: ind["close"] < _ma(ind, int(p["period"])),
              lambda p: f"종가 < {p['period']}일선",
              _ma_val, [P_MA]),
    Condition("near_high", "exit", "52주 고점 근접", "종가가 52주 고점에서 몇 % 이내인 동안",
              lambda ind, p, _r: ind["close"] >= ind["hi52"] * (1 - p["within"] / 100),
              lambda p: f"52주 고점 대비 {p['within']:g}% 이내",
              lambda row, p: "—" if not row.get("hi52") or not np.isfinite(row.get("hi52")) else f"고점 대비 {(row['close'] / row['hi52'] - 1) * 100:+.1f}%",
              [_num("within", "고점 대비(%)", 3, 0, 20, 0.5)]),
    Condition("macd_down", "exit", "MACD 하향 돌파", "MACD가 시그널선을 아래로 돌파한 직후",
              lambda ind, p, _r: _recent(_cross_down(ind["macd"], ind["macd_signal"]), p["window"]),
              lambda p: f"MACD 시그널 하향 돌파 ({p['window']}일 이내)",
              lambda row, p: f"MACD 히스토그램 {_f(row.get('macd_hist'), 2)}", [_int("window", "최근 며칠 이내", 3, 1, 30)]),
    Condition("bb_upper", "exit", "볼린저 상단 터치", "종가가 볼린저밴드(20, 2σ) 상단 이상",
              lambda ind, p, _r: ind["close"] >= ind["bb_upper"],
              lambda p: "볼린저밴드 상단 이상",
              lambda row, p: f"%B {_f(row.get('bb_pctb'), 2)}"),
    Condition("breakdown", "exit", "신저가 이탈", "종가가 직전 N일 최저가 아래로 내려간 날",
              lambda ind, p, _r: ind["close"] < ind["low"].shift(1).rolling(int(p["days"]), min_periods=int(p["days"])).min(),
              lambda p: f"{p['days']}일 신저가 이탈",
              None, [_int("days", "기간(일)", 20, 5, 252)]),
    Condition("risk_off", "exit", "시장 위험 회피", "기준 지수가 200일선 아래이거나 VIX가 25 이상인 동안",
              lambda ind, p, r: ~_regime_or(ind, r, True),
              lambda p: "시장 위험 회피 (지수 < 200일선 또는 VIX ≥ 25)", needs_regime=True),
]}

RISK_FIELDS = [
    {"key": "stop_loss_pct", "label": "손절", "unit": "%", "min": 1, "max": 50, "help": "매수가 대비 이만큼 내리면 매도"},
    {"key": "take_profit_pct", "label": "익절", "unit": "%", "min": 1, "max": 300, "help": "매수가 대비 이만큼 오르면 매도"},
    {"key": "trailing_stop_pct", "label": "추적 손절", "unit": "%", "min": 1, "max": 50, "help": "매수 후 최고 종가에서 이만큼 내리면 매도"},
    {"key": "max_hold_days", "label": "최대 보유", "unit": "거래일", "min": 1, "max": 750, "help": "이 기간이 지나면 매도"},
]

PRESETS = [
    {"id": "my_rules", "name": "내 규칙: 과매도 반등 · 골든크로스 매수 / 과매수 매도",
     "spec": {"entry": {"mode": "any", "conditions": [{"type": "rsi_rebound", "params": {"horizon": "medium", "level": 30, "window": 5}},
                                                      {"type": "golden_cross", "params": {"pair": "20/60", "window": 10}}]},
              "exit": {"mode": "any", "conditions": [{"type": "stoch_overbought", "params": {"scope": "medium_long", "level": 80}}]},
              "risk": {}}},
    {"id": "trend", "name": "추세 추종: 정배열일 때만 보유",
     "spec": {"entry": {"mode": "all", "conditions": [{"type": "ma_above_ma", "params": {"pair": "20/60"}},
                                                      {"type": "price_above_ma", "params": {"period": "200"}}]},
              "exit": {"mode": "any", "conditions": [{"type": "dead_cross", "params": {"pair": "20/60", "window": 1}},
                                                     {"type": "price_below_ma", "params": {"period": "200"}}]},
              "risk": {}}},
    {"id": "pullback", "name": "눌림목: 상승 추세 속 단기 과매도 매수",
     "spec": {"entry": {"mode": "all", "conditions": [{"type": "price_above_ma", "params": {"period": "200"}},
                                                      {"type": "rsi_oversold", "params": {"horizon": "short", "level": 30}}]},
              "exit": {"mode": "any", "conditions": [{"type": "rsi_overbought", "params": {"horizon": "medium", "level": 70}}]},
              "risk": {"stop_loss_pct": 8}}},
    {"id": "breakout", "name": "돌파: 신고가 + 거래량, 추적 손절",
     "spec": {"entry": {"mode": "all", "conditions": [{"type": "breakout", "params": {"days": 55}},
                                                      {"type": "volume_surge", "params": {"mult": 1.5}}]},
              "exit": {"mode": "any", "conditions": [{"type": "breakdown", "params": {"days": 20}}]},
              "risk": {"trailing_stop_pct": 15}}},
]


def catalog() -> dict:
    return {"conditions": [{"type": c.type, "side": c.side, "name": c.name, "help": c.help, "params": c.params,
                            "needs_regime": c.needs_regime} for c in CONDITIONS.values()],
            "risk": RISK_FIELDS, "presets": [{**p, "description": describe(p["spec"])} for p in PRESETS],
            "max_conditions": MAX_CONDITIONS,
            "timing": "조건은 그날 종가로 판정하고 다음 거래일 시가에 체결합니다. 매도 조건이 성립한 날에는 매수하지 않습니다."}


# ------------------------------------------------------------------ validate
def _params(c: Condition, raw: dict | None) -> dict:
    raw = raw or {}
    out = {}
    for d in c.params:
        v = raw.get(d["key"], d["default"])
        if d["kind"] == "select":
            v = str(v)
            if v not in {o["value"] for o in d["options"]}:
                raise ValueError(f"{c.name}: '{d['label']}' 값이 올바르지 않습니다")
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{c.name}: '{d['label']}'은(는) 숫자여야 합니다") from None
            if not np.isfinite(v):
                raise ValueError(f"{c.name}: '{d['label']}'은(는) 숫자여야 합니다")
            v = min(d["max"], max(d["min"], v))
            if d["kind"] == "int":
                v = int(round(v))
        out[d["key"]] = v
    return out


def _group(raw: dict | None, side: str) -> dict:
    raw = raw or {}
    mode = raw.get("mode", "any")
    if mode not in ("any", "all"):
        raise ValueError("조건 결합 방식은 any(하나라도) 또는 all(모두)이어야 합니다")
    conds = raw.get("conditions") or []
    if len(conds) > MAX_CONDITIONS:
        raise ValueError(f"조건은 한쪽에 최대 {MAX_CONDITIONS}개까지 넣을 수 있습니다")
    out = []
    for c in conds:
        cd = CONDITIONS.get(str((c or {}).get("type")))
        if cd is None:
            raise ValueError(f"알 수 없는 조건입니다: {(c or {}).get('type')}")
        out.append({"type": cd.type, "params": _params(cd, c.get("params"))})
    return {"mode": mode, "conditions": out}


def normalize(spec: dict | None) -> dict:
    """Validated copy of a spec with every parameter filled in and clamped; raises ValueError (Korean message)."""
    spec = spec or {}
    entry, exit_ = _group(spec.get("entry"), "entry"), _group(spec.get("exit"), "exit")
    risk = {}
    for f in RISK_FIELDS:
        v = (spec.get("risk") or {}).get(f["key"])
        if v in (None, "", 0):
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{f['label']} 값은 숫자여야 합니다") from None
        if not np.isfinite(v) or v < 0:
            raise ValueError(f"{f['label']} 값이 올바르지 않습니다")
        v = min(f["max"], max(f["min"], v))
        risk[f["key"]] = int(round(v)) if f["key"] == "max_hold_days" else v
    if not entry["conditions"]:
        raise ValueError("매수 조건을 하나 이상 넣어주세요")
    if not exit_["conditions"] and not risk:
        raise ValueError("매도 조건이나 손절·익절 규칙이 하나는 있어야 합니다")
    return {"entry": entry, "exit": exit_, "risk": risk}


# ------------------------------------------------------------------- signals
def _combine(ind: pd.DataFrame, group: dict, regime: pd.Series | None) -> pd.Series:
    if not group["conditions"]:
        return pd.Series(False, index=ind.index)
    parts = [CONDITIONS[c["type"]].fn(ind, c["params"], regime).fillna(False).astype(bool) for c in group["conditions"]]
    out = parts[0]
    for p in parts[1:]:
        out = (out | p) if group["mode"] == "any" else (out & p)
    return out


def signals(ind: pd.DataFrame, spec: dict, regime: pd.Series | None = None) -> tuple[pd.Series, pd.Series]:
    """(entry, exit) boolean series aligned to `ind`. `spec` must be normalized."""
    exit_ = _combine(ind, spec["exit"], regime)
    entry = _combine(ind, spec["entry"], regime) & ~exit_
    return entry, exit_


def risk_kwargs(spec: dict) -> dict:
    r = spec.get("risk") or {}
    return {"stop_loss": (r["stop_loss_pct"] / 100) if r.get("stop_loss_pct") else None,
            "take_profit": (r["take_profit_pct"] / 100) if r.get("take_profit_pct") else None,
            "trailing_stop": (r["trailing_stop_pct"] / 100) if r.get("trailing_stop_pct") else None,
            "max_bars": int(r["max_hold_days"]) if r.get("max_hold_days") else None}


def needs_regime(spec: dict) -> bool:
    return any(CONDITIONS[c["type"]].needs_regime for g in ("entry", "exit") for c in spec[g]["conditions"])


# ------------------------------------------------------------------ describe
def _labels(group: dict) -> list[str]:
    return [CONDITIONS[c["type"]].label(c["params"]) for c in group["conditions"]]


def risk_labels(spec: dict) -> list[str]:
    r = spec.get("risk") or {}
    out = []
    if r.get("stop_loss_pct"):
        out.append(f"손절 −{r['stop_loss_pct']:g}%")
    if r.get("take_profit_pct"):
        out.append(f"익절 +{r['take_profit_pct']:g}%")
    if r.get("trailing_stop_pct"):
        out.append(f"최고가 대비 −{r['trailing_stop_pct']:g}% 추적 손절")
    if r.get("max_hold_days"):
        out.append(f"최대 {r['max_hold_days']}거래일 보유")
    return out


def describe(spec: dict) -> str:
    spec = normalize(spec)
    join = {"any": " 또는 ", "all": " 그리고 "}
    buy = join[spec["entry"]["mode"]].join(_labels(spec["entry"]))
    sell = join[spec["exit"]["mode"]].join(_labels(spec["exit"]))
    parts = [f"매수: {buy}"]
    if sell:
        parts.append(f"매도: {sell}")
    rl = risk_labels(spec)
    if rl:
        parts.append(" · ".join(rl))
    return " / ".join(parts)


def current_state(ind: pd.DataFrame, spec: dict, regime: pd.Series | None = None) -> dict:
    """Which conditions hold on the latest bar - what the rule says today."""
    row = ind.iloc[-1]
    out = {"as_of": str(ind.index[-1].date())}
    for side in ("entry", "exit"):
        items = []
        for c in spec[side]["conditions"]:
            cd = CONDITIONS[c["type"]]
            met = bool(cd.fn(ind, c["params"], regime).fillna(False).iloc[-1])
            items.append({"label": cd.label(c["params"]), "met": met,
                          "value": cd.value(row, c["params"]) if cd.value else None})
        hit = [i["met"] for i in items]
        out[side] = {"mode": spec[side]["mode"], "items": items,
                     "met": bool(hit) and (any(hit) if spec[side]["mode"] == "any" else all(hit))}
    out["action"] = "sell" if out["exit"]["met"] else "buy" if out["entry"]["met"] else "wait"
    return out


REASON_KO = {"signal": "매도 조건", "stop_loss": "손절", "take_profit": "익절", "trailing_stop": "추적 손절",
             "max_hold": "보유 기간 만료", "open": "보유 중"}
