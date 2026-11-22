import pandas as pd
import pytest

from app.analysis import analyst_evidence as ev
from app.analysis import position_advice
from app.services import analyst


def test_micro_score_uses_only_measured_rules():
    us = ev.micro_score({"upside": 0.40}, "US")
    assert us["points"] == 9 and us["score"] == 59
    assert ev.micro_score({"upside": 0.15}, "US")["points"] == 0          # no measured edge -> neutral
    kr = ev.micro_score({"upside": -0.05}, "KR")
    assert kr["points"] == -16 and kr["score"] == 34 and "목표가" in kr["details"][0]
    assert ev.micro_score({"upside": None}, "KR") is None                 # fewer than 3 firms -> not scored


def test_consensus_takes_each_firms_latest_target_within_a_year():
    now = pd.Timestamp("2026-10-10")
    acts = [{"date": pd.Timestamp("2026-09-01"), "firm": "A", "pt": 120.0}, {"date": pd.Timestamp("2026-06-01"), "firm": "A", "pt": 90.0},
            {"date": pd.Timestamp("2026-08-01"), "firm": "B", "pt": 100.0}, {"date": pd.Timestamp("2025-06-01"), "firm": "C", "pt": 300.0}]
    c = analyst._consensus(acts, now)
    assert c["firms"] == 2 and c["mean"] == pytest.approx(110.0)        # A's latest only, C too old


def test_firm_aliases():
    assert "jpmorgan" in analyst._firm_aliases("JP Morgan")
    assert "citi" in analyst._firm_aliases("Citigroup")
    assert "cantor" in analyst._firm_aliases("Cantor Fitzgerald")
    assert analyst._short_name("NVIDIA Corporation", "NVDA") == "NVIDIA"


def test_flow_paragraph_is_built_from_the_records():
    recent = [{"date": "2026-09-29", "firm": "미래에셋증권", "opinion_ko": "매수", "title": "MASGA 프리미엄은 회복이 마땅"},
              {"date": "2026-09-15", "firm": "IBK투자증권", "opinion_ko": "매수", "title": "단골들이 돈을 쓰고 싶어 한다"}]
    cons = {"mean": 145000.0, "n_label": "최근 1년 증권사 12곳의 최신 목표가"}
    counts = {"pt_raises_90": 1, "pt_cuts_90": 4}
    txt = analyst._flow("KR", recent, cons, counts, 72000.0, "KRW", None, None)
    assert "매수 2건" in txt and "MASGA" in txt and "145,000원으로" in txt and "+101%" in txt and "내려가는 중" in txt


def test_reasons_mark_unscored_items_as_reference():
    a = {"available": True, "currency": "USD", "consensus": {"mean": 330.0, "n_label": "애널리스트 59명"}, "upside_pct": 43.0,
         "micro": ev.micro_score({"upside": 0.44}, "US"), "counts": {"pt_raises_90": 16, "pt_cuts_90": 0, "upgrades_30": 0, "downgrades_30": 1},
         "eps": {"+1y": {"chg_pct": 25.2}}}
    pos, neg = position_advice.analyst_reasons(a)
    assert pos[0].startswith("애널리스트: 평균 목표가 대비 상승여력 35% 이상 (+9)")
    assert any("참고" in p and "상향 16" in p for p in pos)
    assert any("하향 1건" in n for n in neg)
