"""Probe every external data source the platform depends on and record the
*shape* of the live responses (not the full data) to var/probe_results.json.

Run on a machine with internet access:
    uv run --with "yfinance>=1.0" --with httpx --with lxml --with pandas python scripts/probe_data_sources.py

Nothing here is used by the app at runtime; it exists so provider parsers are
written against the real, current response formats.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

RESULTS: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "checks": {}}
UA_BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
SEC_UA = os.environ.get("SEC_USER_AGENT", "StockTerminal research-probe contact@example.com")


def record(name: str, fn):
    t0 = time.time()
    try:
        out = fn()
        RESULTS["checks"][name] = {"ok": True, "secs": round(time.time() - t0, 2), "result": out}
        print(f"[OK]   {name} ({time.time()-t0:.1f}s)")
    except Exception as exc:  # noqa: BLE001
        RESULTS["checks"][name] = {
            "ok": False,
            "secs": round(time.time() - t0, 2),
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc()[-1500:],
        }
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")


def df_shape(df):
    if df is None:
        return None
    try:
        idx = df.index
        return {
            "rows": int(len(df)),
            "columns": [str(c) for c in df.columns],
            "index_name": str(idx.name),
            "index_dtype": str(idx.dtype),
            "first_index": str(idx[0]) if len(idx) else None,
            "last_index": str(idx[-1]) if len(idx) else None,
            "last_row": {str(k): (None if v != v else (float(v) if isinstance(v, (int, float)) else str(v)))
                         for k, v in (df.iloc[-1].to_dict().items() if len(df) else {})},
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def main() -> None:
    import httpx
    import pandas as pd
    import yfinance as yf

    RESULTS["versions"] = {"python": sys.version, "yfinance": getattr(yf, "__version__", "?"),
                           "pandas": pd.__version__, "httpx": httpx.__version__}

    # ---------------- Yahoo via yfinance ----------------
    def y_hist():
        t = yf.Ticker("NVDA")
        h = t.history(period="5d", interval="1d", auto_adjust=False, actions=True)
        meta = t.history_metadata
        return {"df": df_shape(h), "metadata_keys": sorted(list(meta.keys())) if meta else None,
                "metadata_sample": {k: (str(v)[:200]) for k, v in (meta or {}).items()
                                    if k in ("currency", "exchangeName", "fullExchangeName", "instrumentType",
                                             "regularMarketPrice", "regularMarketTime", "chartPreviousClose",
                                             "previousClose", "regularMarketDayHigh", "regularMarketDayLow",
                                             "exchangeTimezoneName", "currentTradingPeriod", "longName",
                                             "shortName", "fiftyTwoWeekHigh", "regularMarketVolume",
                                             "hasPrePostMarketData", "dataGranularity", "range")}}
    record("yahoo_history_NVDA", y_hist)

    def y_intraday():
        t = yf.Ticker("^GSPC")
        h = t.history(period="1d", interval="5m")
        return {"df": df_shape(h), "meta_prev_close": t.history_metadata.get("chartPreviousClose"),
                "meta_tp": str(t.history_metadata.get("currentTradingPeriod"))[:400]}
    record("yahoo_intraday_GSPC", y_intraday)

    def y_kr():
        out = {}
        for s in ("005930.KS", "000660.KS", "247540.KQ", "^KS11", "^KQ11", "KRW=X", "DX-Y.NYB", "^TNX",
                  "GC=F", "CL=F", "BZ=F", "HG=F", "^VIX", "^VIX3M", "^N225", "^HSI", "^STOXX50E", "BTC-USD",
                  "^MOVE", "^NDX", "^RUT", "^DJI", "^IRX", "^FVX", "^TYX"):
            try:
                h = yf.Ticker(s).history(period="5d", interval="1d")
                out[s] = {"rows": len(h), "last_close": float(h["Close"].iloc[-1]) if len(h) else None,
                          "last_date": str(h.index[-1]) if len(h) else None}
            except Exception as exc:  # noqa: BLE001
                out[s] = {"error": str(exc)[:200]}
        return out
    record("yahoo_symbols_misc", y_kr)

    def y_download():
        d = yf.download(["AAPL", "MSFT", "005930.KS"], period="1mo", interval="1d", group_by="ticker",
                        auto_adjust=False, progress=False, threads=True)
        return {"columns_sample": [str(c) for c in list(d.columns)[:14]], "rows": len(d),
                "col_nlevels": d.columns.nlevels, "index_tz": str(getattr(d.index, "tz", None))}
    record("yahoo_download_batch", y_download)

    def y_info():
        info = yf.Ticker("NVDA").info
        keys = ["trailingPE", "forwardPE", "trailingPegRatio", "pegRatio", "enterpriseToEbitda",
                "priceToSalesTrailing12Months", "revenueGrowth", "earningsGrowth", "grossMargins",
                "operatingMargins", "returnOnEquity", "debtToEquity", "freeCashflow", "marketCap", "sector",
                "industry", "longName", "currency", "exchange", "quoteType", "marketState",
                "exchangeDataDelayedBy", "earningsTimestamp", "earningsTimestampStart", "beta",
                "totalCash", "totalDebt", "ebitda", "country", "fullExchangeName", "regularMarketPrice",
                "regularMarketChangePercent", "regularMarketTime", "preMarketPrice", "postMarketPrice"]
        return {"n_keys": len(info), "selected": {k: info.get(k) for k in keys}}
    record("yahoo_info_NVDA", y_info)

    def y_info_kr():
        info = yf.Ticker("005930.KS").info
        keys = ["longName", "shortName", "trailingPE", "forwardPE", "marketCap", "sector", "industry",
                "currency", "exchange", "exchangeDataDelayedBy", "marketState", "returnOnEquity"]
        return {"n_keys": len(info), "selected": {k: info.get(k) for k in keys}}
    record("yahoo_info_005930", y_info_kr)

    def y_news():
        n = yf.Ticker("NVDA").news
        first = n[0] if n else None
        return {"count": len(n or []), "first_keys": list(first.keys()) if isinstance(first, dict) else None,
                "first": json.loads(json.dumps(first, default=str))[:1] if False else
                json.loads(json.dumps(first, default=str)) if first else None}
    record("yahoo_news_NVDA", y_news)

    def y_cal():
        t = yf.Ticker("NVDA")
        cal = t.calendar
        out = {"calendar": json.loads(json.dumps(cal, default=str)) if cal is not None else None}
        try:
            ed = t.get_earnings_dates(limit=8)
            out["earnings_dates"] = df_shape(ed)
            out["earnings_dates_head"] = ed.head(8).reset_index().astype(str).to_dict("records") if ed is not None else None
        except Exception as exc:  # noqa: BLE001
            out["earnings_dates_error"] = str(exc)[:300]
        try:
            ud = t.upgrades_downgrades
            out["upgrades_downgrades"] = df_shape(ud)
            out["ud_head"] = ud.head(3).reset_index().astype(str).to_dict("records") if ud is not None else None
        except Exception as exc:  # noqa: BLE001
            out["ud_error"] = str(exc)[:300]
        try:
            out["income_stmt"] = df_shape(t.income_stmt)
            out["income_rows"] = [str(i) for i in t.income_stmt.index][:60]
            out["balance_rows"] = [str(i) for i in t.balance_sheet.index][:80]
            out["cashflow_rows"] = [str(i) for i in t.cashflow.index][:60]
        except Exception as exc:  # noqa: BLE001
            out["fin_error"] = str(exc)[:300]
        try:
            out["earnings_estimate"] = df_shape(t.earnings_estimate)
            out["revenue_estimate"] = df_shape(t.revenue_estimate)
            out["earnings_history"] = df_shape(t.earnings_history)
        except Exception as exc:  # noqa: BLE001
            out["est_error"] = str(exc)[:300]
        return out
    record("yahoo_calendar_earnings_NVDA", y_cal)

    def y_search():
        out = {"has_Search": hasattr(yf, "Search"), "has_screen": hasattr(yf, "screen"),
               "has_Lookup": hasattr(yf, "Lookup"), "has_Market": hasattr(yf, "Market")}
        if hasattr(yf, "Search"):
            s = yf.Search("samsung electronics", max_results=5)
            out["quotes"] = [{k: q.get(k) for k in ("symbol", "shortname", "longname", "exchange", "quoteType")}
                             for q in (s.quotes or [])][:5]
        return out
    record("yahoo_search", y_search)

    client = httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA_BROWSER})

    # ---------------- FRED ----------------
    def fred_csv():
        r = client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "DGS10", "cosd": "2026-01-01"})
        lines = r.text.splitlines()
        return {"status": r.status_code, "header": lines[0] if lines else None, "first": lines[1:3], "last": lines[-2:]}
    record("fred_csv_DGS10", fred_csv)

    def fred_ids():
        ids = ["DFF", "FEDFUNDS", "DFEDTARU", "DFEDTARL", "DGS3MO", "DGS2", "DGS10", "DGS30", "T10Y2Y", "T10Y3M",
               "DFII10", "T10YIE", "T5YIFR", "DTWEXBGS", "DEXKOUS", "M2SL", "WALCL", "RRPONTSYD", "WTREGEN",
               "CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE", "UNRATE", "PAYEMS", "ICSA", "CCSA", "CFNAI",
               "GACDFSA066MSFRBPHI", "GACDINA066MSFRBNY", "INDPRO", "RSAFS", "UMCSENT", "BAMLH0A0HYM2",
               "NFCI", "STLFSI4", "GDPNOW", "A191RL1Q225SBEA", "SAHMREALTIME", "IRSTCB01KRM156N",
               "KORCPIALLMINMEI", "NAPM", "ISM/MAN_PMI", "DCOILWTICO", "DCOILBRENTEU", "VIXCLS"]
        out = {}
        for sid in ids:
            try:
                r = client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid, "cosd": "2025-01-01"})
                lines = [ln for ln in r.text.splitlines() if ln.strip()]
                out[sid] = {"status": r.status_code, "n": len(lines) - 1, "last": lines[-1] if lines else None,
                            "header": lines[0] if lines else None}
            except Exception as exc:  # noqa: BLE001
                out[sid] = {"error": str(exc)[:150]}
            time.sleep(0.25)
        return out
    record("fred_series_ids", fred_ids)

    # ---------------- SEC ----------------
    sec = httpx.Client(timeout=20, headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})

    def sec_tickers():
        r = sec.get("https://www.sec.gov/files/company_tickers_exchange.json")
        j = r.json()
        return {"status": r.status_code, "keys": list(j.keys()), "fields": j.get("fields"), "n": len(j.get("data", [])),
                "sample": j.get("data", [])[:3]}
    record("sec_company_tickers_exchange", sec_tickers)

    def sec_subs():
        r = sec.get("https://data.sec.gov/submissions/CIK0001045810.json")
        j = r.json()
        rec = j.get("filings", {}).get("recent", {})
        return {"status": r.status_code, "top_keys": list(j.keys())[:30], "recent_keys": list(rec.keys()),
                "first_forms": rec.get("form", [])[:10], "first_dates": rec.get("filingDate", [])[:10],
                "first_acc": rec.get("accessionNumber", [])[:2], "first_doc": rec.get("primaryDocument", [])[:2],
                "first_desc": rec.get("primaryDocDescription", [])[:5], "items": rec.get("items", [])[:5]}
    record("sec_submissions_NVDA", sec_subs)

    def sec_facts():
        r = sec.get("https://data.sec.gov/api/xbrl/companyconcept/CIK0001045810/us-gaap/EarningsPerShareDiluted.json")
        j = r.json()
        units = j.get("units", {})
        usd = units.get("USD/shares", [])
        return {"status": r.status_code, "unit_keys": list(units.keys()), "n": len(usd), "sample": usd[-6:]}
    record("sec_concept_eps_NVDA", sec_facts)

    # ---------------- News RSS ----------------
    def gnews():
        out = {}
        for label, params in {
            "en": {"q": "NVIDIA stock when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
            "ko": {"q": "삼성전자 주가 when:2d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
        }.items():
            r = client.get("https://news.google.com/rss/search", params=params)
            txt = r.text
            out[label] = {"status": r.status_code, "len": len(txt), "head": txt[:1500],
                          "n_items": txt.count("<item>")}
        return out
    record("google_news_rss", gnews)

    def yrss():
        r = client.get("https://feeds.finance.yahoo.com/rss/2.0/headline", params={"s": "NVDA", "region": "US", "lang": "en-US"})
        return {"status": r.status_code, "n_items": r.text.count("<item>"), "head": r.text[:800]}
    record("yahoo_rss", yrss)

    # ---------------- KRX / KIND listing ----------------
    def kind():
        out = {}
        for mt in ("stockMkt", "kosdaqMkt"):
            r = client.get("https://kind.krx.co.kr/corpgeneral/corpList.do",
                           params={"method": "download", "searchType": "13", "marketType": mt})
            raw = r.content
            try:
                txt = raw.decode("euc-kr")
            except Exception:  # noqa: BLE001
                txt = raw.decode("cp949", errors="replace")
            tables = pd.read_html(io.StringIO(txt), header=0)
            df = tables[0] if tables else None
            out[mt] = {"status": r.status_code, "columns": [str(c) for c in df.columns] if df is not None else None,
                       "rows": len(df) if df is not None else 0,
                       "sample": df.head(3).astype(str).to_dict("records") if df is not None else None}
        return out
    record("krx_kind_listing", kind)

    # ---------------- Index constituents ----------------
    def wiki():
        out = {}
        for name, url in {"sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                          "ndx": "https://en.wikipedia.org/wiki/Nasdaq-100"}.items():
            r = client.get(url)
            tables = pd.read_html(io.StringIO(r.text))
            info = []
            for t in tables[:8]:
                info.append({"rows": len(t), "columns": [str(c) for c in t.columns][:10]})
            out[name] = {"status": r.status_code, "tables": info}
        return out
    record("wikipedia_constituents", wiki)

    RESULTS["finished_at"] = datetime.now(timezone.utc).isoformat()
    out = Path(__file__).resolve().parents[1] / "var" / "probe_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(RESULTS, indent=1, default=str, ensure_ascii=False))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
