"""At-a-glance views built from the universe scan (no extra market-data calls):

  * summary  - one-line market conclusion, watchlist names with a live signal, 5 buy candidates, caution list
  * matrix   - watchlist rows x indicator columns for a colour table (overheated / oversold at a glance)
  * heatmap  - index members sized by market cap, coloured by the day's change

Everything comes from the last scan; a symbol the scan does not cover is listed as missing, never estimated.
"""
from __future__ import annotations

import re

from ..analysis import fundamentals as fa
from ..analysis.fear_greed import LABEL_KO as FG_KO
from . import macro, recommendations, universe
from .store import clean, utcnow

REGIME_KO = {"Risk-On": "위험 선호", "Neutral": "중립", "Risk-Off": "위험 회피"}
TREND_LEVEL = {"Strong Bullish Structure": 2, "Bullish Structure": 1, "Mixed Structure": 0,
               "Bearish Structure": -1, "Strong Bearish Structure": -2}
TREND_KO = {2: "강한 상승", 1: "상승", 0: "혼조", -1: "하락", -2: "강한 하락"}
NEAR_HIGH = -0.03       # within 3% of the 52-week high counts as "고점권"
HEATMAP = {"SP500": "S&P 500", "KOSPI": "코스피 시총 상위 100"}


def _by_symbol() -> dict[str, dict]:
    return {r["symbol"]: r for r in universe.results()}


def _fscore(r: dict) -> float | None:
    """DART-based score for Korean names when there is one, otherwise the Yahoo fundamentals score."""
    if r.get("f_dart_score") is not None:
        return r["f_dart_score"]
    f = {k[2:]: v for k, v in r.items() if k.startswith("f_")}
    return fa.fundamental_score(f).get("score") if f else None


def _state(r: dict) -> str | None:
    if r.get("stoch_ml_overbought"):
        return "overheated"
    if r.get("rsi_ml_oversold"):
        return "oversold"
    return None


def _row(r: dict) -> dict:
    k = r.get("stoch_k") or {}
    lvl = TREND_LEVEL.get(r.get("trend"))
    return {"symbol": r["symbol"], "name": r.get("name"), "name_ko": r.get("name_ko"), "market": r.get("market"),
            "price": r.get("price"), "change_pct": r.get("change_pct"), "technical": r.get("technical"),
            "signal": r.get("signal"), "trend": lvl, "trend_label": TREND_KO.get(lvl),
            "rsi": r.get("rsi"), "stoch_short": k.get("short"), "stoch_medium": k.get("medium"), "stoch_long": k.get("long"),
            "vol_ratio": r.get("vol_ratio"), "fscore": _fscore(r), "dist_high": r.get("dist_52w_high"),
            "state": _state(r), "oversold_reasons": r.get("rsi_ml_oversold") or [], "as_of": r.get("as_of")}


def matrix(symbols: list[str]) -> dict:
    by = _by_symbol()
    rows = [_row(by[s]) for s in symbols if s in by]
    return clean({
        "rows": rows, "missing": [s for s in symbols if s not in by],
        "columns": [
            {"key": "trend", "label": "추세", "help": "이동평균 배열과 주가 위치로 본 구조 (강한 상승 ~ 강한 하락)"},
            {"key": "rsi", "label": "RSI", "help": "14일 RSI · 70 이상 과열, 30 이하 과매도"},
            {"key": "stoch_short", "label": "스토 단기", "help": "스토캐스틱 5-3-3 · 80 이상 과매수, 20 이하 과매도"},
            {"key": "stoch_medium", "label": "스토 중기", "help": "스토캐스틱 10-6-6"},
            {"key": "stoch_long", "label": "스토 장기", "help": "스토캐스틱 20-12-12 · 중기와 장기가 모두 80 이상이면 내 규칙상 과열"},
            {"key": "vol_ratio", "label": "거래량", "help": "오늘 거래량 ÷ 20일 평균"},
            {"key": "fscore", "label": "재무", "help": "재무 점수 0~100 (한국 종목은 DART 공시 기준, 없으면 야후 재무 지표)"},
            {"key": "dist_high", "label": "고점 대비", "help": "52주 고점 대비 하락률 · 3% 이내는 고점권"},
        ],
        "legend": "붉은 계열 = 과열 · 고점권(내 규칙에서 비중을 줄이는 쪽), 푸른 계열 = 과매도 · 눌림(반등 신호를 기다리는 쪽), 색이 진할수록 극단",
        "updated_at": utcnow().isoformat(), **recommendations._scan_meta()})


def heatmap(uni: str) -> dict:
    by = _by_symbol()
    tiles = []
    for s in universe.members(uni):
        r = by.get(s)
        if not r or not r.get("f_market_cap_usd") or r.get("change_pct") is None:
            continue
        # KR preferred lines (code not ending in 0) report the whole company's market cap: counting them would double the issuer
        if s.endswith((".KS", ".KQ")) and s[5] != "0":
            continue
        tiles.append({"symbol": s, "name": r.get("name_ko") or r.get("name"), "group": r.get("group") or "기타",
                      "cap_usd": r["f_market_cap_usd"], "change_pct": r["change_pct"], "price": r.get("price"),
                      "market": r.get("market"), "technical": r.get("technical")})
    tiles.sort(key=lambda t: -t["cap_usd"])
    total = sum(t["cap_usd"] for t in tiles) or 1
    wavg = sum(t["cap_usd"] * t["change_pct"] for t in tiles) / total if tiles else None
    return clean({"universe": uni, "name": HEATMAP.get(uni, uni), "tiles": tiles, "members": len(universe.members(uni)),
                  "cap_weighted_change": wavg, "advancers": sum(1 for t in tiles if t["change_pct"] > 0),
                  "decliners": sum(1 for t in tiles if t["change_pct"] < 0),
                  "about": "타일 크기 = 시가총액(달러 환산), 색 = 최근 거래일 등락률. 스캔에 시가총액이 없는 종목과 우선주(보통주와 시가총액이 중복)는 뺐습니다.",
                  "updated_at": utcnow().isoformat(), **recommendations._scan_meta()})


# ------------------------------------------------------------------ summary
def _headline() -> dict:
    b = universe.latest_breadth("SP500")
    pct200 = b.get("pct_above_sma200") if b else None
    try:
        reg = macro.regime(pct200)
    except Exception:
        reg = None
    try:
        from . import fear_greed
        fg = fear_greed.index_payload()
    except Exception:
        fg = None
    label = reg.get("label") if reg else None
    fgl = fg.get("label") if fg else None
    greedy, fearful = fgl in ("Greed", "Extreme Greed"), fgl in ("Fear", "Extreme Fear")
    if label is None:
        tone, text = "neutral", "매크로 국면 데이터를 불러오지 못했습니다 - 종목별 신호만 참고하세요"
    elif label == "Risk-Off":
        tone, text = "bad", "방어 우선 - 신규 매수는 줄이고 보유 종목 손절선부터 점검"
    elif label == "Risk-On":
        tone, text = (("warn", "위험 선호지만 심리가 과열 - 추격 매수 대신 눌림목만") if greedy else
                      ("good", "위험 선호 국면 - 추세가 살아 있는 종목은 매수 가능"))
    else:
        tone, text = (("warn", "중립 국면에 심리는 탐욕 - 신규 매수보다 과열 종목 정리가 먼저") if greedy else
                      ("warn", "중립 국면에 심리는 공포 - 과매도 반등 · 골든크로스가 확인된 종목만 분할로") if fearful else
                      ("neutral", "방향성이 약한 중립 국면 - 신호가 뜬 종목만 선별"))
    parts = []
    if reg:
        parts.append({"label": "매크로 국면", "value": REGIME_KO.get(label, label), "detail": f"Risk-On 점수 {reg.get('score'):.0f}/100",
                      "href": "/macro"})
    if fg and fg.get("value") is not None:
        parts.append({"label": "공포 · 탐욕", "value": f"{fg['value']:.0f} {FG_KO.get(fgl, fgl)}", "detail": f"{fg.get('as_of')} 기준",
                      "href": "/market"})
    if b:
        parts.append({"label": "S&P 500 시장 폭", "value": f"200일선 위 {pct200:.0f}%",
                      "detail": f"신고가 {b.get('new_highs')} · 신저가 {b.get('new_lows')}", "href": "/market"})
    top = sorted((reg or {}).get("reasons") or [], key=lambda x: -abs(x.get("points") or 0))[:3]
    return {"tone": tone, "text": text, "regime": label, "regime_score": reg.get("score") if reg else None,
            "fear_greed": fg.get("value") if fg else None, "fear_greed_label": fgl, "parts": parts,
            "drivers": [{"text": x["text"], "direction": x.get("direction")} for x in top]}


def _days_ago(text: str | None) -> int | None:
    m = re.search(r"\((\d+)일 전\)", text or "")
    return int(m.group(1)) if m else None


def _crosses(r: dict) -> tuple[str | None, str | None]:
    """(golden, dead) - only the more recent of the two is still the live one."""
    golden = next((x for x in r.get("bullish") or [] if x.startswith("골든크로스")), None)
    dead = next((x for x in r.get("bearish") or [] if x.startswith("데드크로스")), None)
    if golden and dead:
        g, d = _days_ago(golden), _days_ago(dead)
        if g is not None and d is not None:
            return (golden, None) if g <= d else (None, dead)
    return golden, dead


def _tags(r: dict) -> tuple[list[dict], int]:
    """(tags, priority) - buy-side events live on this name right now, in the user's own rule vocabulary."""
    tags, pri = [], 0
    sig = r.get("signal") or ""
    if "BUY" in sig:
        tags.append({"text": "매수 신호" if sig == "BUY" else "강한 매수", "tone": "up"})
        pri += 3
    if r.get("rsi_ml_oversold"):
        tags.append({"text": "RSI 과매도 - 반등 대기", "tone": "info"})
        pri += 2
    golden, _ = _crosses(r)
    if golden:
        tags.append({"text": golden, "tone": "up"})
        pri += 2
    if r.get("near_breakout") and not r.get("stoch_ml_overbought"):
        tags.append({"text": "돌파 임박", "tone": "up"})
        pri += 1
    if (r.get("vol_ratio") or 0) >= 2:
        tags.append({"text": f"거래량 {r['vol_ratio']:.1f}배", "tone": "info"})
        pri += 1
    return tags, pri


def _mini(r: dict, **extra) -> dict:
    return {"symbol": r["symbol"], "name": r.get("name_ko") or r.get("name"), "market": r.get("market"),
            "price": r.get("price"), "change_pct": r.get("change_pct"), "technical": r.get("technical"),
            "signal": r.get("signal"), **extra}


def _caution(r: dict) -> list[str]:
    why = []
    if r.get("stoch_ml_overbought"):
        k = r.get("stoch_k") or {}
        why.append(f"중기 {k.get('medium', 0):.0f} · 장기 {k.get('long', 0):.0f} 스토캐스틱 과매수")
    if "SELL" in (r.get("signal") or ""):
        why.append("기술 신호 매도")
    d = r.get("dist_52w_high")
    if d is not None and d >= NEAR_HIGH:
        why.append(f"52주 고점 {abs(d) * 100:.1f}% 이내")
    if (r.get("rsi") or 0) >= 70:
        why.append(f"RSI {r['rsi']:.0f} 과열")
    _, dead = _crosses(r)
    if dead:
        why.append(dead)
    if TREND_LEVEL.get(r.get("trend"), 0) <= -2:
        why.append("강한 하락 구조")
    return why


def summary(symbols: list[str]) -> dict:
    by = _by_symbol()
    rows = [by[s] for s in symbols if s in by]
    signals = []
    for r in rows:
        tags, pri = _tags(r)
        if tags:
            signals.append((pri, _mini(r, tags=tags[:3], state=_state(r))))
    signals.sort(key=lambda x: (-x[0], -(x[1]["technical"] or 0)))
    cautions = []
    for r in rows:
        why = _caution(r)
        if why:
            cautions.append((len(why), _mini(r, reasons=why[:3], state=_state(r))))
    cautions.sort(key=lambda x: (-x[0], x[1]["symbol"]))
    rank = recommendations.technical_ranking("ALL", 5)
    return clean({
        "headline": _headline(),
        "signals": [s for _, s in signals[:8]], "signals_total": len(signals),
        "candidates": [{"symbol": c["symbol"], "name": c.get("name_ko") or c["name"], "market": c["market"], "price": c["price"],
                        "change_pct": c.get("change_pct"), "technical": c["technical"], "signal": c["signal"],
                        "reason": (c.get("reason") or [])[:2], "time_horizon": c.get("time_horizon")} for c in rank["items"]],
        "candidates_note": f"전체 스캔 {rank['count']}개 중 기술 점수 상위 (내 규칙상 과열 · 과매도 {(rank.get('excluded_overbought') or {}).get('count', 0)}개 제외)",
        "cautions": [c for _, c in cautions[:8]], "cautions_total": len(cautions),
        "watch_count": len(symbols), "covered": len(rows), "missing": [s for s in symbols if s not in by],
        "updated_at": utcnow().isoformat(), **recommendations._scan_meta()})
