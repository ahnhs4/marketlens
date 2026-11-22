import pytest

from app.services import recap_causes as rc

SUBJ = {g[0]: g[4] for g in rc.GROUPS}


@pytest.mark.parametrize("group,title,cause,marker,direction", [
    ("oil", "국제유가, 미러 경유 거래·중동 긴장 주시하며 혼조 마감", "미러 경유 거래·중동 긴장", "주시하며", "flat"),
    ("oil", "후티 반군 사우디 공습에 공급 우려…국제유가 2% 급등", "후티 반군 사우디 공습에 공급 우려", "속에", "up"),
    ("us", "뉴욕증시, 기대 못채운 美은행 실적·반도체 약세에 일제히 하락(종합)", "기대 못채운 美 은행 실적·반도체 약세", "에", "down"),
    ("us", "美은행 실적 혼조·반도체 약세…뉴욕증시 하락", "美 은행 실적 혼조·반도체 약세", "속에", "down"),
    ("rates", "NYT \"성장 때문 아니다\"…美국채금리 급등 이끈 '재정적자 경고'", "재정적자 경고", "로", "up"),
    ("kr", "코스피 6000선 턱걸이... 외국인 매도에 0.44%↓ [마감시황]", "외국인 매도", "에", "down"),
])
def test_cause_is_cut_out_of_the_headline(group, title, cause, marker, direction):
    ex = rc.extract(title, SUBJ[group])
    assert ex == {"cause": cause, "marker": marker, "direction": direction}


@pytest.mark.parametrize("group,title", [
    ("oil", "美 연준 추가 긴축 우려 완화에 아시아 증시 반등…국제유가는 상승세"),   # the cause belongs to another market
    ("kr", "코스피, 0.44% 하락 마감…코스닥도 1.57%↓"),                             # no cause at all
    ("kr", "[증시 레이더] 코스피 6천선 겨우 방어⋯6거래일째 하락"),                    # a streak is not a cause ('연속' ≠ '속')
    ("kr", "[마켓뷰] 6천피 깨지나 반등하나…'급락' 코스피 향배는"),                   # a question is not a cause
    ("krw", "[속보] 원·달러 환율, 6.8원 내린 1338.3원 - 조선비즈"),
])
def test_no_cause_is_invented(group, title):
    assert rc.extract(title, SUBJ[group]) is None


def test_direction_must_match_the_move():
    assert rc._agrees("up", 0.8, "pct") and not rc._agrees("up", -0.8, "pct")
    assert rc._agrees("flat", 0.3, "pct") and not rc._agrees("flat", 1.2, "pct")


def test_korean_particles():
    assert rc.josa("국제유가", "이", "가") == "국제유가가" and rc.josa("원/달러 환율", "이", "가") == "원/달러 환율이"
    assert rc._lead({"cause": "미러 경유 거래·중동 긴장", "marker": "주시하며"}) == "미러 경유 거래·중동 긴장을 주시하며"
    assert rc._lead({"cause": "재정적자 경고", "marker": "로"}) == "재정적자 경고로"
    assert rc._lead({"cause": "공급 우려", "marker": "속에"}) == "공급 우려 속에"


def test_krw_wording_and_forecasts():
    ex = rc.extract("원화, 외국인 순매수에 강세", SUBJ["krw"])         # KRW stronger = USD/KRW rate down
    assert ex["direction"] == "down"
    assert rc.extract("[환율 전망] 불안한 반도체 투심에 원화도 휘청", SUBJ["krw"]) is None
    ex = rc.extract("원·달러 환율, 1341.5원…달러, 연준 위원 발언에 하락", SUBJ["krw"])
    assert ex["direction"] == "down" and "연준 위원 발언" in ex["cause"]


def test_narrowed_losses_are_not_a_cause_of_the_fall():
    ex = rc.extract("[뉴스프레소] 뉴욕증시 동반 하락…윌리엄스 총재 발언에 낙폭 축소", SUBJ["us"])
    assert ex is None or ex["direction"] == "up"          # the remark limited the fall: never attributed to a decline
    assert not (ex and rc._agrees(ex["direction"], -0.3, "pct"))
