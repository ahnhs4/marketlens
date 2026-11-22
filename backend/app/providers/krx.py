"""Korean listed-company master from KRX KIND (kind.krx.co.kr), no key required.

The KIND "corpList" download returns an HTML table (EUC-KR) with company name,
market, 6-digit code and industry. KOSPI codes map to Yahoo `.KS`, KOSDAQ to `.KQ`.
"""
from __future__ import annotations

import datetime as dt
import html as htmlmod
import io
import logging
import re

import httpx
import pandas as pd

from ..config import APP_USER_AGENT
from .base import ProviderError, health

log = logging.getLogger(__name__)
URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"
MARKETS = {"stockMkt": ("KOSPI", ".KS"), "kosdaqMkt": ("KOSDAQ", ".KQ")}


class KrxKind:
    name = "krx_kind"

    def listings(self) -> list[dict]:
        out: list[dict] = []
        with httpx.Client(timeout=30.0, headers={"User-Agent": APP_USER_AGENT}) as c:
            for mkt, (label, suffix) in MARKETS.items():
                try:
                    r = c.get(URL, params={"method": "download", "searchType": "13", "marketType": mkt})
                    r.raise_for_status()
                    html = r.content.decode("euc-kr", errors="replace")
                    df = pd.read_html(io.StringIO(html), header=0)[0]
                except Exception as e:
                    health.fail(self.name, e)
                    raise ProviderError(f"KRX KIND {label}: {e}") from e
                for _, row in df.iterrows():
                    code = str(row.get("종목코드", "")).strip()
                    if not code or code == "nan":
                        continue
                    code = code.zfill(6)
                    out.append({"symbol": f"{code}{suffix}", "kr_code": code, "name_ko": str(row.get("회사명", "")).strip(),
                                "market_label": label, "industry_ko": str(row.get("업종", "") or "").strip(),
                                "products_ko": str(row.get("주요제품", "") or "").strip()})
        health.ok(self.name)
        return out


    # ---------------------------------------------------------- 기업 밸류업 (KIND)
    def _valueup_post(self, path: str, form: dict) -> str:
        try:
            r = httpx.post(f"https://kind.krx.co.kr/valueup/{path}", data=form, timeout=60.0,
                           headers={"User-Agent": APP_USER_AGENT})
            r.raise_for_status()
        except Exception as e:
            health.fail(self.name, e)
            raise ProviderError(f"KRX KIND {path}: {e}") from e
        health.ok(self.name)
        return r.text

    def low_pbr(self) -> list[dict]:
        """The exchange's published 저PBR list (기업 밸류업 > 저PBR기업): PBR, GICS sector, dates."""
        page = self._valueup_post("lowpbr.do", {
            "method": "valueupLowpbrSub", "currentPageSize": "3000", "pageIndex": "1", "orderMode": "0", "orderStat": "D",
            "forward": "valueupLowpbr_sub", "repIsuSrtCd": "", "isurCd": "", "searchCorpName": "", "allRepIsuSrtCd": "",
            "marketType": "", "gicsSectCd": "", "appldDd": ""})
        out = []
        for tr in re.findall(r"<tr.*?</tr>", page, re.S):
            m = re.search(r"companysummary_open\('(\w+)'\)", tr)
            tds = [htmlmod.unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if not m or len(tds) < 7:
                continue
            out.append({"isur_cd": m.group(1), "name": tds[1], "market": _market(tr), "published": tds[2],
                        "pbr": _num(tds[3]), "gics_sector": tds[4] or None, "pbr_date": tds[5], "fiscal_month": tds[6]})
        return out

    def valueup_disclosures(self, since: dt.date = dt.date(2024, 5, 1)) -> list[dict]:
        """Every 기업가치 제고 계획 disclosure (plans, previews, progress reports, corrections) since `since`."""
        page = self._valueup_post("disclsstat.do", {
            "method": "valueupDisclsStatSub", "currentPageSize": "5000", "pageIndex": "1", "orderMode": "1", "orderStat": "D",
            "forward": "valueupDisclsStat_sub", "repIsuSrtCd": "", "isurCd": "", "searchCorpName": "", "allRepIsuSrtCd": "",
            "fromDate": since.isoformat(), "toDate": dt.date.today().isoformat(), "marketType": ""})
        out = []
        for tr in re.findall(r"<tr.*?</tr>", page, re.S):
            m = re.search(r"companysummary_open\('(\w+)'\)", tr)
            v = re.search(r"openDisclsViewer\('(\d+)'", tr)
            tds = [htmlmod.unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if not m or not v or len(tds) < 4:
                continue
            title = re.sub(r"\s+", " ", tds[3])
            out.append({"isur_cd": m.group(1), "name": re.sub(r"\s+", " ", tds[2]).strip(), "market": _market(tr),
                        "time": tds[1], "title": title, "acptno": v.group(1),
                        "url": f"https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={v.group(1)}",
                        "kind": _vu_kind(title),
                        "tags": re.findall(r"alt='(V100|KOSPI200|KOSDAQ150|KRX300|KTOP30)'", tr)})
        return out


def _market(tr: str) -> str | None:
    return "KOSPI" if "alt='유가증권'" in tr else "KOSDAQ" if "alt='코스닥'" in tr else "KONEX" if "alt='코넥스'" in tr else None


def _num(x: str) -> float | None:
    try:
        return float(x.replace(",", ""))
    except ValueError:
        return None


def _vu_kind(title: str) -> str:
    """plan | progress (이행현황) | preview (예고) - corrections ([정정]) keep the kind of what they correct."""
    if "예고" in title:
        return "preview"
    if "이행" in title:
        return "progress"
    return "plan"


krx = KrxKind()
