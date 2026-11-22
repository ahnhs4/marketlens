"""Why did it move? Cause-and-effect sentences for the 12-hour recap, from Korean market headlines.

Korean financial headlines state the reason with the move ("국제유가, 중동 긴장에 상승", "기대 못 미친 은행 실적에
뉴욕증시 하락"). For every market group that moved in the window this module:
  1. searches Korean news (Google News, last 12 hours) for that market,
  2. keeps headlines that name the market and whose direction word agrees with the actual move,
  3. cuts the cause phrase out of the headline (the part before 에 / 로 / 속에 / 주시하며 / 이끈 ...),
  4. writes "원인 → 결과" sentences; a second, different cause is added with "이와 더불어".
No headline with a matching direction = no cause is claimed (the move is reported on its own).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from ..cache import cached
from ..providers.news import GoogleNewsRSS

log = logging.getLogger(__name__)

# group key, display name, member symbols (from recap.MARKETS/MACRO), searches, words that name the market
GROUPS = [
    ("oil", "국제유가", ["CL=F"], ["국제유가"], ["국제유가", "국제 유가", "유가", "WTI", "브렌트"]),
    ("us", "뉴욕증시", ["^GSPC", "^NDX", "^DJI", "^RUT"], ["뉴욕증시 마감", "뉴욕증시"], ["뉴욕증시", "뉴욕 증시", "미 증시", "美 증시", "미국 증시", "다우", "나스닥", "S&P"]),
    ("kr", "국내 증시", ["^KS11", "^KQ11"], ["코스피 마감 외국인", "코스피 마감", "코스피"], ["코스피", "코스닥", "국내 증시", "한국 증시"]),
    ("rates", "미 국채금리", ["^TNX"], ["미 국채금리"], ["국채금리", "국채 금리", "국채 10년물", "국채수익률", "美국채", "미 국채"]),
    ("krw", "원/달러 환율", ["KRW=X"], ["원달러 환율"], ["환율", "원·달러", "원/달러", "원달러", "원화"]),
    ("gold", "국제 금값", ["GC=F"], ["국제 금값"], ["금값", "금 가격", "금시세", "국제 금", "금 시세"]),
    ("btc", "비트코인", ["BTC-USD"], ["비트코인 가격"], ["비트코인"]),
    ("asia", "아시아 증시", ["^N225", "^HSI"], ["아시아 증시"], ["아시아 증시", "닛케이", "니케이", "항셍", "일본 증시", "중국 증시", "홍콩 증시"]),
]
UP = ("상승", "올라", "오름", "오른", "반등", "급등", "강세", "최고", "돌파", "치솟", "뛰", "회복", "↑")
DOWN = ("하락", "내려", "내린", "내림", "떨어", "급락", "약세", "최저", "붕괴", "밀려", "밀린", "폭락", "낙폭", "↓", "휘청")
FLAT = ("혼조", "보합", "관망", "진정", "숨고르기", "제자리")
MARKERS = ["여파로", "영향으로", "우려에", "기대에", "주시하며", "속에", "속 ", "에도 ", "에 ", "으로 ", "로 "]
TRUSTED = ("연합뉴스", "연합인포맥스", "한국경제", "매일경제", "조선비즈", "YTN", "뉴시스", "뉴스1", "이데일리", "머니투데이",
           "서울경제", "아시아경제", "헤럴드경제", "파이낸셜뉴스", "SBS", "KBS", "MBC", "연합뉴스TV", "한겨레", "중앙일보", "동아일보")


# ------------------------------------------------------------------ Korean helpers
def _batchim(word: str) -> bool:
    """Does the last sound of `word` end in a consonant (for 이/가, 을/를, 은/는)?"""
    w = re.sub(r"[\s)\]'\"’”]+$", "", word)
    if not w:
        return False
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return ch in "013678"
    return ch.upper() in "LMNR"


def josa(word: str, with_b: str, without_b: str) -> str:
    return word + (with_b if _batchim(word) else without_b)


def _clean(title: str) -> str:
    t = re.sub(r"^\s*(\[[^\]]*\]|【[^】]*】|<[^>]*>)\s*", "", title)
    t = re.sub(r"\s*(\(종합\d?\)|\(\d보\)|\(속보\)|\(종합\)|\(상보\))", "", t)
    t = re.sub(r"\s+-\s+[^-]+$", "", t)          # "- 조선비즈"
    t = re.sub(r"\s+By\s+.+$", "", t)            # Investing.com "By 알파경제"
    t = re.sub(r"\s*(\[[^\]]*\]|【[^】]*】)\s*$", "", t)   # trailing "[마감시황]"
    t = t.replace("美", "美 ").replace("  ", " ").replace("‧", "·")
    return re.sub(r"\s+", " ", t).strip()


# phrases that mean the opposite of their direction word: losses narrowed = a recovery, gains given back = a fall
REVERSALS = [(r"(낙폭|하락폭|손실)\s*(을|를)?\s*(축소|줄|만회|좁)", " 반등 "), (r"(상승폭|상승분|오름폭)\s*(을|를)?\s*(축소|반납|줄)", " 하락 ")]


def _direction(seg: str) -> str | None:
    """The last direction word in the segment wins ('낙폭 진정' -> flat, '낙폭 축소' -> up)."""
    for pat, rep in REVERSALS:
        seg = re.sub(pat, rep, seg)
    best, pos = None, -1
    for words, d in ((UP, "up"), (DOWN, "down"), (FLAT, "flat")):
        for w in words:
            i = seg.rfind(w)
            if i > pos:
                best, pos = d, i
    return best


def _agrees(d: str | None, move: float, unit: str) -> bool:
    if d is None:
        return False
    if d == "flat":
        return abs(move) < (4.5 if unit == "bp" else 0.75)
    return (move > 0) == (d == "up") and abs(move) > (0.5 if unit == "bp" else 0.02)


def _strip_subject(cause: str, subjects: list[str]) -> str:
    for s in sorted(subjects, key=len, reverse=True):
        if cause.startswith(s):
            cause = cause[len(s):].lstrip(" ,은는이가도")
    return cause.strip(" ,·'\"‘’“”")


def _usable(cause: str) -> bool:
    """A cause is a short noun phrase - not a number, a question or a fragment."""
    if not (4 <= len(cause) <= 48) or not re.search(r"[가-힣A-Za-z]{2,}", cause):
        return False
    if re.fullmatch(r"[\d.,\s원%달러포인트p·~-]+", cause) or re.search(r"[?？!]", cause):
        return False
    if re.search(r"(하나|나|까|는가|일까|할까|될까|연)$", cause):   # questions ('깨지나'), cut words ('연')
        return False
    return not re.match(r"^\d+(거래일|일|주|개월)", cause)                # '6거래일 연속' is a streak, not a cause


def _marker_cause(text: str, subjects: list[str]) -> dict | None:
    """'X에 하락', 'X 주시하며 혼조', 'X 속에 상승' -> cause X (the subject itself is dropped from the front)."""
    for mk in MARKERS:
        pat = (r"(?<=\s)" + re.escape(mk)) if mk.startswith("속") else re.escape(mk)  # '속에' as a word, never inside '연속'
        for mm in re.finditer(pat, text):
            after = text[mm.end():mm.end() + 28]
            if _direction(after) is None:
                continue
            cause = _strip_subject(text[:mm.start()].strip(), subjects)
            if _usable(cause):
                return {"cause": cause, "marker": mk.strip(), "direction": _direction(after)}
    return None


def extract(title: str, subjects: list[str]) -> dict | None:
    """{'cause', 'marker', 'direction'} from one headline about the market named by `subjects`, or None.
    The direction is for the market's own quote: '원화 약세' (KRW down) means the USD/KRW rate went UP."""
    if re.search(r"[\[【(][^\]】)]*(전망|예상|프리뷰)[^\]】)]*[\]】)]", title):
        return None   # a forecast written before the move is not its cause
    ex = _extract(title, subjects)
    if ex and "원화" in subjects:
        seg_txt = _clean(title)
        if "원화" in seg_txt and not re.search(r"환율", seg_txt):
            ex["direction"] = {"up": "down", "down": "up"}.get(ex["direction"], ex["direction"])
    return ex


def _extract(title: str, subjects: list[str]) -> dict | None:
    t = _clean(title)
    segs = [s.strip() for s in re.split(r"\s*(?:…|⋯|\.\.\.|\||;)\s*", t) if s.strip()]
    others = [w for g in GROUPS for w in g[4] if w not in subjects]

    def about_other(seg: str) -> bool:
        return any(w in seg for w in others) and not any(s in seg for s in subjects)
    for i, seg in enumerate(segs):
        if not any(s in seg for s in subjects):
            continue
        d = _direction(seg)
        # "... 급등 이끈 '재정적자 경고'"
        m = re.search(r"(?:급등|급락|상승|하락|강세|약세)\s*이끈\s*['‘\"“]?([^'’\"”]{3,40})['’\"”]?$", seg)
        if m:
            return {"cause": m.group(1).strip(), "marker": "로", "direction": d}
        rest = seg.split(",", 1)[1].strip() if "," in seg else seg
        found = _marker_cause(rest, subjects)
        if found:
            return found
        # "코스피 6000선 턱걸이… 외국인 매도에 0.44%↓": the cause sits in the next clause
        if i + 1 < len(segs) and not about_other(segs[i + 1]):
            found = _marker_cause(segs[i + 1], subjects)
            if found:
                return found
        # "원인…시장 결과": the clause before a short "subject + direction" clause is the cause
        if i > 0 and len(seg) <= 18 and d and not any(s in segs[i - 1] for s in subjects) and not about_other(segs[i - 1]) \
                and not any(w in segs[i - 1] for w in others):
            prev = segs[i - 1].strip(" ,'\"‘’“”")
            if _usable(prev) and not re.search(r"^[A-Za-z]+\s*['\"“]", prev):
                return {"cause": prev, "marker": "속에", "direction": d}
    return None


# ------------------------------------------------------------------ search
def _search(query: str, since: dt.datetime) -> list[dict]:
    def load():
        try:
            items = GoogleNewsRSS().search(query, language="ko", limit=20, window="1d")
        except Exception as e:
            log.info("recap cause search %s failed: %s", query, e)
            return []
        return [{"title": it.title, "source": it.source, "url": it.url,
                 "published_at": it.published_at.isoformat() if it.published_at else None} for it in items]
    rows = cached(f"recap:ko:{query}", 900, load)
    out = []
    for r in rows:
        if not r["published_at"]:
            continue
        t = dt.datetime.fromisoformat(r["published_at"])
        if t >= since:
            out.append(r)
    return out


def _lead(cause: dict) -> str:
    """The cause phrase with its connective: '중동 긴장에', '재정적자 경고로', '...을 주시하며', '... 속에'."""
    c, mk = cause["cause"], cause["marker"]
    if mk == "주시하며":
        return f"{josa(c, '을', '를')} 주시하며"
    if mk in ("로", "으로"):
        return c + ("으로" if _batchim(c) else "로")
    if mk == "에도":
        return f"{c}에도"
    if mk == "에":
        return f"{c}에"
    return f"{c} {mk}"


def _effect(name: str, ms: list[dict], main: dict, d: str, fmt_move, small: bool = False) -> tuple[str, str | None]:
    """('국제유가가 0.23% 올랐습니다', detail) - multi-index groups put the numbers in the detail."""
    verb = {"up": "올랐습니다", "down": "내렸습니다", "flat": "보합권에 머물렀습니다"}[d]
    if small and d != "flat":
        verb = "소폭 " + verb
    if len(ms) > 1:
        detail = " · ".join(fmt_move(m) for m in sorted(ms, key=lambda m: -m["weight"])[:3])
        return f"{josa(name, '이', '가')} {verb}", detail
    mv = main["move"]
    amount = f"{abs(mv):.0f}bp" if main["unit"] == "bp" else f"{abs(mv):.2f}%"
    level = (f"{main['level']:.2f}%" if main["unit"] == "bp" else f"{main['level']:,.1f}원" if main["symbol"] == "KRW=X" else None)
    if d == "flat":
        return f"{josa(name, '이', '가')} {mv:+.2f}%로 {verb}" if main["unit"] != "bp" else f"{josa(name, '이', '가')} {mv:+.0f}bp로 {verb}", level
    return f"{josa(name, '이', '가')} {amount} {verb}", level


def stories(moves: list[dict], since: dt.datetime, fmt_move) -> list[dict]:
    """One cause-and-effect item per market group that moved in the window, most important first."""
    by_sym = {m["symbol"]: m for m in moves}
    groups = []
    for key, name, syms, queries, subjects in GROUPS:
        ms = [by_sym[s] for s in syms if s in by_sym]
        if ms:
            groups.append((key, name, ms, max(ms, key=lambda m: m["weight"]), queries, subjects))

    def run(g):
        key, name, ms, main, queries, subjects = g
        heads = []
        for q in queries:
            heads += _search(q, since)
        # trusted outlets first, then the newest
        heads.sort(key=lambda h: (not any(t in (h["source"] or "") for t in TRUSTED), -(dt.datetime.fromisoformat(h["published_at"]).timestamp())))
        seen, found = set(), []
        for h in heads:
            if h["title"] in seen:
                continue
            seen.add(h["title"])
            ex = extract(h["title"], subjects)
            if not ex or not _agrees(ex["direction"], main["move"], main["unit"]):
                continue
            same = next((f for f in found if _similar(ex["cause"], f["cause"])), None)
            if same:
                same["sources"].append(h)
                continue
            found.append({**ex, "sources": [h]})
            if len(found) >= 2:
                break
        small = abs(main["move"]) < (1.5 if main["unit"] == "bp" else 0.1)
        if abs(main["move"]) < 1e-9 or (found and found[0]["direction"] == "flat"):
            d = "flat"
        else:
            d = "up" if main["move"] > 0 else "down"
        effect, detail = _effect(name, ms, main, d, fmt_move, small)
        if found:
            sentence = f"{_lead(found[0])} {effect}."
            if len(found) > 1:
                c2 = found[1]
                sentence += (f" 이와 더불어 시장은 {josa(c2['cause'], '을', '를')} 주시했습니다." if c2["marker"] == "주시하며" else
                             f" 이와 더불어 {c2['cause']}도 영향을 줬습니다.")
        else:
            sentence = f"{effect}."
        return {"key": key, "name": name, "move": main["move"], "unit": main["unit"], "direction": d,
                "weight": max(m["weight"] for m in ms), "z": main.get("z"), "sentence": sentence, "detail": detail,
                "moves": [{"name": m["name"], "text": fmt_move(m), "move": m["move"]} for m in ms],
                "causes": [{"cause": f["cause"], "marker": f["marker"], "sources": f["sources"][:3]} for f in found],
                "has_cause": bool(found)}

    with ThreadPoolExecutor(max_workers=6) as ex:
        out = list(ex.map(run, groups))
    return sorted(out, key=lambda s: -s["weight"])


def _similar(a: str, b: str) -> bool:
    ta, tb = set(re.findall(r"[가-힣A-Za-z]{2,}", a)), set(re.findall(r"[가-힣A-Za-z]{2,}", b))
    return bool(ta and tb) and len(ta & tb) / min(len(ta), len(tb)) >= 0.5
