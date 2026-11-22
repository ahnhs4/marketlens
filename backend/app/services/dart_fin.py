"""Korean company financials from OpenDART: storage, market-wide collection, per-company analysis and the KR score.

* Whole market (~2,600 listed companies): main BS/IS accounts per period via fnlttMultiAcnt (100 companies per call).
  Annual reports for the last four years and quarterly reports for the last three, refreshed daily while filings arrive.
* Per company (stock page, scan universe, official low-PBR list): full statements (parent-company profit and equity,
  cash flow) for the latest period and the previous annual report, plus dividends.
* Market cap: every share class summed per company (Naver lists), so PBR/PER are company-level.
Everything is stored in `dart_financials`; pages read the database, never DART directly, except to fill a gap.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select

from ..analysis import kr_financials as kf
from ..cache import cache, cached
from ..config import BACKEND_DIR
from ..db import SessionLocal
from ..models import DartCorp, DartFinancial
from ..providers.base import ProviderError
from ..providers.dart import REPORT_KO, REPORTS, dart
from ..providers.krx import krx
from ..providers.naver import naver_kr
from .store import clean, upsert, utcnow
from .symbols import index

log = logging.getLogger(__name__)
MARKET_LOG = BACKEND_DIR / "data" / "dart_market_log.json"
STRICT_FIN = {"은행 및 저축기관", "보험업", "재 보험업", "금융 지원 서비스업", "신탁업 및 집합투자업", "보험 및 연금관련 서비스업"}
_state: dict = {"state": "idle", "processed": 0, "total": 0, "message": None, "started_at": None, "finished_at": None}
_run_lock = threading.Lock()
_company_locks: dict[str, threading.Lock] = {}


def enabled() -> bool:
    return dart.enabled


def status() -> dict:
    return {**_state, "enabled": enabled()}


# ------------------------------------------------------------------ listings / identifiers
def corp_map() -> dict[str, dict]:
    """stock_code -> {corp_code, corp_name}; refreshed from DART weekly."""
    def load():
        with SessionLocal() as db:
            rows = db.execute(select(DartCorp)).scalars().all()
        fresh = rows and (utcnow() - max(r.fetched_at for r in rows)) < dt.timedelta(days=7)
        if (not rows or not fresh) and enabled():
            try:
                got = dart.corp_codes()
                now = utcnow()
                with SessionLocal() as db:
                    upsert(db, DartCorp, [{**g, "fetched_at": now} for g in got], ["corp_code"])
                    db.commit()
                return {g["stock_code"]: {"corp_code": g["corp_code"], "corp_name": g["corp_name"]} for g in got}
            except ProviderError as e:
                log.warning("DART corp codes unavailable: %s", e)
        return {r.stock_code: {"corp_code": r.corp_code, "corp_name": r.corp_name} for r in rows}
    return cached("dart:corps", 6 * 3600, load)


def listed() -> dict[str, dict]:
    """Common stocks listed today (code -> row) with company market cap = all share classes summed."""
    def load():
        rows = naver_kr.market_values()
        stocks = [r for r in rows if r["type"] == "stock" and r.get("code")]
        by5: dict[str, float] = {}
        for r in stocks:
            by5[r["code"][:5]] = by5.get(r["code"][:5], 0.0) + (r.get("market_cap") or 0.0)
        out = {}
        for r in stocks:
            if not r["code"].endswith("0"):          # preferred shares (5/7/9/K...) roll into their company
                continue
            name = r["name"] or ""
            if "스팩" in name or "리츠" in name or name.endswith("REIT"):
                continue
            out[r["code"]] = {**r, "company_cap": by5.get(r["code"][:5]) or r.get("market_cap"),
                              "has_pref": by5.get(r["code"][:5], 0) > (r.get("market_cap") or 0) + 1}
        return out
    return cached("dart:listed", 3600, load)


def symbol_of(code: str, market: str | None = None) -> str:
    for suf in ((".KS", ".KQ") if market != "KOSDAQ" else (".KQ", ".KS")):
        if index.get(f"{code}{suf}"):
            return f"{code}{suf}"
    return f"{code}{'.KQ' if market == 'KOSDAQ' else '.KS'}"


def is_financial(code: str, name: str | None = None) -> bool:
    row = index.get(symbol_of(code)) or {}
    ind = row.get("industry") or ""
    name = name or row.get("name_ko") or ""
    return ind in STRICT_FIN or (ind == "기타 금융업" and ("금융" in name or name == "신한지주"))


# ------------------------------------------------------------------ periods
def _period_end(y: int, r: str) -> dt.date:
    return {"11013": dt.date(y, 3, 31), "11012": dt.date(y, 6, 30), "11014": dt.date(y, 9, 30), "11011": dt.date(y, 12, 31)}[r]


def market_periods(today: dt.date | None = None) -> list[tuple[int, str]]:
    """Annual reports for the last four fiscal years and quarterly reports for the last three, oldest first."""
    today = today or dt.date.today()
    y = today.year
    out = [(yy, "11011") for yy in range(y - 4, y)]
    out += [(yy, r) for yy in range(y - 2, y + 1) for r in ("11013", "11012", "11014")]
    return sorted([p for p in out if _period_end(*p) < today], key=lambda p: (p[0], REPORTS[p[1]]))


def _open(p: tuple[int, str], today: dt.date) -> bool:
    """Still receiving filings or corrections: within ~5 months of the period end (annual: ~4 months)."""
    return (today - _period_end(*p)).days <= (130 if p[1] == "11011" else 75)


def _load_log() -> dict:
    try:
        return json.loads(MARKET_LOG.read_text())
    except Exception:
        return {}


def refresh_market(force: bool = False) -> dict:
    """Fetch main accounts for every listed company for each needed period (skips closed periods already stored)."""
    if not enabled():
        return {"ok": False, "message": "DART_API_KEY가 설정되지 않았습니다"}
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "message": "이미 수집 중입니다"}
    try:
        corps = corp_map()
        live = listed()
        codes = [c["corp_code"] for s, c in corps.items() if s in live]
        today = dt.date.today()
        mlog = _load_log()
        todo = [p for p in market_periods(today)
                if force or f"{p[0]}-{p[1]}" not in mlog
                or (_open(p, today) and (time.time() - mlog[f"{p[0]}-{p[1]}"]) > 20 * 3600)]
        _state.update(state="running", processed=0, total=len(todo) * ((len(codes) + 99) // 100), errors=0,
                      started_at=utcnow().isoformat(), finished_at=None, message=f"{len(todo)}개 보고기간 수집")
        by_code = {c["corp_code"]: s for s, c in corps.items()}
        for y, r in todo:
            stored = 0

            def fetch(part: list[str], y=y, r=r) -> list[dict]:
                try:
                    return dart.multi_accounts(part, y, r)
                finally:
                    _state["processed"] += 1
            chunks = [codes[i:i + 100] for i in range(0, len(codes), 100)]
            with ThreadPoolExecutor(max_workers=4) as ex:      # DART answers a 100-company call in ~5 s; 4 at a time
                futs = {ex.submit(fetch, part): part for part in chunks}
                for fut in as_completed(futs):
                    part = futs[fut]
                    try:
                        rows = fut.result()
                    except ProviderError as e:
                        _state["errors"] = _state.get("errors", 0) + 1
                        log.warning("DART multi %s %s: %s", y, r, e)
                        if "요청 제한" in str(e) or "키" in str(e):
                            raise
                        continue
                    parsed = kf.parse_main(rows)
                    code_to_corp = {s: c for c, s in by_code.items() if c in set(part)}
                    recs = [{"corp_code": code_to_corp[s], "stock_code": s, "year": y, "reprt": r, "kind": "main",
                             "fs_div": d["fs"], "rcept_no": d.get("rcept_no"), "data": clean(d), "fetched_at": utcnow()}
                            for s, d in parsed.items() if s in code_to_corp]
                    if recs:
                        with SessionLocal() as db:
                            upsert(db, DartFinancial, recs, ["corp_code", "year", "reprt", "kind"])
                            db.commit()
                    stored += len(recs)
            mlog[f"{y}-{r}"] = time.time()
            MARKET_LOG.write_text(json.dumps(mlog))
            log.info("DART %s %s: %d companies", y, REPORT_KO[r], stored)
        cache.delete_prefix("dart:snapshot")
        cache.delete_prefix("valueup:")
        _state.update(state="done", finished_at=utcnow().isoformat(),
                      message=f"{len(todo)}개 보고기간, 오류 {_state.get('errors', 0)}건")
        return {"ok": True, "periods": len(todo)}
    except Exception as e:
        _state.update(state="error", finished_at=utcnow().isoformat(), message=str(e)[:300])
        log.exception("DART market refresh failed")
        return {"ok": False, "message": str(e)[:300]}
    finally:
        _run_lock.release()


# ------------------------------------------------------------------ stored data
def _rows(corp_codes: list[str] | None, kinds: tuple[str, ...]) -> list[DartFinancial]:
    with SessionLocal() as db:
        q = select(DartFinancial).where(DartFinancial.kind.in_(kinds))
        if corp_codes is not None:
            q = q.where(DartFinancial.corp_code.in_(corp_codes))
        return list(db.execute(q).scalars().all())


def _group(rows: list[DartFinancial]) -> dict[str, dict[str, dict]]:
    """corp_code -> kind -> {(year, reprt): data}."""
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        out.setdefault(r.corp_code, {}).setdefault(r.kind, {})[(r.year, r.reprt)] = r.data or {}
    return out


def refresh_company(code: str, max_age_h: float = 20) -> bool:
    """Full statements for the latest period + the previous annual report, and the latest dividends."""
    corp = corp_map().get(code)
    if not corp or not enabled():
        return False
    cc = corp["corp_code"]
    lock = _company_locks.setdefault(cc, threading.Lock())
    with lock:
        have = _group(_rows([cc], ("main", "full", "div"))).get(cc, {})
        main = have.get("main", {})
        at = kf.latest(main)
        if not at:     # nothing market-wide yet (new listing, or the market load has not run): look for recent periods
            at = next((p for p in list(reversed(market_periods()))[:3] if _try_full(cc, code, *p)), None)
            if not at:
                return False
        prefer = (main.get(at) or {}).get("fs") or "CFS"
        need = [at] + ([(at[0] - 1, "11011")] if at[1] != "11011" else [])
        with SessionLocal() as db:
            got = {(r.year, r.reprt, r.kind): r for r in db.execute(select(DartFinancial).where(
                DartFinancial.corp_code == cc, DartFinancial.kind.in_(("full", "div")))).scalars().all()}
        stale = lambda k: k not in got or (not got[k].data and (utcnow() - got[k].fetched_at).total_seconds() > max_age_h * 3600)  # noqa: E731
        fetched = False
        for y, r in need:
            if stale((y, r, "full")):
                _try_full(cc, code, y, r, prefer)
                fetched = True
        fy = at[0] if at[1] == "11011" else at[0] - 1
        if stale((fy, "11011", "div")):
            try:
                d = kf.parse_dividends(dart.dividends(cc, fy))
                _store(cc, code, fy, "11011", "div", None, d)
                fetched = True
            except ProviderError as e:
                log.info("DART dividends %s: %s", code, e)
        if fetched:
            cache.delete(f"dart:company:{code}")
        return fetched


def _try_full(cc: str, code: str, y: int, r: str, prefer: str = "CFS") -> bool:
    other = "OFS" if prefer == "CFS" else "CFS"
    try:
        rows, fs = dart.full_statements(cc, y, r, prefer), prefer
        if not rows:
            rows, fs = dart.full_statements(cc, y, r, other), other
    except ProviderError as e:
        log.info("DART full %s %s %s: %s", code, y, r, e)
        return False
    d = kf.parse_full(rows, r) if rows else {}
    if d:
        d["fs"] = fs
    _store(cc, code, y, r, "full", fs if rows else None, d)
    return bool(rows)


def _store(cc: str, code: str, y: int, r: str, kind: str, fs: str | None, data: dict) -> None:
    with SessionLocal() as db:
        upsert(db, DartFinancial, [{"corp_code": cc, "stock_code": code, "year": y, "reprt": r, "kind": kind, "fs_div": fs,
                                    "rcept_no": (data or {}).get("rcept_no"), "data": clean(data or {}), "fetched_at": utcnow()}],
               ["corp_code", "year", "reprt", "kind"])
        db.commit()


# ------------------------------------------------------------------ official lists (KIND)
def kind_lists() -> dict:
    """Exchange's low-PBR list and value-up plan disclosures, keyed by stock code."""
    def load():
        live = listed()
        by_name = {(r.get("name") or "").replace(" ", ""): c for c, r in live.items()}

        def code_of(isur: str, name: str) -> str | None:
            c = f"{isur}0"
            if c in live:
                return c
            return by_name.get((name or "").replace(" ", ""))
        out: dict = {"low_pbr": {}, "valueup": {}, "errors": []}
        try:
            for x in krx.low_pbr():
                c = code_of(x["isur_cd"], x["name"])
                if c:
                    out["low_pbr"][c] = x
            out["low_pbr_published"] = max((x["published"] for x in out["low_pbr"].values()), default=None)
        except ProviderError as e:
            out["errors"].append(f"저PBR 명단: {e}")
        try:
            for x in krx.valueup_disclosures():
                c = code_of(x["isur_cd"], x["name"])
                if not c:
                    continue
                v = out["valueup"].setdefault(c, {"count": 0, "plans": 0, "last_plan": None, "last": None, "pbr_plan": False,
                                                 "progress": 0, "tags": x["tags"], "items": []})
                v["count"] += 1
                v["items"].append({k: x[k] for k in ("time", "title", "url", "kind")})
                if x["kind"] == "plan":
                    v["plans"] += 1
                    v["last_plan"] = max(v["last_plan"] or "", x["time"])
                if x["kind"] == "progress":
                    v["progress"] += 1
                v["pbr_plan"] = v["pbr_plan"] or "PBR 개선" in x["title"]
                v["last"] = max(v["last"] or "", x["time"])
        except ProviderError as e:
            out["errors"].append(f"밸류업 공시: {e}")
        out["fetched_at"] = utcnow().isoformat()
        return out
    return cached("valueup:kind", 6 * 3600, load)


# ------------------------------------------------------------------ analysis
def snapshot() -> dict:
    """Metrics for every listed company with DART data + the market distributions the score ranks against."""
    def load():
        live = listed()
        corps = corp_map()
        cc_of = {s: c["corp_code"] for s, c in corps.items() if s in live}
        grouped = _group(_rows(list(cc_of.values()), ("main", "full", "div")))
        rows = []
        for code, cc in cc_of.items():
            g = grouped.get(cc) or {}
            if not g.get("main"):
                continue
            lv = live[code]
            fin = is_financial(code, lv.get("name"))
            div = g.get("div") or {}
            m = kf.metrics(g["main"], g.get("full"), lv.get("company_cap"), div[max(div)] if div else None, lv.get("close"), fin)
            m.update(code=code, name=lv.get("name"), market=lv.get("market"), close=lv.get("close"), corp_code=cc)
            rows.append(m)
        dist = kf.distributions(rows)
        return {"rows": rows, "dist": dist, "built_at": utcnow().isoformat(),
                "periods": sorted({f"{r['period']['year']}-{r['period']['reprt']}" for r in rows if r.get("available")})}
    return cached("dart:snapshot", 3600, load)


def company(code: str, refresh: bool = True) -> dict:
    """Everything the stock page shows for one Korean company (`refresh` first fills missing full statements)."""
    if refresh and enabled() and corp_map().get(code):
        try:
            refresh_company(code)
        except ProviderError as e:
            log.info("DART refresh %s: %s", code, e)

    def load():
        corp = corp_map().get(code)
        if not corp:
            return {"available": False, "message": "DART에 등록된 상장사 코드를 찾지 못했습니다 (Data unavailable)"
                    if enabled() else "DART_API_KEY가 설정되지 않았습니다 (.env에 추가 후 백엔드 재시작)"}
        cc = corp["corp_code"]
        g = _group(_rows([cc], ("main", "full", "div"))).get(cc, {})
        main, full, div = g.get("main") or {}, g.get("full") or {}, g.get("div") or {}
        if not main and full:
            main = full
        if not main:
            return {"available": False, "message": "DART 재무 데이터가 아직 없습니다 (Data unavailable)"}
        lv = listed().get(code) or {}
        fin = is_financial(code, lv.get("name") or corp["corp_name"])
        m = kf.metrics(main, full, lv.get("company_cap"), div[max(div)] if div else None, lv.get("close"), fin)
        snap = snapshot()
        sc = kf.score(m, snap["dist"])
        lists = kind_lists()
        extras = {"low_pbr": lists["low_pbr"].get(code), "valueup": lists["valueup"].get(code)}
        merged = {**main, **{k: v for k, v in full.items() if v}}
        return clean({
            "available": True, "code": code, "corp_code": cc, "name": lv.get("name") or corp["corp_name"], "financial": fin,
            "metrics": m, "score": sc, "summary": kf.summary(m, sc, corp["corp_name"], extras),
            "annual": kf.annuals(merged), "quarters": kf.quarters(main),
            "low_pbr": extras["low_pbr"], "valueup": extras["valueup"],
            "peers": len(snap["rows"]), "dividends": div[max(div)] if div else None, "evidence": evidence(),
            "basis_note": ("지배주주 순이익·지배주주 자본 기준 (전체 재무제표)" if m.get("basis") == "parent"
                           else "연결 당기순이익·자본총계 기준 (비지배지분 포함, 주요계정)"),
            "source": "금융감독원 OpenDART · 시가총액 네이버 증권 · 저PBR/밸류업 한국거래소 KIND",
            "updated_at": utcnow().isoformat()})
    return cached(f"dart:company:{code}", 3600, load)


def scan_fields() -> dict[str, dict]:
    """Per stock code: DART score and the metrics the screener filters on (growth as a fraction, like Yahoo's)."""
    def load():
        snap = snapshot()
        out = {}
        for m in snap["rows"]:
            if not m.get("available"):
                continue
            g = (m.get("rev_growth") or {}).get("pct")
            out[m["code"]] = {"dart_score": kf.score(m, snap["dist"])["score"], "pbr": m.get("pbr"), "roe": m.get("roe"),
                              "pe": m.get("per"), "revenue_growth": g / 100 if g is not None else None,
                              "op_margin": m.get("op_margin"), "div_yield_pct": m.get("div_yield")}
        return out
    return cached("dart:scan_fields", 3600, load)


def evidence() -> dict | None:
    """Summary of the Korean factor backtest (tools/kr_factor_study.py) that set the score's weights."""
    def load():
        try:
            d = json.loads((BACKEND_DIR / "data" / "kr_factor_study.json").read_text())
        except Exception:
            return None
        allm = d["markets"].get("ALL", {})
        return {"years": [d["years"][0], d["years"][-1]], "n_years": len(d["years"]), "built_at": d.get("built_at"),
                "total": allm.get("total"), "kospi": d["markets"].get("KOSPI", {}).get("total"),
                "kosdaq": d["markets"].get("KOSDAQ", {}).get("total"),
                "metrics": {k: v for k, v in allm.items() if k != "total"},
                "method": "매년 5월 1일, 그때까지 공시된 사업보고서만으로 점수를 매기고 다음 1년 수익률(같은 시장 중앙값 대비)과 비교. "
                          "지금 상장된 회사만 포함(상장폐지 종목 제외)이라 실제보다 좋게 나올 수 있고, 현금흐름·배당 지표는 검증에 없습니다."}
    return cached("dart:evidence", 86400, load)


def score_for(symbol: str) -> dict | None:
    """KR fundamental score for the scoring blend (stored data only; never waits on DART)."""
    if not symbol.endswith((".KS", ".KQ")):
        return None
    try:
        d = company(symbol.split(".")[0], refresh=False)
    except Exception as e:
        log.debug("dart score %s: %s", symbol, e)
        return None
    return d["score"] if d.get("available") and d["score"].get("score") is not None else None


def warm_companies(codes: list[str], workers: int = 4) -> int:
    """Background: full statements + dividends for a list of companies (a few at a time)."""
    n = 0
    stop = threading.Event()

    def one(c: str) -> bool:
        if stop.is_set():
            return False
        try:
            return refresh_company(c)
        except ProviderError as e:
            log.info("DART warm %s: %s", c, e)
            if "요청 제한" in str(e):
                stop.set()
            return False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ok in ex.map(one, codes):
            n += bool(ok)
    if n:
        cache.delete_prefix("dart:snapshot")
        cache.delete_prefix("dart:scan_fields")
        cache.delete_prefix("valueup:")
    return n


def _missing_full(limit: int) -> list[str]:
    """Companies whose latest main-account period has no full statements stored yet (largest first)."""
    live = listed()
    with SessionLocal() as db:
        rows = db.execute(select(DartFinancial.stock_code, DartFinancial.year, DartFinancial.reprt, DartFinancial.kind)
                          .where(DartFinancial.kind.in_(("main", "full")))).all()
    latest_main: dict[str, tuple] = {}
    have_full: set[tuple] = set()
    for code, y, r, kind in rows:
        if kind == "main":
            if code not in latest_main or (y, REPORTS[r]) > (latest_main[code][0], REPORTS[latest_main[code][1]]):
                latest_main[code] = (y, r)
        else:
            have_full.add((code, y, r))
    todo = [c for c, p in latest_main.items() if (c, *p) not in have_full and c in live]
    return sorted(todo, key=lambda c: -(live[c].get("company_cap") or 0))[:limit]


def daily() -> dict:
    """Scheduled: new filings for the whole market, then full statements for the companies people look at most."""
    if not enabled():
        return {"ok": False}
    res = refresh_market()
    if not res.get("ok"):          # still collecting (or failed): company fills need the market data first
        return res
    from ..models import PortfolioPosition, WatchlistItem
    from . import universe
    want: list[str] = []
    with SessionLocal() as db:
        for model in (PortfolioPosition, WatchlistItem):
            want += [r for (r,) in db.execute(select(model.symbol)).all()]
    try:
        c = universe.constituents()
        want += c.get("KOSPI", []) + c.get("KOSDAQ", [])
    except Exception as e:
        log.info("universe unavailable for DART warm: %s", e)
    codes = [w.split(".")[0] for w in want if w.endswith((".KS", ".KQ"))]
    codes += list(kind_lists()["low_pbr"].keys())
    n = warm_companies(list(dict.fromkeys(codes)))
    # then the rest of the market, largest first, ~800 companies (~2,000 calls) per run until everyone has full statements
    rest = warm_companies(_missing_full(800))
    return {**res, "warmed": n, "market_warmed": rest, "calls_today": dart.calls_today}
