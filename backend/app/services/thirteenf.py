"""What the largest hedge funds and banks hold, from their quarterly 13F-HR filings (SEC EDGAR).

* Managers: a fixed list of well-known hedge funds / investment companies and banks (CIKs checked on
  EDGAR 2026-10-10). Market makers (Susquehanna, Jane Street) are left out: their 13F is mostly hedges.
* Each quarter's information table is summed per CUSIP over long share positions only - option rows
  (putCall) and principal amounts (bonds, converts) are dropped. Amendments are applied: a restatement
  replaces the table, "new holdings" add to it (some banks file a partial original and restate later).
* Ranking: the top-N managers by reported equity value; for every stock, the average of its weight in
  each manager's portfolio (0 when not held, so every manager counts equally), or the average share of
  the portfolio added during the latest quarter (shares bought × quarter-end price).
* 13F shows positions 45 days after the quarter ends, and never says why something was bought.
"""
from __future__ import annotations

import calendar
import datetime as dt
import io
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

import httpx
from sqlalchemy import delete, select

from ..cache import cache, cached
from ..config import get_settings
from ..db import SessionLocal
from ..models import CusipMap, Filing13F, Holding13F
from .store import clean, utcnow

log = logging.getLogger(__name__)

# (CIK, name, kind, representative, Wikipedia page of the representative)
MANAGERS: list[tuple[int, str, str, str, str]] = [
    (1423053, "Citadel Advisors", "hedge", "Ken Griffin", "Ken_Griffin"),
    (1350694, "Bridgewater Associates", "hedge", "Ray Dalio (창업자)", "Ray_Dalio"),
    (1037389, "Renaissance Technologies", "hedge", "Jim Simons (창업자)", "Jim_Simons"),
    (1179392, "Two Sigma", "hedge", "John Overdeck · David Siegel", "John_Overdeck"),
    (1009207, "D. E. Shaw", "hedge", "David E. Shaw", "David_E._Shaw"),
    (1273087, "Millennium Management", "hedge", "Israel Englander", "Israel_Englander"),
    (1603466, "Point72", "hedge", "Steve Cohen", "Steve_Cohen_(businessman)"),
    (1167557, "AQR Capital", "hedge", "Cliff Asness", "Clifford_Asness"),
    (1336528, "Pershing Square", "hedge", "Bill Ackman", "Bill_Ackman"),
    (1791786, "Elliott Investment Mgmt", "hedge", "Paul Singer", "Paul_Singer_(businessman)"),
    (1167483, "Tiger Global", "hedge", "Chase Coleman", "Chase_Coleman_III"),
    (1135730, "Coatue", "hedge", "Philippe Laffont", "Philippe_Laffont"),
    (1103804, "Viking Global", "hedge", "Andreas Halvorsen", "Andreas_Halvorsen"),
    (1061165, "Lone Pine Capital", "hedge", "Stephen Mandel (창업자)", "Stephen_Mandel"),
    (1040273, "Third Point", "hedge", "Dan Loeb", "Daniel_S._Loeb"),
    (1061768, "Baupost Group", "hedge", "Seth Klarman", "Seth_Klarman"),
    (1029160, "Soros Fund Management", "hedge", "George Soros (창업자)", "George_Soros"),
    (1536411, "Duquesne Family Office", "hedge", "Stanley Druckenmiller", "Stanley_Druckenmiller"),
    (1656456, "Appaloosa", "hedge", "David Tepper", "David_Tepper"),
    (1218710, "Balyasny", "hedge", "Dmitry Balyasny", "Dmitry_Balyasny"),
    (1318757, "Marshall Wace", "hedge", "Paul Marshall", "Paul_Marshall_(investor)"),
    (1067983, "Berkshire Hathaway", "hedge", "Warren Buffett", "Warren_Buffett"),
    (19617, "JPMorgan Chase", "bank", "Jamie Dimon", "Jamie_Dimon"),
    (70858, "Bank of America", "bank", "Brian Moynihan", "Brian_Moynihan"),
    (895421, "Morgan Stanley", "bank", "Ted Pick", "Ted_Pick"),
    (886982, "Goldman Sachs", "bank", "David Solomon", "David_M._Solomon"),
    (72971, "Wells Fargo", "bank", "Charlie Scharf", "Charles_Scharf"),
    (831001, "Citigroup", "bank", "Jane Fraser", "Jane_Fraser_(executive)"),
    (1610520, "UBS", "bank", "Sergio Ermotti", "Sergio_Ermotti"),
    (312069, "Barclays", "bank", "C. S. Venkatakrishnan", "C._S._Venkatakrishnan"),
    (1000275, "Royal Bank of Canada", "bank", "Dave McKay", "Dave_McKay"),
    (948046, "Deutsche Bank", "bank", "Christian Sewing", "Christian_Sewing"),
    (73124, "Northern Trust", "bank", "Michael O'Grady", "Michael_O'Grady"),
    (1390777, "BNY", "bank", "Robin Vince", "Robin_Vince"),
]
BY_CIK = {m[0]: m for m in MANAGERS}
QUARTERS = 4  # latest + 3 earlier: enough to tell when a position first appeared within a year
KIND_KO = {"hedge": "헤지펀드·투자회사", "bank": "은행"}

_NOT_STOCK = re.compile(r"\b(ETF|ETN|TRUST|TR UNIT|UNIT SER|FUND|INDEX|SPDR|ISHARES|VANGUARD|INVESCO QQQ|PROSHARES|DIREXION)\b", re.I)
_state = {"state": "idle", "done": 0, "total": 0, "message": None, "started_at": None, "finished_at": None}
_lock = threading.Lock()


def _client() -> httpx.Client:
    # SEC asks for a descriptive User-Agent with a contact address (SEC_USER_AGENT in .env).
    return httpx.Client(headers={"User-Agent": get_settings().sec_user_agent, "Accept-Encoding": "gzip, deflate"},
                        timeout=90, follow_redirects=True)


def _get(c: httpx.Client, url: str) -> httpx.Response:
    for attempt in range(3):
        r = c.get(url)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
        time.sleep(0.15)  # stay well under SEC's 10 requests / second
        return r
    r.raise_for_status()
    return r


def _filings(c: httpx.Client, cik: int, n: int = QUARTERS) -> list[dict]:
    """The latest `n` report periods: the original 13F-HR plus its amendments (13F-HR/A), oldest first."""
    j = _get(c, f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    out: dict[str, dict] = {}

    def scan(f: dict) -> None:
        for i, form in enumerate(f["form"]):
            period = f["reportDate"][i]
            if form not in ("13F-HR", "13F-HR/A") or not period:
                continue
            p = out.setdefault(period, {"period": period, "name": j.get("name"), "docs": []})
            if not any(d["accession"] == f["accessionNumber"][i] for d in p["docs"]):
                p["docs"].append({"accession": f["accessionNumber"][i], "form": form, "filed": f["filingDate"][i]})
    scan(j["filings"]["recent"])
    # Banks file thousands of prospectuses, so older 13Fs can sit in the paged history files.
    for extra in j["filings"].get("files", []):
        if sum(any(d["form"] == "13F-HR" for d in p["docs"]) for p in out.values()) >= n:
            break
        scan(_get(c, f"https://data.sec.gov/submissions/{extra['name']}").json())
    res = []
    for period in sorted(out, reverse=True):
        p = out[period]
        if not any(d["form"] == "13F-HR" for d in p["docs"]):
            continue
        p["docs"].sort(key=lambda d: (d["form"] != "13F-HR", d["filed"], d["accession"]))  # original, then amendments
        p["filed"] = p["docs"][0]["filed"]
        p["key"] = p["docs"][-1]["accession"]  # changes when a new amendment arrives
        res.append(p)
        if len(res) >= n:
            break
    return res


def _amendment_type(c: httpx.Client, cik: int, accession: str) -> str:
    """'RESTATEMENT' (replaces the table) or 'NEW HOLDINGS' (adds to it), from the cover page."""
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    try:
        xml = _get(c, f"{base}/primary_doc.xml").text
    except Exception:
        return "RESTATEMENT"
    m = re.search(r"<(?:\w+:)?amendmentType>\s*([^<]+?)\s*<", xml)
    return (m.group(1).upper() if m else "RESTATEMENT")


def _merged_table(c: httpx.Client, cik: int, docs: list[dict]) -> dict[str, dict]:
    """Original table with amendments applied in filing order."""
    rows: dict[str, dict] = {}
    for d in docs:
        try:
            part = fix_units(parse_info_table(_info_table(c, cik, d["accession"])))
        except ValueError:  # an amendment without a table (cover-page fix)
            continue
        overlap = sum(v["value"] for k, v in part.items() if k in rows) / (sum(v["value"] for v in part.values()) or 1)
        # Some filers label a full re-filing "NEW HOLDINGS"; mostly-known positions mean it is really a restatement.
        if d["form"] == "13F-HR" or overlap > 0.5 or _amendment_type(c, cik, d["accession"]).startswith("RESTATE"):
            rows = part
        else:
            for k, v in part.items():
                r = rows.setdefault(k, {**v, "value": 0.0, "shares": 0.0})
                r["value"] += v["value"]
                r["shares"] += v["shares"]
    return rows


def _info_table(c: httpx.Client, cik: int, accession: str) -> bytes:
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    items = _get(c, f"{base}/index.json").json()["directory"]["item"]
    xmls = [x["name"] for x in items if x["name"].lower().endswith(".xml") and x["name"].lower() != "primary_doc.xml"]
    if not xmls:
        raise ValueError(f"no information table in {accession}")
    return _get(c, f"{base}/{xmls[0]}").content


def parse_info_table(xml: bytes) -> dict[str, dict]:
    """{cusip: {issuer, title, value, shares}} over long share positions (no options, no principal amounts)."""
    out: dict[str, dict] = {}
    for _, el in ET.iterparse(io.BytesIO(xml)):
        if not el.tag.endswith("infoTable"):
            continue
        f = {ch.tag.split("}")[-1]: ch for ch in el.iter()}
        put_call = f.get("putCall")
        kind = f.get("sshPrnamtType")
        if (put_call is not None and (put_call.text or "").strip()) or (kind is not None and (kind.text or "").strip().upper() != "SH"):
            el.clear()
            continue
        cusip = (f["cusip"].text or "").strip().upper()
        try:
            value = float((f["value"].text or "0").replace(",", ""))
            shares = float((f["sshPrnamt"].text or "0").replace(",", ""))
        except (KeyError, ValueError):
            el.clear()
            continue
        d = out.setdefault(cusip, {"issuer": (f["nameOfIssuer"].text or "").strip()[:200],
                                   "title": (f["titleOfClass"].text or "").strip()[:100] if "titleOfClass" in f else None,
                                   "value": 0.0, "shares": 0.0})
        d["value"] += value
        d["shares"] += shares
        el.clear()
    return out


def fix_units(rows: dict[str, dict]) -> dict[str, dict]:
    """Values are whole dollars since 2023, but some filers still report thousands. If the largest positions
    imply share prices under $1.50, scale by 1,000."""
    top = sorted((r for r in rows.values() if r["shares"]), key=lambda r: -r["value"])[:10]
    if top:
        implied = sorted(r["value"] / r["shares"] for r in top)[len(top) // 2]
        if implied < 1.5:
            for r in rows.values():
                r["value"] *= 1000
    return rows


def status() -> dict:
    with SessionLocal() as db:
        n = len(db.execute(select(Filing13F.id)).all())
    return {**_state, "filings": n}


def ensure_filings() -> None:
    """Background: download the latest QUARTERS filings of every manager that are not stored yet (idempotent)."""
    with _lock:
        if _state["state"] == "running":
            return
        _state.update(state="running", done=0, total=len(MANAGERS), started_at=utcnow().isoformat(), message="SEC EDGAR에서 13F 내려받는 중")
    added = 0
    try:
        with _client() as c, SessionLocal() as db:
            have = {(f, p): (i, acc) for i, f, p, acc in db.execute(select(Filing13F.id, Filing13F.cik, Filing13F.period, Filing13F.accession)).all()}
            for i, (cik, name, *_rest) in enumerate(MANAGERS):
                _state.update(done=i, message=f"{name} 13F 확인 중")
                try:
                    for f in _filings(c, cik):
                        period = dt.date.fromisoformat(f["period"])
                        old = have.get((cik, period))
                        if old and old[1] == f["key"]:
                            continue
                        rows = _merged_table(c, cik, f["docs"])
                        if old:  # an amendment arrived since we stored this quarter
                            db.execute(delete(Holding13F).where(Holding13F.filing_id == old[0]))
                            db.execute(delete(Filing13F).where(Filing13F.id == old[0]))
                        fil = Filing13F(cik=cik, manager=f["name"] or name, period=period, filed=dt.date.fromisoformat(f["filed"]),
                                        accession=f["key"], equity_value=sum(r["value"] for r in rows.values()), positions=len(rows))
                        db.add(fil)
                        db.flush()
                        db.add_all([Holding13F(filing_id=fil.id, cusip=k, **v) for k, v in rows.items()])
                        db.commit()
                        added += 1
                except Exception as e:  # one manager failing must not stop the rest
                    db.rollback()
                    log.warning("13F %s (%s) failed: %s", name, cik, e)
            # keep only QUARTERS periods per manager
            for cik, *_ in MANAGERS:
                periods = sorted({p for (p,) in db.execute(select(Filing13F.period).where(Filing13F.cik == cik)).all()}, reverse=True)
                for old in periods[QUARTERS:]:
                    ids = [i for (i,) in db.execute(select(Filing13F.id).where(Filing13F.cik == cik, Filing13F.period == old)).all()]
                    db.execute(delete(Holding13F).where(Holding13F.filing_id.in_(ids)))
                    db.execute(delete(Filing13F).where(Filing13F.id.in_(ids)))
            db.commit()
        _state.update(state="done", done=len(MANAGERS), finished_at=utcnow().isoformat(), message=f"새 공시 {added}건 저장")
        if added:
            cache.delete_prefix("13f:")
    except Exception as e:
        log.exception("13F ingest failed")
        _state.update(state="error", finished_at=utcnow().isoformat(), message=str(e)[:200])


def has_data() -> bool:
    with SessionLocal() as db:
        return db.execute(select(Filing13F.id).limit(1)).first() is not None


def start_ingest() -> None:
    """Run ensure_filings in a thread unless it is already running."""
    if _state["state"] != "running":
        threading.Thread(target=ensure_filings, name="13f-ingest", daemon=True).start()


def map_cusips(cusips: list[str]) -> dict[str, dict]:
    """CUSIP -> {ticker, name, security_type} via OpenFIGI (stored; unknowns are stored too so they aren't retried daily)."""
    with SessionLocal() as db:
        known = {m.cusip: m for m in db.execute(select(CusipMap).where(CusipMap.cusip.in_(cusips))).scalars()}
        stale = utcnow() - dt.timedelta(days=7)
        # unknown, or a miss older than a week (OpenFIGI coverage grows; foreign CINS codes need their own id type)
        todo = [c for c in cusips if c not in known or (known[c].ticker is None and known[c].fetched_at < stale)]
        headers = {"Content-Type": "application/json"}
        if get_settings().openfigi_api_key:
            headers["X-OPENFIGI-APIKEY"] = get_settings().openfigi_api_key
        step = 100 if "X-OPENFIGI-APIKEY" in headers else 10
        with httpx.Client(timeout=30, headers=headers) as c:
            for i in range(0, len(todo), step):
                chunk = todo[i:i + step]
                try:
                    jobs = [{"idType": "ID_CINS" if x[:1].isalpha() else "ID_CUSIP", "idValue": x} for x in chunk]
                    r = c.post("https://api.openfigi.com/v3/mapping", json=jobs)
                    if r.status_code == 429:
                        time.sleep(7)
                        r = c.post("https://api.openfigi.com/v3/mapping", json=jobs)
                    r.raise_for_status()
                except Exception as e:
                    log.info("OpenFIGI failed: %s", e)
                    break
                for cus, res in zip(chunk, r.json()):
                    data = res.get("data") or []
                    us = next((d for d in data if d.get("exchCode") == "US"), data[0] if data else None)
                    tk = re.sub(r"[^A-Z0-9.\-]", "", ((us or {}).get("ticker") or "").upper().replace("/", "-")) or None
                    m = CusipMap(cusip=cus, ticker=tk, name=(us or {}).get("name"),
                                 security_type=(us or {}).get("securityType2") or (us or {}).get("securityType"))
                    db.merge(m)
                    known[cus] = m
                db.commit()
                time.sleep(0.3 if step == 100 else 2.5)  # OpenFIGI: 25 requests / minute without a key
        return {k: {"ticker": v.ticker, "name": v.name, "security_type": v.security_type} for k, v in known.items()}


def _is_stock(issuer: str, title: str | None, sec_type: str | None) -> bool:
    if sec_type:
        st = sec_type.lower()
        if any(w in st for w in ("etp", "etf", "fund", "unit", "closed-end", "mutual")):
            return False
        if "common stock" in st or "depositary" in st or "adr" in st or "reit" in st or st in ("equity", "ordinary shares"):
            return True
    return not _NOT_STOCK.search(f"{issuer} {title or ''}")


def _quarter_ko(d: dt.date) -> str:
    return f"{d.year}년 {(d.month - 1) // 3 + 1}분기"


def wiki_photo(page: str) -> str | None:
    """Thumbnail of the representative from Wikipedia (cached a week); None if the page has no image."""
    def load():
        try:
            r = httpx.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{page}", timeout=10,
                          headers={"User-Agent": "MarketLens/0.1 (personal research app)"})
            if r.status_code != 200:
                return ""
            return ((r.json().get("thumbnail") or {}).get("source")) or ""
        except Exception:
            return ""
    return cached(f"wiki:{page}", 7 * 86400, load) or None


def top_stocks(group: str = "all", n_managers: int = 20, top: int = 10, metric: str = "weight") -> dict:
    """The ranking for the institutions page; see the module docstring for the method."""
    with SessionLocal() as db:
        fils = db.execute(select(Filing13F)).scalars().all()
        if not fils:
            return {"ready": False, "status": status(), "items": [], "managers": []}
        by_mgr: dict[int, list[Filing13F]] = defaultdict(list)
        for f in fils:
            if f.cik in BY_CIK and (group == "all" or BY_CIK[f.cik][2] == group):
                by_mgr[f.cik].append(f)
        for v in by_mgr.values():
            v.sort(key=lambda f: f.period, reverse=True)
        # the latest quarter most managers have filed for
        counts: dict[dt.date, int] = defaultdict(int)
        for v in by_mgr.values():
            counts[v[0].period] += 1
        if not counts:
            return {"ready": False, "status": status(), "items": [], "managers": []}
        period = max(counts, key=lambda p: (counts[p], p))
        current = [v for v in by_mgr.values() if v[0].period == period and v[0].equity_value > 0]
        current.sort(key=lambda v: -v[0].equity_value)
        chosen = current[:n_managers]
        ids = [f.id for v in chosen for f in v]
        rows = db.execute(select(Holding13F.filing_id, Holding13F.cusip, Holding13F.issuer, Holding13F.title, Holding13F.value,
                                 Holding13F.shares).where(Holding13F.filing_id.in_(ids))).all()
    hold: dict[int, dict[str, tuple]] = defaultdict(dict)
    for fid, cus, issuer, title, value, shares in rows:
        hold[fid][cus] = (issuer, title, value, shares)

    n = len(chosen)
    splits = _split_factors(chosen, hold)
    agg: dict[str, dict] = {}
    for v in chosen:
        now, prev = v[0], (v[1] if len(v) > 1 else None)
        h_now, h_prev = hold[now.id], hold[prev.id] if prev else {}
        for cus, (issuer, title, value, shares) in h_now.items():
            px = value / shares if shares else 0.0
            sh_prev = h_prev.get(cus, (None, None, 0.0, 0.0))[3] * splits.get(cus, 1.0) if prev else None
            a = agg.setdefault(cus, {"cusip": cus, "issuer": issuer, "title": title, "weight_sum": 0.0, "added_sum": 0.0,
                                     "holders": 0, "value": 0.0, "new": 0, "added": 0, "reduced": 0})
            a["weight_sum"] += value / now.equity_value
            a["holders"] += 1
            a["value"] += value
            if sh_prev is not None:
                a["added_sum"] += (shares - sh_prev) * px / now.equity_value
                if sh_prev == 0:
                    a["new"] += 1
                elif shares > sh_prev * 1.01:
                    a["added"] += 1
                elif shares < sh_prev * 0.99:
                    a["reduced"] += 1
        if prev:  # sold out completely during the quarter: negative buying
            for cus, (issuer, title, value, shares) in h_prev.items():
                if cus not in h_now and shares:
                    a = agg.setdefault(cus, {"cusip": cus, "issuer": issuer, "title": title, "weight_sum": 0.0, "added_sum": 0.0,
                                             "holders": 0, "value": 0.0, "new": 0, "added": 0, "reduced": 0})
                    a["added_sum"] -= value / now.equity_value  # previous quarter-end value as the best available price
                    a["reduced"] += 1
    key = "added_sum" if metric == "bought" else "weight_sum"
    pre = sorted(agg.values(), key=lambda a: -a[key])[:top * 4]
    names = map_cusips([a["cusip"] for a in pre])
    items = []
    for a in pre:
        m = names.get(a["cusip"]) or {}
        if not _is_stock(a["issuer"], a["title"], m.get("security_type")):
            continue
        items.append({"cusip": a["cusip"], "ticker": m.get("ticker"), "name": m.get("name") or a["issuer"], "issuer": a["issuer"],
                      "avg_weight_pct": a["weight_sum"] / n * 100, "avg_added_pct": a["added_sum"] / n * 100,
                      "holders": a["holders"], "value_usd": a["value"], "new": a["new"], "added": a["added"], "reduced": a["reduced"]})
        if len(items) >= top:
            break
    for i, it in enumerate(items, 1):
        it["rank"] = i
        it["detail"] = _detail(it["cusip"], chosen, hold, splits.get(it["cusip"], 1.0))
        firsts = [d["first_seen"] for d in it["detail"] if d.get("first_seen")]
        it["typical_since"] = sorted(firsts)[len(firsts) // 2] if firsts else None
    managers = [{"cik": v[0].cik, "name": BY_CIK[v[0].cik][1], "kind": BY_CIK[v[0].cik][2], "kind_ko": KIND_KO[BY_CIK[v[0].cik][2]],
                 "rep": BY_CIK[v[0].cik][3], "photo": wiki_photo(BY_CIK[v[0].cik][4]), "equity_value_usd": v[0].equity_value,
                 "positions": v[0].positions, "filed": v[0].filed.isoformat()} for v in chosen]
    return clean({"ready": True, "period": period.isoformat(), "period_ko": _quarter_ko(period), "group": group, "metric": metric,
                  "n_managers": n, "items": items, "managers": managers, "status": status(),
                  "next_deadline": _next_deadline(period).isoformat(),
                  "method": __doc__, "note": "13F 공시에는 매수 사유가 적혀 있지 않습니다. 사유는 펀드가 투자자 서한·인터뷰로 직접 밝힌 경우에만 알 수 있습니다.",
                  "updated_at": utcnow().isoformat()})


def _split_factors(chosen: list[list[Filing13F]], hold: dict[int, dict[str, tuple]]) -> dict[str, float]:
    """Stock splits between the last two quarters: when most holders' share counts moved by the same whole
    ratio (2:1, 10:1, 1:4 ...), previous shares are scaled so a split never looks like buying or selling."""
    ratios: dict[str, list[float]] = defaultdict(list)
    for v in chosen:
        if len(v) < 2:
            continue
        now, prev = hold[v[0].id], hold[v[1].id]
        for cus, t in now.items():
            p = prev.get(cus)
            if p and p[3] and t[3]:
                ratios[cus].append(t[3] / p[3])
    out = {}
    for cus, r in ratios.items():
        if len(r) < 3:
            continue
        med = sorted(r)[len(r) // 2]
        for f in (med, 1 / med):
            k = round(f)
            if k >= 2 and abs(f - k) / k < 0.03:
                out[cus] = float(k) if f == med else 1 / k
    return out


def _next_deadline(period: dt.date) -> dt.date:
    """13F for the next quarter is due 45 days after that quarter ends."""
    m = period.month + 3
    y, m = (period.year + 1, m - 12) if m > 12 else (period.year, m)
    return dt.date(y, m, calendar.monthrange(y, m)[1]) + dt.timedelta(days=45)


def _detail(cusip: str, chosen: list[list[Filing13F]], hold: dict[int, dict[str, tuple]], split: float = 1.0) -> list[dict]:
    """Per manager: weight, rank in their book, latest-quarter change and when the position first shows up."""
    out = []
    for v in chosen:
        now = v[0]
        cur = hold[now.id].get(cusip)
        if not cur:
            continue
        book = sorted(hold[now.id].values(), key=lambda t: -t[2])
        rank = next(i for i, t in enumerate(book, 1) if t is cur)
        prev = hold[v[1].id].get(cusip) if len(v) > 1 else None
        if len(v) < 2:
            change, change_pct = None, None
        elif not prev or not prev[3]:
            change, change_pct = "new", None
        else:
            change_pct = (cur[3] / (prev[3] * split) - 1) * 100
            change = "added" if change_pct > 1 else "reduced" if change_pct < -1 else "same"
        first = now.period
        for f in v[1:]:  # walk back while the position is still there
            if cusip in hold[f.id]:
                first = f.period
            else:
                break
        held_all = first == v[-1].period and len(v) >= QUARTERS
        m = BY_CIK[now.cik]
        out.append({"cik": now.cik, "manager": m[1], "kind": m[2], "rep": m[3], "photo": wiki_photo(m[4]),
                    "weight_pct": cur[2] / now.equity_value * 100, "rank_in_book": rank, "value_usd": cur[2], "shares": cur[3],
                    "change": change, "change_pct": change_pct,
                    "first_seen": first.isoformat(), "first_seen_ko": f"{_quarter_ko(first)} 이전부터" if held_all else f"{_quarter_ko(first)} 편입",
                    "filed": now.filed.isoformat()})
    return sorted(out, key=lambda d: -d["weight_pct"])
