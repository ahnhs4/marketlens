"""Per-user settings with documented defaults."""
from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from ..analysis.scoring import DEFAULT_WEIGHTS, SCORE_BANDS, SIGNAL_THRESHOLDS
from ..models import User, UserSettings
from .store import utcnow

DEFAULT_PROFILE = {"risk_tolerance": "Moderate", "holding_period": "Medium", "preferred_market": "Global",
                   "preferred_sectors": [], "max_drawdown": 0.25, "base_currency": "USD"}
DEFAULT_WEIGHTS_ALL = {"technical_components": dict(DEFAULT_WEIGHTS),
                       "overall_blend": {"technical": 0.5, "macro": 0.3, "fundamental": 0.2},
                       "combined_ranking": {"technical": 0.6, "macro": 0.4}}
DEFAULT_PREFS = {"color_convention": "global", "rsi_period": 14, "rsi_short_period": 7, "rsi_long_period": 28,
                 "lower_osc": "stoch", "stoch_sets": {"short": [5, 3, 3], "medium": [10, 6, 6], "long": [20, 12, 12]},
                 "default_range": "1Y",
                 "indicators": {"sma5": False, "sma20": True, "sma60": True, "sma120": False, "sma200": True,
                                "bb": False, "vwap": False, "ema20": False, "volume": True, "volume_profile": True,
                                "osc3": True, "rsi": False, "macd": False, "stoch": False, "atr": False, "adx": False, "obv": False,
                                "levels": True},
                 "volume_profile_period": "6M",
                 # 채점 방식: 'user' = 내 규칙 (과매수 · 고점 감점, 과매도 · 골든크로스 가산, 크게) / 'evidence' = 데이터 검증
                 "timing_style": "user",
                 # portfolio plan: dividend goal (won/yr), tax inputs, target weights by group (%), group overrides
                 "plan": {"dividend_target_krw": None, "dividend_basis": "net", "realized_overseas_krw": 0,
                          "contribution_krw": 0, "targets": None, "bucket_overrides": {}}}


def _merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get(db: Session, user: User) -> dict:
    s = db.get(UserSettings, user.id)
    if s is None:
        s = UserSettings(user_id=user.id)
        db.add(s)
        db.commit()
    return {"profile": _merge(DEFAULT_PROFILE, s.profile), "weights": _merge(DEFAULT_WEIGHTS_ALL, s.weights),
            "preferences": _merge(DEFAULT_PREFS, s.preferences),
            "last_visit_at": s.last_visit_at.isoformat() if s.last_visit_at else None,
            "previous_visit_at": s.previous_visit_at.isoformat() if s.previous_visit_at else None,
            "reference": {"signal_thresholds": SIGNAL_THRESHOLDS, "score_bands": SCORE_BANDS,
                          "default_weights": DEFAULT_WEIGHTS_ALL}}


def update(db: Session, user: User, patch: dict) -> dict:
    s = db.get(UserSettings, user.id) or UserSettings(user_id=user.id)
    db.add(s)
    if "profile" in patch:
        s.profile = _merge(s.profile or {}, patch["profile"])
    if "weights" in patch:
        w = _merge(s.weights or {}, patch["weights"])
        tc = w.get("technical_components")
        if tc:  # normalise to sum 1 and keep non-negative
            tc = {k: max(0.0, float(v)) for k, v in tc.items() if k in DEFAULT_WEIGHTS}
            tot = sum(tc.values()) or 1
            w["technical_components"] = {k: v / tot for k, v in tc.items()}
        s.weights = w
    if "preferences" in patch:
        s.preferences = _merge(s.preferences or {}, patch["preferences"])
    s.updated_at = utcnow()
    db.commit()
    return get(db, user)


def touch_visit(db: Session, user: User) -> None:
    """Record a visit; keep the previous one for 'since your last visit' (only if >30 min apart)."""
    s = db.get(UserSettings, user.id)
    if s is None:
        return
    now = utcnow()
    if s.last_visit_at is None or (now - s.last_visit_at.replace(tzinfo=now.tzinfo)).total_seconds() > 1800:
        s.previous_visit_at = s.last_visit_at
    s.last_visit_at = now
    db.commit()
