"""Written short / medium / long outlook for one stock (기간별 종합 분석).

Rule-based prose assembled only from numbers already computed elsewhere (technicals, macro
view, fundamentals, regime, Fear & Greed). Every sentence states its inputs; nothing here
forecasts. Horizons: 단기 1~20 거래일, 중기 20~120 거래일, 장기 120 거래일 이상.
"""
from __future__ import annotations

import datetime as dt
import logging

from ..analysis.technical import describe_osc
from ..analysis.timing_evidence import market_of
from ..i18n_ko import GROUP_KO, PATTERN_KO, STATUS_KO
from .store import clean, utcnow

log = logging.getLogger(__name__)

VIEW_KO = {"Bullish": "강세", "Neutral": "중립", "Bearish": "약세"}
REGIME_KO = {"Risk-On": "위험선호", "Neutral": "중립", "Risk-Off": "위험회피"}
QUAD_KO = {"Inflationary Growth": "인플레이션 동반 성장", "Disinflationary Growth": "디스인플레이션 성장(골디락스)",
           "Stagflation": "스태그플레이션", "Recessionary": "경기 침체형"}
OVERLAY_KO = {"Rate-Cut Expectation": "금리인하 기대", "Rate-Hike Expectation": "금리인상 기대",
              "Liquidity-Driven": "유동성 장세", "Recession Warning": "경기침체 경고"}
FG_KO = {"Extreme Fear": "극단적 공포", "Fear": "공포", "Neutral": "중립", "Greed": "탐욕", "Extreme Greed": "극단적 탐욕"}
PILLAR_KO = {"valuation": "밸류에이션", "growth": "성장성", "quality": "수익성", "balance_sheet": "재무 건전성",
             "profitability": "수익성", "stability": "재무 안정성", "cash_return": "현금흐름·주주환원"}
HZ = {"short": ("단기", "1~20 거래일"), "medium": ("중기", "20~120 거래일"), "long": ("장기", "120 거래일 이상")}


def _n(x, d: int = 1, signed: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x:+,.{d}f}" if signed else f"{x:,.{d}f}"


def _pct(a, b) -> float | None:
    return None if a is None or not b else (a / b - 1) * 100


def _px(x) -> str:
    if x is None:
        return "—"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def _stance(view: str, ctx: float | None, label: str) -> tuple[str, str]:
    """Combine the horizon's technical view with that horizon's context score (`label` names it)."""
    c = None if ctx is None else round(ctx)  # compare what the user sees
    m = "우호적" if c is not None and c >= 55 else "부담" if c is not None and c < 45 else "중립"
    if view == "Bullish":
        if m == "우호적":
            return "positive", f"기술적 흐름과 {label}이 모두 우호적입니다. 추세가 이어질 근거가 더 많습니다."
        if m == "부담":
            return "mixed", f"차트는 강하지만 {label}이 부담입니다. 추세는 살아 있으나 흔들림이 커질 수 있어 비중 관리가 중요합니다."
        return "positive", f"기술적 흐름이 강하고 {label}은 중립입니다. 차트의 추세가 판단의 중심입니다."
    if view == "Bearish":
        if m == "우호적":
            return "mixed", f"{label}은 우호적이지만 차트가 약합니다. 지지 확인이나 추세 회복이 나오기 전까지는 확인이 먼저입니다."
        if m == "부담":
            return "negative", f"기술적 흐름과 {label} 모두 약합니다. 보수적으로 볼 구간입니다."
        return "negative", f"차트가 약하고 {label}은 중립입니다. 추세 회복 신호가 나오는지가 관건입니다."
    if m == "우호적":
        return "neutral", f"차트는 방향을 정하지 못했지만 {label}은 우호적입니다. 위쪽 돌파 여부가 관건입니다."
    if m == "부담":
        return "neutral", f"차트는 방향이 없고 {label}은 부담입니다. 아래쪽 지지가 지켜지는지 먼저 봐야 합니다."
    return "neutral", f"차트와 {label} 모두 뚜렷한 방향이 없습니다. 박스권 상단 · 하단 중 어느 쪽이 먼저 깨지는지가 관건입니다."


def _mean(xs: list) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def _osc_note(state: dict | None, horizon: str, market: str, style: str = "evidence") -> str | None:
    """Extra sentence when an oscillator is at an extreme or just turned - worded by the active scoring style."""
    if not state:
        return None
    c = state.get("cross")
    hz = HZ[horizon][0]
    if style == "user":
        if c and c["bars_ago"] <= 5 and c["in_extreme"]:
            return (f"{hz} 스토캐스틱이 과매도권에서 상승 전환했습니다 - 턴어라운드 신호로 봅니다." if c["type"] == "up" else
                    f"{hz} 스토캐스틱이 과매수권에서 하락 전환했습니다 - 힘이 빠지는 신호로 봅니다.")
        if state["zone"] == "overbought":
            return f"{hz} 스토캐스틱이 과매수권({state['k']:.0f})입니다 - 이미 늦은 자리일 수 있어 신규 진입은 보류합니다."
        if state["zone"] == "oversold":
            return f"{hz} 스토캐스틱이 과매도권({state['k']:.0f})입니다 - 반등 후보로 봅니다."
        return None
    if c and c["bars_ago"] <= 5 and c["in_extreme"]:
        turn = "과매도권에서 상승 전환" if c["type"] == "up" else "과매수권에서 하락 전환"
        return f"{hz} 스토캐스틱이 {turn}했습니다 - 다만 과거 데이터상 이 전환 신호만으로는 이후 초과수익이 없었습니다."
    if state["zone"] == "overbought":
        if market == "KR":
            return f"{hz} 스토캐스틱이 과매수권({state['k']:.0f})입니다 - 한국 데이터상 과열 직후 성과가 약했던 편이라 신규 진입은 이격 해소를 기다립니다."
        return f"{hz} 스토캐스틱이 과매수권({state['k']:.0f})입니다 - 미국 데이터상 과매수만으로 이후 성과가 나빠지지는 않았습니다 (강한 종목이 계속 강한 경향)."
    if state["zone"] == "oversold":
        return f"{hz} 스토캐스틱이 과매도권({state['k']:.0f})입니다 - 스토캐스틱 과매도 자체는 과거 데이터상 뚜렷한 우위가 없었습니다."
    return None


def build(symbol: str, tech: dict, scores: dict, regime: dict | None, fear_greed: dict | None,
          earnings_ts: int | None) -> dict:
    ind = tech.get("indicators") or {}
    market = market_of(symbol)
    style = (tech.get("signal") or {}).get("style") or "evidence"
    hz = tech["signal"]["horizons"]
    osc = (tech.get("oscillators") or {}).get("stoch") or {}
    sets = tech.get("stoch_sets") or {}
    ma = tech.get("ma") or {}
    sr = tech.get("support_resistance") or {}
    md = scores.get("macro_detail") or {}
    macro_score = scores.get("macro")
    fd = scores.get("fundamental_detail") or {}
    c = ind.get("close")
    s1 = (sr.get("supports") or [None])[0]
    r1 = (sr.get("resistances") or [None])[0]
    events = ma.get("events") or []
    grp = (md.get("group") or {}).get("group")
    grp_ko = GROUP_KO.get(grp, grp) if grp else None
    days_to_earn = None
    if earnings_ts:
        days_to_earn = (dt.datetime.fromtimestamp(earnings_ts, dt.timezone.utc).date() - dt.date.today()).days

    def osc_line(k: str) -> str:
        n = sets.get(k)
        name = f"{HZ[k][0]} 스토캐스틱({','.join(str(x) for x in n)})" if n else f"{HZ[k][0]} 스토캐스틱"
        return describe_osc(osc.get(k), name)

    out = {}
    # ------------------------------------------------------------------ short
    t, m, w = [], [], []
    t.append(f"현재가 {_px(c)} - 20일선 대비 {_n(_pct(c, ind.get('sma20')), 1, True)}%이고, "
             f"5일선이 20일선 {'위(단기 정배열)' if (ind.get('sma5') or 0) > (ind.get('sma20') or 0) else '아래(단기 역배열)'}입니다. "
             f"5일 수익률 {_n((ind.get('mom5') or 0) * 100, 1, True)}%, 20일 {_n((ind.get('mom20') or 0) * 100, 1, True)}%.")
    t.append(osc_line("short") + f" · RSI({(tech.get('rsi_periods') or {}).get('short', 7)}) {_n(ind.get('rsi_short'), 0)}.")
    if ind.get("vol_ratio") is not None:
        t.append(f"오늘 거래량은 20일 평균의 {ind['vol_ratio']:.1f}배입니다.")
    ev = next((e for e in events if e.get("pair") == [5, 20] and e["bars_ago"] <= 10), None)
    if ev:
        t.append(f"{ev['bars_ago']}일 전 {ev['event']}가 나왔습니다.")
    if s1 and r1:
        rr = sr.get("reward_risk")
        t.append(f"가까운 지지 {_px(s1['price'])}({s1['distance_pct']:+.1f}%) · 저항 {_px(r1['price'])}({r1['distance_pct']:+.1f}%)"
                 + (f", 손익비 {rr:.2f}" + (" - 위쪽 여유가 작습니다" if rr < 1 else " - 위쪽 여유가 더 큽니다") if rr else "") + ".")
        w += [f"저항 {_px(r1['price'])} 종가 돌파 여부", f"지지 {_px(s1['price'])} 종가 이탈 여부"]
    near = _pct(c, ind.get("hi52"))
    if near is not None and near >= -5:
        t.append(f"52주 고점({_px(ind['hi52'])})까지 {abs(near):.1f}% - "
                 + ("고점권이라 저항 · 되돌림 위험이 커서 감점합니다." if style == "user" else
                    "한국 데이터상 고점 근처 종목은 이후에도 강했던 편입니다 (신고가 효과)." if market == "KR" else
                    "미국 데이터상 고점과의 거리는 이후 성과에 큰 차이를 만들지 않았습니다."))
        w.append(f"52주 고점 {_px(ind['hi52'])} 종가 돌파 여부")
    if fear_greed:
        wk = (fear_greed.get("compare") or {}).get("week")
        m.append(f"시장 심리(공포·탐욕 지수)는 {fear_greed['value']:.0f}({FG_KO.get(fear_greed['label'], '')})"
                 + (f", 1주 전 {wk['value']:.0f}" if wk else "") + "입니다. "
                 + ("공포 구간에서는 지수 전체가 흔들리기 쉬워 개별 종목도 변동성이 커집니다." if fear_greed["value"] < 45 else
                    "탐욕 구간에서는 단기 과열 뒤 되돌림이 잦습니다." if fear_greed["value"] > 55 else "심리는 중립입니다."))
    emp = (md.get("empirical") or {}).get("details") or []
    big = [d for d in emp if abs(d.get("effect_pct") or 0) >= 0.5]
    if big:
        m.append("최근 60일 매크로 변화가 이 종목에 준 영향(실측 민감도 기준): "
                 + ", ".join(f"{d['factor']} {d['move']} → 추정 {d['effect_pct']:+.1f}%" for d in big) + ".")
    if days_to_earn is not None and 0 <= days_to_earn <= 30:
        m.append(f"실적 발표가 {days_to_earn}일 뒤입니다 - 발표 전후로 변동성이 커지는 구간입니다.")
        w.append(f"실적 발표 D-{days_to_earn}")
    shock = sum(d.get("effect_pct") or 0 for d in emp)
    ctx_s = _mean([fear_greed["value"] if fear_greed else None, max(0.0, min(100.0, 50 + 5 * shock)) if emp else None])
    note = _osc_note(osc.get("short"), "short", market, style)
    st, concl = _stance(hz["short"]["view"], ctx_s, "단기 환경")
    out["short"] = {"technical": t, "macro": m, "conclusion": concl + (f" {note}" if note else ""), "stance": st, "watch": w,
                    "context": ctx_s, "context_label": "단기 환경", "context_formula": "평균(공포·탐욕 지수, 50 + 5 × 최근 60일 매크로 충격 추정치 합)"}

    # ----------------------------------------------------------------- medium
    t, m, w = [], [], []
    s2060 = next((s for s in ma.get("states") or [] if s["state"].startswith("SMA20")), None)
    t.append(f"이동평균 배열은 {ma.get('structure_ko', '—')}이며"
             + (f", 20일선이 60일선 {'위' if s2060['bullish'] else '아래'}({s2060['detail']})" if s2060 else "")
             + f", 주가는 60일선 대비 {_n(_pct(c, ind.get('sma60')), 1, True)}%입니다.")
    if ind.get("macd_hist") is not None:
        t.append(f"MACD 히스토그램 {ind['macd_hist']:+.2f}로 {'상승' if ind['macd_hist'] > 0 else '하락'} 모멘텀, "
                 f"ADX {_n(ind.get('adx'), 0)}({'추세 뚜렷' if (ind.get('adx') or 0) >= 25 else '추세 약함'}), 60일 수익률 {_n((ind.get('mom60') or 0) * 100, 1, True)}%.")
    t.append(osc_line("medium") + ".")
    ev = next((e for e in events if e.get("pair") == [20, 60] and e["bars_ago"] <= 40), None)
    if ev:
        t.append(f"{ev['bars_ago']}일 전 {ev['event']}가 나왔습니다.")
    pts = [p for p in tech.get("patterns") or [] if p.get("direction") != "neutral"][:2]
    for p in pts:
        hist = p.get("historical") or {}
        sr_txt = f", 과거 성공률 {hist['success_rate'] * 100:.0f}% (n={hist['n']})" if hist.get("success_rate") is not None and hist.get("n") else ""
        t.append(f"차트 패턴: {PATTERN_KO.get(p['pattern'], p['pattern'])} {STATUS_KO.get(p['status'], p['status'])}"
                 f"(신뢰도 {p.get('confidence', 0) * 100:.0f}%{sr_txt}).")
    if macro_score is not None:
        m.append(f"매크로 정합도 {macro_score:.0f}/100" + (f" - {grp_ko} 업종 기준" if grp_ko else "") + ".")
    for r in (md.get("reasons") or [])[:2]:
        m.append(f"순풍: {r}")
    for r in (md.get("risks") or [])[:2]:
        m.append(f"역풍: {r}")
    if regime:
        ov = ", ".join(OVERLAY_KO.get(o["name"], o["name"]) for o in regime.get("overlays") or [])
        m.append(f"경기 사이클은 '{QUAD_KO.get(regime.get('quadrant'), regime.get('quadrant'))}' 국면" + (f", {ov}" if ov else "")
                 + " - 이 국면이 1~6개월 업종 흐름의 배경입니다.")
    if days_to_earn is not None and 30 < days_to_earn <= 120:
        m.append(f"다음 실적 발표는 약 {days_to_earn}일 뒤로, 중기 구간 안에 있습니다.")
    if ind.get("sma60"):
        w.append(f"60일선 {_px(ind['sma60'])} 지지 여부")
    if ind.get("sma20") and ind.get("sma60"):
        w.append("20일선과 60일선의 교차(골든/데드크로스)")
    ctx_m = macro_score
    note = _osc_note(osc.get("medium"), "medium", market, style)
    st, concl = _stance(hz["medium"]["view"], ctx_m, "업종 매크로 환경")
    out["medium"] = {"technical": t, "macro": m, "conclusion": concl + (f" {note}" if note else ""), "stance": st, "watch": w,
                     "context": ctx_m, "context_label": "업종 매크로 환경", "context_formula": "종목 매크로 정합도 (업종 노출도 × 팩터 추세 + 실측 민감도)"}

    # ------------------------------------------------------------------- long
    t, m, w = [], [], []
    slope200 = (ma.get("slopes_pct") or {}).get("sma200")
    t.append(f"주가는 200일선 대비 {_n(_pct(c, ind.get('sma200')), 1, True)}%"
             + (f", 200일선은 최근 20일 {slope200:+.2f}% {'상승' if slope200 > 0 else '하락'} 중" if slope200 is not None else "") + "입니다.")
    if ind.get("sma50") and ind.get("sma200"):
        g = ind["sma50"] > ind["sma200"]
        last = next((e for e in events if e.get("pair") == [50, 200]), None)
        t.append(f"50일선이 200일선 {'위(장기 골든크로스 구간)' if g else '아래(장기 데드크로스 구간)'}"
                 + (f" - {last['bars_ago']}일 전 {last['event']}" if last else "") + ".")
    hi_gap = _pct(c, ind.get("hi52"))
    t.append(f"6개월 수익률 {_n((ind.get('mom120') or 0) * 100, 1, True)}%, 12개월 {_n((ind.get('mom250') or 0) * 100, 1, True)}%, "
             f"52주 고점 대비 {_n(hi_gap, 1, True)}%"
             + ("." if hi_gap is None else
                (" - 고점권, 내 규칙상 감점." if hi_gap >= -5 else ".") if style == "user" else
                (" - 고점 대비 30% 넘게 하락한 약한 종목은 한국 데이터상 이후에도 약했습니다." if hi_gap < -30 else
                 " - 고점 30% 이내의 강한 종목은 한국 데이터상 이후 성과가 좋았습니다.") if market == "KR" else
                (" - 낙폭 과대 종목은 미국 데이터상 이후 소폭 반등했습니다." if hi_gap < -30 else ".")))
    t.append(osc_line("long") + ".")
    if ind.get("vol20") is not None:
        t.append(f"연환산 변동성 {ind['vol20'] * 100:.0f}%.")
    if regime:
        m.append(f"시장 국면 {REGIME_KO.get(regime.get('label'), regime.get('label'))} ({regime.get('score', 0):.0f}/100), "
                 f"경기 사이클 '{QUAD_KO.get(regime.get('quadrant'), regime.get('quadrant'))}'.")
    pillars = fd.get("pillars") or {}
    if pillars:
        good = [f"{PILLAR_KO.get(k, k)} {v['score']:.0f}" for k, v in pillars.items() if isinstance(v, dict) and v.get("score") is not None and v["score"] >= 70]
        weak = [f"{PILLAR_KO.get(k, k)} {v['score']:.0f}" for k, v in pillars.items() if isinstance(v, dict) and v.get("score") is not None and v["score"] <= 40]
        m.append(f"펀더멘털 {_n(scores.get('fundamental'), 0)}/100" + (f" - 강점: {', '.join(good)}" if good else "")
                 + (f" · 약점: {', '.join(weak)}" if weak else "") + ". 장기 구간에서는 이익 성장이 주가를 끌고 갑니다.")
    else:
        m.append("펀더멘털 데이터를 불러올 수 없습니다 (Data unavailable).")
    beta = (md.get("empirical") or {}).get("market_beta")
    if beta is not None:
        m.append(f"시장 베타 {beta:.2f} - 지수가 1% 움직일 때 평균 {beta:.1f}% 움직였습니다 (2년 일간 회귀).")
    if ind.get("sma200"):
        w.append(f"200일선 {_px(ind['sma200'])} 위 유지 여부")
    if ind.get("hi52"):
        w.append(f"52주 고점 {_px(ind['hi52'])} 돌파 여부")
    ctx_l = _mean([regime.get("score") if regime else None, scores.get("fundamental")])
    note = _osc_note(osc.get("long"), "long", market, style)
    st, concl = _stance(hz["long"]["view"], ctx_l, "장기 환경")
    out["long"] = {"technical": t, "macro": m, "conclusion": concl + (f" {note}" if note else ""), "stance": st, "watch": w,
                   "context": ctx_l, "context_label": "장기 환경", "context_formula": "평균(시장 국면 점수, 펀더멘털 점수)"}

    for k, v in out.items():
        h = hz[k]
        v.update({"key": k, "title": HZ[k][0], "range": HZ[k][1], "score": h["score"], "view": h["view"],
                  "headline": f"기술 {h['score']:.0f} ({VIEW_KO.get(h['view'], h['view'])}) · {v['context_label']} {_n(v['context'], 0)}"})
    return clean({"symbol": symbol, "as_of": tech.get("as_of"), "horizons": out,
                  "method": "기간별 기술 점수(이평 · 모멘텀 · 오실레이터 · 거래량 · 지지/저항)와 종목 매크로 정합도, "
                            "시장 국면, 공포·탐욕 지수, 펀더멘털, 실측 매크로 민감도를 문장으로 정리한 규칙 기반 분석입니다. 예측이 아닙니다.",
                  "updated_at": utcnow().isoformat()})
