"""Rule-based opinion for a held position (보유 종목 투자 의견).

It restates the site's own scores in position terms and records every step, so the
UI can show exactly why a holding is labelled 강력 매수 / 추가 매수 / 보유 / 비중 축소 /
매도 추천. It is not a forecast and not personalised advice.

1. 판단 점수 (채점 방식에 따라)
   · 내 규칙 (기본): 0.30 × 종합 점수 + 0.25 × 선호 보유기간 전망 점수 + 0.45 × 진입 타이밍
     진입 타이밍 = 단기·중기·장기 RSI 과매수 감점 / 과매도 가산, 52주 · 역대 고점과 저항선 근처 감점,
     골든크로스 가산 / 데드크로스 감점 (analysis/user_rules.py)
   · 데이터 검증: 0.40 × 종합 점수 + 0.35 × 전망 점수 + 0.25 × 진입 타이밍 (시장별로 성과가 검증된 규칙만)
   (종합 점수가 없으면 기술적 점수를 쓴다)
   애널리스트 데이터(목표가 3곳 이상)가 있으면 0.85 × 위 점수 + 0.15 × 기업·애널리스트 점수.
   기업·애널리스트 점수 = 50 + 검증된 신호의 점수 (미국: 목표가 상승여력 35% 이상 +9 /
   한국: 주가가 평균 목표가 위 −16, 상승여력 10~20% −3). 목표가 수정 방향 · 의견 변경 · EPS 추정치 변화는
   과거 데이터상 성과 차이가 확인되지 않아 사유로만 보여 주고 점수에는 넣지 않는다.
2. 판단 점수 → 기본 의견, 종목 신호와 같은 기준점:
   ≥78 강력 매수 · ≥62 추가 매수 · >40 보유 · >28 비중 축소 · ≤28 매도 추천
3. 보유 상황 규칙 (적용되면 사유에 모두 표시):
   a. 손실이 허용 낙폭(설정값)을 넘고 추세도 약세(기술 신호 매도 이하 또는 200일선 아래) → 매도 추천
   b. 손실이 허용 낙폭을 넘었지만 추세는 유지 → 매수 의견을 보유로 제한 (물타기 보류)
   c. 비중이 한도(안정형 20% · 중립형 25% · 공격형 35%)를 넘으면 매수 의견을 보유로 제한,
      한도의 1.5배를 넘으면 보유 → 비중 축소
   d. 진입 타이밍 ≤ 30 (검증된 규칙이 모두 불리한 자리, 예: 한국의 급등 직후)이면 매수 의견을 보유로 제한
   e. 수익 +30% 이상인데 단기 전망이 약세면 보유 → 비중 축소 (일부 차익 실현 검토)
"""
from __future__ import annotations

ORDER = ["SELL", "TRIM", "HOLD", "BUY", "STRONG_BUY"]
LABEL = {"STRONG_BUY": "강력 매수", "BUY": "추가 매수", "HOLD": "보유", "TRIM": "비중 축소", "SELL": "매도 추천",
         "NA": "판단 보류"}
HORIZON_OF = {"Short": "short", "Medium": "medium", "Long": "long"}
HORIZON_KO = {"short": "단기", "medium": "중기", "long": "장기"}
VIEW_KO = {"Bullish": "강세", "Neutral": "중립", "Bearish": "약세"}
WEIGHT_LIMIT = {"Conservative": 20.0, "Moderate": 25.0, "Aggressive": 35.0}
PILLAR_KO = {"valuation": "밸류에이션", "growth": "성장성", "quality": "수익성", "balance_sheet": "재무 건전성",
             "profitability": "수익성", "stability": "재무 안정성", "cash_return": "현금흐름·주주환원"}
SIGNAL_KO = {"STRONG BUY": "강력 매수", "BUY": "매수", "NEUTRAL": "중립", "SELL": "매도", "STRONG SELL": "강력 매도"}
METHOD = ("판단 점수 = 0.40 × 종합 점수 + 0.35 × 선호 보유기간 전망 점수 + 0.25 × 진입 타이밍 → 78/62/40/28 기준으로 기본 의견, "
          "이후 손실 한도 · 비중 한도 · 과열(타이밍 ≤ 30) · 차익 실현 규칙을 적용")


def base_action(score: float) -> str:
    if score >= 78:
        return "STRONG_BUY"
    if score >= 62:
        return "BUY"
    if score > 40:
        return "HOLD"
    if score > 28:
        return "TRIM"
    return "SELL"


def _cap(action: str, ceiling: str) -> str:
    """Lower `action` to at most `ceiling`."""
    return action if ORDER.index(action) <= ORDER.index(ceiling) else ceiling


def _fmt(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{x:,.{d}f}"


def advise(position: dict, scores: dict | None, tech: dict | None, profile: dict, analyst: dict | None = None) -> dict:
    """`position`: price, avg_cost, unrealized_pl_pct, weight. `scores`/`tech`: the stock page payloads.
    `analyst`: services.analyst.view() (consensus target, recent reports, measured micro score)."""
    sym = position["symbol"]
    if not scores or not tech or position.get("price") is None:
        return {"symbol": sym, "action": "NA", "label": LABEL["NA"], "score": None,
                "summary": "가격 또는 분석 데이터를 불러올 수 없어 의견을 내지 않습니다 (Data unavailable).",
                "steps": [], "overrides": [], "positives": [], "negatives": [], "levels": [], "watch": [],
                "horizons": {}, "scores": {}, "method": METHOD}

    hz_key = HORIZON_OF.get(profile.get("holding_period") or "Medium", "medium")
    horizons = scores.get("horizons") or tech["signal"]["horizons"]
    hz = horizons[hz_key]
    overall = scores.get("overall")
    base_name = "종합 점수"
    if overall is None:
        overall, base_name = scores.get("technical"), "기술적 점수 (종합 점수 없음)"
    comps = {c["key"]: c for c in tech["signal"].get("components", [])}
    timing = (comps.get("timing") or {}).get("score")
    user_style = tech["signal"].get("style") == "user"
    wo, wh, wt = (0.30, 0.25, 0.45) if user_style else (0.40, 0.35, 0.25)
    if timing is None:
        score = 0.5 * overall + 0.5 * hz["score"]
    else:
        score = wo * overall + wh * hz["score"] + wt * timing
    base_score = score
    micro = (analyst or {}).get("micro")
    if micro:
        score = (1 - micro["weight"]) * base_score + micro["weight"] * micro["score"]
    action = base_action(score)

    used = scores.get("weights_used") or {}
    parts_ko = {"technical": "기술", "macro": "매크로", "fundamental": "펀더멘털"}
    blend_txt = " + ".join(f"{parts_ko[k]} {scores.get(k):.0f} × {w * 100:.0f}%" for k, w in used.items()
                           if scores.get(k) is not None)
    steps = [
        {"label": base_name, "value": round(overall, 1), "detail": blend_txt or None},
        {"label": f"{HORIZON_KO[hz_key]} 전망 점수 (선호 보유기간)", "value": hz["score"],
         "detail": f"{VIEW_KO.get(hz['view'], hz['view'])} · 설정 > 투자 성향에서 변경"},
        *([{"label": "진입 타이밍 (내 규칙)" if user_style else "진입 타이밍 (데이터 검증)", "value": round(timing, 1),
             "detail": "; ".join((comps["timing"].get("details") or [])[:3]) or None}] if timing is not None else []),
        *([{"label": "기업 · 애널리스트", "value": round(micro["score"], 1),
            "detail": "; ".join(micro["details"]) + f" · 근거 {micro['study']}"}] if micro else []),
        {"label": "판단 점수", "value": round(score, 1),
         "detail": ((f"{wo:.2f} × {overall:.1f} + {wh:.2f} × {hz['score']:.1f} + {wt:.2f} × {timing:.1f}" if timing is not None
                     else f"0.5 × {overall:.1f} + 0.5 × {hz['score']:.1f}")
                    + (f" = {base_score:.1f} → 0.85 × {base_score:.1f} + 0.15 × {micro['score']:.0f}" if micro else ""))},
        {"label": "기본 의견", "value": LABEL[action], "detail": "≥78 강력 매수 · ≥62 추가 매수 · >40 보유 · >28 비중 축소 · ≤28 매도 추천"},
    ]

    ind = tech.get("indicators") or {}
    sig = tech["signal"]
    price, avg = position["price"], position.get("avg_cost")
    pl = position.get("unrealized_pl_pct")
    weight = position.get("weight")
    max_dd = float(profile.get("max_drawdown") or 0.25)
    limit = WEIGHT_LIMIT.get(profile.get("risk_tolerance") or "Moderate", 25.0)
    sma200 = ind.get("sma200")
    below200 = sma200 is not None and price < sma200
    weak_trend = sig["signal"] in ("SELL", "STRONG SELL") or below200
    rsi, rsi_s = ind.get("rsi"), ind.get("rsi_short")
    short = horizons.get("short") or {}

    overrides: list[dict] = []
    estimated = bool(position.get("avg_cost_estimated"))
    if estimated:
        pl = None  # stand-in cost: P/L-based rules (손실 한도 · 차익 실현) cannot be judged

    def apply(new: str, rule: str, detail: str) -> None:
        nonlocal action
        if new != action:
            overrides.append({"rule": rule, "detail": detail, "from": LABEL[action], "to": LABEL[new]})
            action = new

    if pl is not None and pl <= -max_dd * 100:
        if weak_trend:
            why = f"기술 신호 {SIGNAL_KO.get(sig['signal'], sig['signal'])}" if not below200 else "주가가 200일선 아래"
            apply("SELL", "손실 한도", f"평가손실 {pl:.1f}%가 허용 낙폭 -{max_dd * 100:.0f}%를 넘었고 {why}")
        else:
            apply(_cap(action, "HOLD"), "손실 한도",
                  f"평가손실 {pl:.1f}%가 허용 낙폭 -{max_dd * 100:.0f}%를 넘음 - 추세는 유지 중이라 매도 대신 추가 매수만 보류")
    if weight is not None and weight > limit:
        apply(_cap(action, "HOLD"), "비중 한도", f"포트폴리오 비중 {weight:.1f}%가 한도 {limit:.0f}%를 넘어 추가 매수 보류")
        if weight > limit * 1.5 and action == "HOLD":
            apply("TRIM", "비중 한도", f"비중 {weight:.1f}%가 한도의 1.5배({limit * 1.5:.0f}%)를 넘음 - 일부 축소로 집중 위험 완화")
    if timing is not None and timing <= 30 and action in ("STRONG_BUY", "BUY"):
        apply("HOLD", "과열 구간", f"진입 타이밍 {_fmt(timing, 0)} (RSI {_fmt(rsi)}) - "
              + ("과매수 · 고점권이라 이미 늦은 자리, 추가 매수 보류" if user_style else
                 "과거 데이터상 이후 성과가 약했던 자리라 추가 매수 보류, 이격 해소를 기다림"))
    if action == "HOLD" and pl is not None and pl >= 30 and short.get("view") == "Bearish":
        apply("TRIM", "차익 실현", f"평가수익 +{pl:.1f}%인데 단기 전망 약세({short.get('score', 0):.0f}점) - 일부 차익 실현 검토")

    positives = list(sig.get("bullish_factors") or [])[:4]
    negatives = list(sig.get("bearish_factors") or [])[:4]
    md = scores.get("macro_detail") or {}
    positives += [f"매크로: {x}" for x in (md.get("reasons") or [])[:2]]
    negatives += [f"매크로: {x}" for x in (md.get("risks") or [])[:2]]
    for k, v in ((scores.get("fundamental_detail") or {}).get("pillars") or {}).items():
        s = v.get("score") if isinstance(v, dict) else None
        if s is None:
            continue
        if s >= 70:
            positives.append(f"펀더멘털: {PILLAR_KO.get(k, k)} 우수 ({s:.0f}/100)")
        elif s <= 40:
            negatives.append(f"펀더멘털: {PILLAR_KO.get(k, k)} 취약 ({s:.0f}/100)")
    a_pos, a_neg = analyst_reasons(analyst)
    positives += a_pos
    negatives += a_neg
    for k in ("short", "medium", "long"):
        h = horizons.get(k)
        if not h:
            continue
        txt = f"{HORIZON_KO[k]} 전망 {VIEW_KO.get(h['view'], h['view'])} ({h['score']:.0f}점)"
        if h["view"] == "Bullish":
            positives.append(txt)
        elif h["view"] == "Bearish":
            negatives.append(txt)

    sr = tech.get("support_resistance") or {}
    s1 = (sr.get("supports") or [None])[0]
    r1 = (sr.get("resistances") or [None])[0]
    stop = avg * (1 - max_dd) if avg and not estimated else None
    levels = []
    if s1:
        levels.append({"label": "지지선 S1", "price": s1["price"], "distance_pct": s1["distance_pct"], "projected": s1.get("projected")})
    if r1:
        levels.append({"label": "저항선 R1", "price": r1["price"], "distance_pct": r1["distance_pct"], "projected": r1.get("projected")})
    if sma200:
        levels.append({"label": "200일선", "price": sma200, "distance_pct": (sma200 / price - 1) * 100})
    if stop:
        levels.append({"label": f"허용 낙폭 기준가 (평단 -{max_dd * 100:.0f}%)", "price": stop, "distance_pct": (stop / price - 1) * 100})

    watch = []
    if s1 and action in ("STRONG_BUY", "BUY", "HOLD"):
        watch.append(f"지지선 S1 {_fmt(s1['price'], 2)} 아래로 종가 마감 시 기술 점수가 낮아져 의견 하향 가능")
    if r1 and action in ("HOLD", "TRIM", "BUY"):
        watch.append(f"저항선 R1 {_fmt(r1['price'], 2)} 돌파 시 돌파 점수가 올라 의견 상향 가능")
    if below200:
        watch.append(f"200일선 {_fmt(sma200, 2)} 회복 시 장기 추세 점수 개선")
    if stop and price > stop:
        watch.append(f"{_fmt(stop, 2)} 아래로 내려가면 손실 한도 규칙 적용")

    if estimated:
        steps.append({"label": "평단 미입력", "value": "규칙 일부 제외",
                      "detail": "평균 매입가를 모르기 때문에 손실 한도 · 차익 실현 규칙은 적용하지 않았습니다 (평단을 입력하면 반영)"})
    rules = list(dict.fromkeys(o["rule"] for o in overrides))
    rule_txt = f" → {', '.join(rules)} 규칙으로 {LABEL[action]}" if overrides else ""
    tm_txt = (f" · 진입 타이밍 {timing:.0f}" if timing is not None else "") + (f" · 애널리스트 {micro['score']:.0f}" if micro else "")
    summary = (f"{base_name} {overall:.0f} · {HORIZON_KO[hz_key]} 전망 {hz['score']:.0f}{tm_txt} → 판단 점수 {score:.0f}"
               f" ({LABEL[base_action(score)]}){rule_txt}")

    return {"symbol": sym, "action": action, "label": LABEL[action], "score": round(score, 1),
            "base_action": base_action(score), "summary": summary, "steps": steps, "overrides": overrides,
            "positives": positives, "negatives": negatives, "levels": levels, "watch": watch,
            "horizon_used": hz_key,
            "horizons": {k: {"score": v["score"], "view": v["view"]} for k, v in horizons.items() if isinstance(v, dict) and "score" in v},
            "rsi": {"short": rsi_s, "medium": rsi, "long": ind.get("rsi_long"), "periods": tech.get("rsi_periods")},
            "scores": {"overall": scores.get("overall"), "technical": scores.get("technical"), "macro": scores.get("macro"),
                       "timing": timing,
                       "fundamental": scores.get("fundamental"), "signal": sig["signal"]},
            "position": {"price": price, "avg_cost": None if estimated else avg, "unrealized_pl_pct": pl, "weight": weight,
                         "avg_cost_estimated": estimated, "since_ref_pct": position.get("since_ref_pct"),
                         "weight_limit": limit, "max_drawdown": max_dd},
            "analyst": analyst_block(analyst),
            "as_of": tech.get("as_of"), "method": METHOD}


def _won(v: float | None, ccy: str | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}원" if ccy == "KRW" else f"${v:,.2f}"


def analyst_reasons(a: dict | None) -> tuple[list[str], list[str]]:
    """Company-level (micro) reasons from the sell-side view. Scored items say so; the rest are marked 참고."""
    if not a or not a.get("available"):
        return [], []
    pos, neg = [], []
    m, c, ccy = a.get("micro"), a.get("consensus"), a.get("currency")
    if m and m["points"] > 0:
        pos.append(f"애널리스트: {m['details'][0]}")
    elif m and m["points"] < 0:
        neg.append(f"애널리스트: {m['details'][0]}")
    up = a.get("upside_pct")
    if c and up is not None and not (m and m["points"]):
        (pos if up >= 0 else neg).append(f"애널리스트 참고: 평균 목표가 {_won(c.get('mean'), ccy)} (현재가 대비 {up:+.0f}%, {c.get('n_label')})")
    k = a.get("counts") or {}
    r, d = k.get("pt_raises_90", 0), k.get("pt_cuts_90", 0)
    if r + d >= 3:
        if r >= 2 * d:
            pos.append(f"애널리스트 참고: 최근 90일 목표가 상향 {r} · 하향 {d}")
        elif d >= 2 * r:
            neg.append(f"애널리스트 참고: 최근 90일 목표가 하향 {d} · 상향 {r}")
    if k.get("upgrades_30"):
        pos.append(f"애널리스트 참고: 최근 30일 투자의견 상향 {k['upgrades_30']}건")
    if k.get("downgrades_30"):
        neg.append(f"애널리스트 참고: 최근 30일 투자의견 하향 {k['downgrades_30']}건")
    e = (a.get("eps") or {}).get("+1y") or {}
    if e.get("chg_pct") is not None and abs(e["chg_pct"]) >= 3:
        (pos if e["chg_pct"] > 0 else neg).append(f"애널리스트 참고: 내년 EPS 추정치 90일간 {e['chg_pct']:+.1f}%")
    return pos[:3], neg[:3]


def analyst_block(a: dict | None) -> dict | None:
    if not a:
        return None
    return {k: a.get(k) for k in ("available", "source", "consensus", "upside_pct", "counts", "recent", "flow", "micro", "currency",
                                  "ratings", "eps", "message")}
