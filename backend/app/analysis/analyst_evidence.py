"""Analyst (sell-side) signals scored from measured forward returns, per market - the "micro" component.

Measured by backend/tools/analyst_event_study.py (run 2026-10-11):
  * US: 499 S&P 500 members, monthly 2022-07 ~ 2026-06 (22,418 stock-months), Yahoo rating / target actions.
  * KR: 131 KOSPI 100 / KOSDAQ 50 members, monthly 2024-07 ~ 2026-06 (2,762 stock-months), Naver broker reports.
  * At each month-end: consensus upside (mean of each firm's latest target within a year, >= 3 firms),
    90-day target-revision breadth, 30-day net rating changes. Buy next open; 20 / 60-day return minus the
    market's median stock. Points = 4 × 60-day median excess % × share of years with the same sign, ±20 cap;
    kept only when 20 / 60 days agree, the bucket is large enough (US 1,000 / KR 300) and |points| >= 3.

Did NOT hold up (shown as information, never scored): target-revision direction, upgrades / downgrades in the
last 30 days, and every other upside bucket. EPS estimate revisions have no history to test against, so they
are shown but not scored either.
"""
from __future__ import annotations

from .timing_evidence import Rule

RULES: dict[str, dict[str, Rule]] = {
    "US": {
        "upside_35_plus": Rule("upside_35_plus", 9, "평균 목표가 대비 상승여력 35% 이상", "20일 +0.7% · 60일 +3.0% · 2,792건 · 4/5년"),
    },
    "KR": {
        "upside_neg": Rule("upside_neg", -16, "주가가 평균 목표가보다 높음 (목표가 도달)", "20일 −2.5% · 60일 −3.9% · 336건 · 3/3년"),
        "upside_10_20": Rule("upside_10_20", -3, "평균 목표가 대비 상승여력 10~20%", "20일 −0.1% · 60일 −1.2% · 345건 · 2/3년"),
    },
}
STUDY = {"US": "S&P 500 499종목 · 월말 기준 2022-07 ~ 2026-06", "KR": "코스피100 · 코스닥50 중 131종목 · 2024-07 ~ 2026-06"}
WEIGHT = 0.15   # share of the 판단 점수 given to this component when analyst data exists


def bucket(upside: float | None) -> str | None:
    if upside is None:
        return None
    if upside < 0:
        return "upside_neg"
    if upside < 0.10:
        return "upside_0_10"
    if upside < 0.20:
        return "upside_10_20"
    if upside < 0.35:
        return "upside_20_35"
    return "upside_35_plus"


def micro_score(signals: dict, market: str) -> dict | None:
    """0-100 (50 = no measured edge) plus the reasons. None without a consensus of >= 3 firms."""
    up = signals.get("upside")
    if up is None:
        return None
    b = bucket(up)
    rule = RULES.get(market, {}).get(b)
    pts = rule.points if rule else 0
    details = [f"{rule.label} ({pts:+.0f}) - {rule.evidence}"] if rule else \
        [f"목표가 상승여력 {up * 100:+.0f}% - {'이 구간은' if b else ''} 과거 데이터상 성과 차이가 뚜렷하지 않아 0점"]
    return {"score": max(0.0, min(100.0, 50.0 + pts)), "points": pts, "bucket": b, "upside": up, "details": details,
            "study": STUDY.get(market), "weight": WEIGHT}
