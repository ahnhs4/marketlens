"""Entry-timing rules, with points set from measured forward returns, per market.

How the numbers were measured (backend/tools/timing_event_study.py, run 2026-10-10):
  * US: 499 current S&P 500 members, 2022-06-10 ~ 2026-10-09 (~379k stock-days).
  * KR: 2,483 KOSPI/KOSDAQ companies, 2024-06-25 ~ 2026-10-08 (~560k stock-days).
  * For every stock-day a rule fires, buy at the next day's open and measure the return
    20 and 60 trading days later, minus the median stock over the same window (excess).
  * Points = 4 × (60-day median excess %) × (share of calendar years whose 20-day median
    had the same sign), capped at ±20. A rule is kept only when the 20- and 60-day medians
    agree in sign, it fired at least 2,000 times, and the result is at least 3 points.

Rules that did NOT pass (left at 0, i.e. ignored): stochastic turns from oversold, MACD
signal crosses, reclaiming the 20-day line, golden/death crosses (5/20, 20/60, 50/200), RSI(2)
pullbacks; in the US also RSI ≥ 70, stochastics ≥ 80, Bollinger breaks and the distance to
the 52-week high; in Korea 12-1 momentum ≥ 40%. Caveats: short samples, survivor bias (today's members only), no costs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    key: str
    points: float
    label: str          # shown in the "Why?" list
    evidence: str       # measured basis: 20d / 60d median excess, cases, consistent years


STUDY = {
    "US": "S&P 500 499종목 · 2022-06 ~ 2026-10",
    "KR": "코스피·코스닥 2,483종목 · 2024-06 ~ 2026-10",
}
METHOD = ("다음날 시가에 사서 20 · 60거래일 뒤 수익률을 같은 기간 중앙값 종목과 비교 (초과수익 중앙값). "
          "점수 = 4 × 60일 초과수익 × 같은 방향이었던 연도 비율, ±20 상한. 20일·60일 방향 일치, 2,000건 이상, 3점 이상인 규칙만 반영.")

RULES: dict[str, dict[str, Rule]] = {
    "KR": {
        "ext20_8": Rule("ext20_8", -19, "20일선보다 8% 넘게 위 (급등 후 이격)", "20일 −2.0% · 60일 −4.6% · 70,156건 · 2/2년"),
        "rsi_70": Rule("rsi_70", -5, "RSI14 70 이상 (과매수)", "20일 −0.7% · 60일 −1.3% · 25,376건 · 2/2년"),
        "bb_above": Rule("bb_above", -5, "볼린저 상단 밴드 위", "20일 −1.0% · 60일 −1.4% · 33,642건 · 2/2년"),
        "rsi_30_below200": Rule("rsi_30_below200", 4, "RSI14 30 이하 과매도 (200일선 아래)", "20일 +1.0% · 60일 +1.0% · 34,245건 · 2/2년"),
        "bb_below": Rule("bb_below", 5, "볼린저 하단 밴드 아래 (단기 과매도)", "20일 +0.9% · 60일 +1.1% · 30,329건 · 2/2년"),
        "mom12_30": Rule("mom12_30", 7, "12개월(최근 1개월 제외) 수익률 30~40%", "20일 +0.5% · 60일 +1.8% · 24,830건 · 2/2년"),
        "hi52_0_2": Rule("hi52_0_2", 10, "52주 고점 2% 이내", "20일 +1.0% · 60일 +5.3% · 12,263건 · 1/2년"),
        "hi52_2_5": Rule("hi52_2_5", 7, "52주 고점 2~5% 아래", "20일 +1.3% · 60일 +3.4% · 11,721건 · 1/2년"),
        "hi52_5_8": Rule("hi52_5_8", 8, "52주 고점 5~8% 아래", "20일 +1.1% · 60일 +3.8% · 15,649건 · 1/2년"),
        "hi52_8_15": Rule("hi52_8_15", 14, "52주 고점 8~15% 아래 (강한 종목의 조정)", "20일 +0.9% · 60일 +3.5% · 50,052건 · 2/2년"),
        "hi52_15_30": Rule("hi52_15_30", 8, "52주 고점 15~30% 아래", "20일 +0.5% · 60일 +2.0% · 145,605건 · 3/3년"),
        "hi52_30_plus": Rule("hi52_30_plus", -7, "52주 고점보다 30% 넘게 아래 (약한 종목)", "20일 −0.6% · 60일 −2.6% · 325,282건 · 2/3년"),
    },
    "US": {
        "mom12_40": Rule("mom12_40", 10, "12개월(최근 1개월 제외) 수익률 40% 이상 - 모멘텀 상위", "20일 +0.9% · 60일 +2.4% · 66,745건 · 5/5년"),
        "ext20_8": Rule("ext20_8", 6, "20일선보다 8% 넘게 위 (미국은 강한 종목이 계속 강했음)", "20일 +0.7% · 60일 +2.0% · 22,621건 · 4/5년"),
        "rsi_30_below200": Rule("rsi_30_below200", 4, "RSI14 30 이하 과매도 (200일선 아래)", "20일 +0.3% · 60일 +1.2% · 12,002건 · 3/4년"),
        "hi52_30_plus": Rule("hi52_30_plus", 4, "52주 고점보다 30% 넘게 아래 (낙폭 과대)", "20일 +0.7% · 60일 +1.8% · 47,013건 · 3/5년"),
    },
}


def market_of(symbol: str | None) -> str:
    return "KR" if symbol and symbol.upper().endswith((".KS", ".KQ")) else "US"
