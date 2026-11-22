"""'지난 12시간' market recap with importance weights.

Paragraph 1 - what prices did and why: one cause-and-effect sentence per market, the cause taken from Korean
market headlines of the same window whose direction matches the move (services/recap_causes.py).
Paragraph 2 - which stories mattered most (all outlets, importance-weighted).
Weights (shown with the recap):
  market / macro move  = asset weight × (1 + z),  z = |move| / typical daily move (1-year std of daily changes)
  news theme           = Σ top-3 story priority × recency (1 at 0h → 0.5 at 12h) × (1 + 0.25 × extra outlets)
Only quotes whose last trade falls inside the window and stories published inside it are used.
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np

from ..cache import cached
from ..i18n_ko import CATEGORY_KO, GROUP_KO
from . import market, news, prices
from .store import clean, utcnow

log = logging.getLogger(__name__)

WINDOW_H = 12
# (symbol, name, weight, unit) - unit "pct" or "bp" (yield level changes)
MARKETS = [("^GSPC", "S&P 500", 1.0, "pct"), ("^NDX", "나스닥 100", 0.9, "pct"), ("^DJI", "다우존스", 0.6, "pct"),
           ("^RUT", "러셀 2000", 0.5, "pct"), ("^KS11", "코스피", 1.0, "pct"), ("^KQ11", "코스닥", 0.8, "pct"),
           ("^N225", "니케이 225", 0.5, "pct"), ("^HSI", "항셍", 0.5, "pct"), ("^STOXX50E", "유로스톡스 50", 0.5, "pct")]
MACRO = [("^TNX", "미 10년물 금리", 0.9, "bp"), ("DX-Y.NYB", "달러인덱스", 0.6, "pct"), ("KRW=X", "원/달러 환율", 0.7, "pct"),
         ("CL=F", "WTI 유가", 0.6, "pct"), ("GC=F", "금", 0.5, "pct"), ("^VIX", "VIX", 0.7, "pct"), ("BTC-USD", "비트코인", 0.4, "pct")]
REGION = {"^GSPC": "US", "^NDX": "US", "^DJI": "US", "^RUT": "US", "^KS11": "KR", "^KQ11": "KR",
          "^N225": "ASIA", "^HSI": "ASIA", "^STOXX50E": "EU"}
DIR_KO = {"up": "↑", "down": "↓", "mixed": "↕", "neutral": "→"}


def _sigma(sym: str, unit: str) -> float | None:
    """Typical daily move over the last year (std of daily % changes, or bp changes for yields)."""
    def load():
        try:
            c = prices.get_daily(sym, years=2).df["close"].dropna().iloc[-252:]
        except Exception:
            return None
        d = c.diff() * 100 if unit == "bp" else c.pct_change() * 100  # Yahoo ^TNX is the yield in %, so Δ × 100 = bp
        v = float(d.dropna().std())
        return v if np.isfinite(v) and v > 0 else None
    return cached(f"recap:sigma:{sym}", 86400, load)


def _moves(since: dt.datetime) -> list[dict]:
    syms = [s for s, *_ in MARKETS + MACRO]
    quotes = prices.get_quotes(syms)
    out = []
    for sym, name, w, unit in MARKETS + MACRO:
        q = quotes.get(sym)
        if q is None or q.change_pct is None or q.quote_time is None:
            continue
        qt = q.quote_time if q.quote_time.tzinfo else q.quote_time.replace(tzinfo=dt.timezone.utc)
        if qt < since:
            continue
        move = (q.change or 0) * 100 if unit == "bp" else q.change_pct
        sig = _sigma(sym, unit)
        z = abs(move) / sig if sig else None
        out.append({"symbol": sym, "name": name, "unit": unit, "move": move, "level": q.price, "z": z,
                    "weight": round(w * (1 + (z or 0)), 2), "time": qt.isoformat(), "region": REGION.get(sym, "MACRO"),
                    "session": q.market_state})
    return out


def _fmt_move(m: dict) -> str:
    if m["unit"] == "bp":
        return f"{m['name']} {m['level']:.2f}%({m['move']:+.0f}bp)"
    if m["symbol"] == "KRW=X":
        return f"{m['name']} {m['level']:,.1f}원({m['move']:+.2f}%)"
    return f"{m['name']} {m['move']:+.2f}%"


def _move_detail(m: dict) -> str:
    if m["unit"] == "bp":
        return f"{m['level']:.2f}%, {m['move']:+.0f}bp"
    if m["symbol"] == "KRW=X":
        return f"{m['level']:,.1f}원, {m['move']:+.2f}%"
    return f"{m['move']:+.2f}%"


def _themes(since: dt.datetime, now: dt.datetime) -> tuple[list[dict], dict]:
    try:
        nws = news.market_news()
    except Exception as e:
        log.info("recap news unavailable: %s", e)
        return [], {}
    groups: dict[str, list[dict]] = {}
    sent = {"pos": 0, "neg": 0, "neu": 0}
    for it in nws.get("items", []):
        try:
            t = dt.datetime.fromisoformat(it["published_at"])
        except (TypeError, ValueError, KeyError):
            continue
        if t < since:
            continue
        age_h = max(0.0, (now - t).total_seconds() / 3600)
        rec = 1 - 0.5 * min(age_h, WINDOW_H) / WINDOW_H
        score = (it.get("priority") or 0) * rec * (1 + 0.25 * max(0, (it.get("cluster_size") or 1) - 1))
        it = {**it, "score": round(score, 1), "age_h": round(age_h, 1)}
        groups.setdefault(it.get("category") or "Other", []).append(it)
        s = it.get("sentiment") or ""
        sent["pos" if "Bullish" in s else "neg" if "Bearish" in s else "neu"] += 1
    themes = []
    for cat, items in groups.items():
        items.sort(key=lambda x: -x["score"])
        w = sum(x["score"] for x in items[:3])
        top = items[0]
        bull = sum(1 for x in items if "Bullish" in (x.get("sentiment") or ""))
        bear = sum(1 for x in items if "Bearish" in (x.get("sentiment") or ""))
        themes.append({"category": cat, "label": CATEGORY_KO.get(cat, cat), "weight": round(w, 1), "count": len(items),
                       "tone": "긍정적" if bull > bear else "부정적" if bear > bull else "혼조",
                       "top": {"title": top["title"], "source": top.get("source"), "url": top.get("url"),
                               "impact": top.get("impact"), "sentiment": top.get("sentiment"), "age_h": top["age_h"],
                               "chain": top.get("chain") or [], "sectors": top.get("sectors") or []},
                       "items": [{"title": x["title"], "source": x.get("source"), "score": x["score"], "url": x.get("url")} for x in items[:4]]})
    themes.sort(key=lambda t: -t["weight"])
    return themes, sent


def _chain_txt(chain: list[dict]) -> str | None:
    nodes = [f"{c.get('node')}{DIR_KO.get(c.get('direction'), '')}" for c in chain if c.get("node")]
    return " → ".join(nodes[:4]) if len(nodes) >= 2 else None


def build(hours: int = WINDOW_H) -> dict:
    now = utcnow()
    since = now - dt.timedelta(hours=hours)
    moves = _moves(since)
    themes, sent = _themes(since, now)
    fg = None
    try:
        from .fear_greed import index_payload
        fg = index_payload()
    except Exception:
        pass
    sect = None
    try:
        sect = market.sectors()
    except Exception:
        pass

    # ---------------------------------------------------------------- why it moved
    try:
        from .recap_causes import stories as cause_stories
        stories = cause_stories(moves, since, _fmt_move)
    except Exception as e:
        log.info("recap causes failed: %s", e)
        stories = []
    grouped = {mv["name"] for st in stories for mv in st["moves"]}
    others = [m for m in sorted(moves, key=lambda m: -m["weight"]) if m["name"] not in grouped]
    lead = next((st for st in stories[:3] if st["has_cause"]), stories[0] if stories else None)

    # ---------------------------------------------------------------- paragraph 1
    p1: list[str] = []
    eq = sorted([m for m in moves if m["region"] != "MACRO"], key=lambda m: -m["weight"])
    mac = sorted([m for m in moves if m["region"] == "MACRO"], key=lambda m: -m["weight"])
    if not moves:
        p1.append(f"지난 {hours}시간 안에 거래된 주요 지수 · 자산 시세가 없습니다 (휴장 또는 데이터를 불러올 수 없음).")
    elif stories:
        p1 += [st["sentence"] + (f" ({st['detail']})" if st.get("detail") else "") for st in stories]
    else:
        top = max(moves, key=lambda m: m["weight"])
        zt = f", 평소 하루 변동의 {top['z']:.1f}배" if top.get("z") else ""
        p1.append(f"지난 {hours}시간 가장 눈에 띈 움직임은 {top['name']}({_move_detail(top)}{zt})입니다.")
        by = {r: [m for m in eq if m["region"] == r] for r in ("US", "KR", "ASIA", "EU")}

        def word(ms: list[dict]) -> str:
            avg = float(np.mean([m["move"] for m in ms]))
            return "상승" if avg > 0.1 else "하락" if avg < -0.1 else "보합"
        if by["US"]:
            p1.append(f"뉴욕 증시는 {', '.join(_fmt_move(m) for m in by['US'][:3])}로 {word(by['US'])} 마감했습니다.")
        if by["KR"]:
            live = any(m["session"] == "REGULAR" for m in by["KR"])
            p1.append(f"한국 증시는 {', '.join(_fmt_move(m) for m in by['KR'])}로 "
                      + (f"{word(by['KR'])} 흐름 속에 거래 중입니다." if live else f"{word(by['KR'])} 마감했습니다."))
        other = sorted(by["ASIA"] + by["EU"], key=lambda m: -m["weight"])
        if other:
            p1.append("그 밖에 " + ", ".join(_fmt_move(m) for m in other[:2]) + "를 기록했습니다.")
        if mac:
            p1.append("금리 · 환율 · 원자재 쪽에서는 " + ", ".join(_fmt_move(m) for m in mac[:4]) + "로 움직였습니다.")
    extras: list[str] = []
    if fg:
        prev = (fg.get("compare") or {}).get("previous")
        d = f", 전일 대비 {fg['value'] - prev['value']:+.0f}" if prev else ""
        label = {"Extreme Fear": "극단적 공포", "Fear": "공포", "Neutral": "중립", "Greed": "탐욕", "Extreme Greed": "극단적 탐욕"}.get(fg["label"], "")
        extras.append(f"공포·탐욕 지수는 {fg['value']:.0f}({label}){d}입니다.")
    if sect and sect.get("sectors"):
        rows = [r for r in sect["sectors"] if r.get("perf") and r["perf"].get("1d") is not None]
        if len(rows) >= 4:
            rows.sort(key=lambda r: -r["perf"]["1d"])
            hi = ", ".join(f"{r['name']}({r['perf']['1d']:+.1f}%)" for r in rows[:2])
            lo = ", ".join(f"{r['name']}({r['perf']['1d']:+.1f}%)" for r in rows[::-1][:2])
            extras.append(f"미국 업종별로는 {hi} 순으로 강했고, {lo} 순으로 약했습니다.")

    # ---------------------------------------------------------------- paragraph 2
    p2: list[str] = []
    if not themes:
        p2.append(f"지난 {hours}시간 안에 수집된 시장 뉴스가 없습니다.")
    else:
        t1 = themes[0]
        ch = _chain_txt(t1["top"]["chain"])
        p2.append(f"중요도가 가장 높은 이슈는 '{t1['label']}' 관련 소식입니다(기사 {t1['count']}건, 분위기 {t1['tone']}). "
                  f"대표 기사는 '{t1['top']['title'][:90]}'({t1['top']['source']})이며"
                  + (f", 파급 경로는 {ch} 순서로 해석됩니다." if ch else ", 시장 전반에 영향을 주는 이슈로 분류됐습니다."))
        rest = themes[1:3]
        if rest:
            p2.append("그다음은 " + ", ".join(f"'{t['label']}'({t['count']}건 · 대표: {t['top']['title'][:60]})" for t in rest)
                      + " 순서입니다.")
        # tie the top story to the price action when they point the same way
        rates = next((m for m in mac if m["symbol"] == "^TNX"), None)
        if t1["category"] in ("Fed", "Treasury", "Inflation") and rates:
            p2.append(f"실제로 미 10년물 금리가 {rates['move']:+.0f}bp 움직여 금리 관련 뉴스가 가격에도 반영됐습니다."
                      if abs(rates["move"]) >= 3 else "다만 미 10년물 금리 변화는 크지 않아 가격 반영은 제한적이었습니다.")
        total = sum(sent.values())
        if total:
            p2.append(f"{hours}시간 동안 고유 기사 {total}건 중 긍정 {sent['pos']} · 부정 {sent['neg']} · 중립 {sent['neu']}건으로 "
                      + ("뉴스 흐름은 긍정 쪽이었습니다." if sent["pos"] > sent["neg"] * 1.3 else
                         "뉴스 흐름은 부정 쪽이었습니다." if sent["neg"] > sent["pos"] * 1.3 else "뉴스 흐름은 엇갈렸습니다."))
        sectors = [GROUP_KO.get(s, s) for s in t1["top"]["sectors"][:3]]
        if sectors:
            p2.append(f"다음으로 볼 것은 {t1['label']} 후속 보도와 {', '.join(sectors)} 쪽 반응입니다.")

    weights = ([{"kind": "시세", "label": _fmt_move(m), "weight": m["weight"],
                 "basis": f"자산 가중치 × (1 + {m['z']:.1f}σ)" if m.get("z") else "자산 가중치 (변동성 데이터 없음)"} for m in sorted(moves, key=lambda m: -m["weight"])]
               + [{"kind": "뉴스", "label": f"{t['label']} ({t['count']}건)", "weight": t["weight"],
                   "basis": "상위 3개 기사 중요도 × 최신성 × 보도 매체 수"} for t in themes[:6]])
    return clean({"hours": hours, "since": since.isoformat(), "until": now.isoformat(),
                  "paragraphs": [" ".join(p1 + extras), " ".join(p2)], "moves": moves, "themes": themes[:6], "weights": weights,
                  "lead": lead, "stories": stories, "others": [{"name": m["name"], "text": _fmt_move(m), "move": m["move"]} for m in others],
                  "extras": extras,
                  "method": "원인은 같은 12시간 안의 국내 경제 기사 제목에서 가져오며, 기사의 방향(상승/하락/혼조)이 실제 가격 움직임과 맞을 때만 연결합니다. "
                            "맞는 기사가 없으면 움직임만 적습니다. 시세 중요도 = 자산 가중치 × (1 + 평소 하루 변동 대비 배수), "
                            "뉴스 중요도 = 상위 3개 기사 우선순위 합 × 최신성 × 매체 수. 규칙 기반으로 자동 작성됩니다 (AI 아님).",
                  "updated_at": now.isoformat()})


def recap(hours: int = WINDOW_H) -> dict:
    return cached(f"recap:{hours}", 600, lambda: build(hours))
