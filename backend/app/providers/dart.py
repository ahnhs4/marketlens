"""OpenDART (금융감독원 전자공시시스템 Open API): Korean financial statements, dividends, corp codes.

Key: DART_API_KEY in .env (never sent to the browser). Limits: 20,000 calls/day per key, and bursts are throttled
by DART, so calls here are spaced out. Endpoints used:
  corpCode.xml          every company's 8-digit corp_code <-> 6-digit stock code (zip of XML)
  fnlttMultiAcnt.json   main accounts (BS + IS) of up to 100 companies per call - the whole market in ~25 calls
  fnlttSinglAcntAll.json  one company's full statements (BS, IS/CIS, CF) with IFRS account ids
  alotMatter.json       dividends (DPS, payout, yield) from the annual report
Report codes: 11013 = Q1, 11012 = half-year, 11014 = Q3, 11011 = annual (사업보고서).
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile

import httpx

from ..config import APP_USER_AGENT, BACKEND_DIR, get_settings
from .base import ProviderError, health

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)   # request lines would carry the key in the query string
BASE = "https://opendart.fss.or.kr/api"
REPORTS = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}     # report code -> months covered (cumulative)
REPORT_KO = {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "사업보고서"}
STATUS = {"010": "등록되지 않은 키입니다", "011": "사용할 수 없는 키입니다", "012": "접근할 수 없는 IP입니다",
          "020": "요청 제한(일 20,000건)을 초과했습니다", "021": "한 번에 조회할 수 있는 회사 수(100)를 넘었습니다",
          "100": "요청 값이 잘못됐습니다", "101": "부적절한 접근입니다", "800": "DART 시스템 점검 중입니다",
          "901": "키의 개인정보 보유기간이 만료됐습니다"}
NO_DATA = "013"
COUNTER = BACKEND_DIR / "data" / "dart_calls.json"     # calls per day survive restarts (DART's limit is per key per day)


def _redact(text: str) -> str:
    return re.sub(r"crtfc_key=[^&\s'\"]+", "crtfc_key=***", text)


class DartKeyMissing(ProviderError):
    """DART_API_KEY is not set."""


class OpenDart:
    name = "opendart"
    min_interval = 0.15      # ~6-7 calls/s keeps well under DART's burst limit

    daily_cap = 18_000      # our own ceiling under DART's 20,000/day, so pages keep working after a big fill

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = 0.0
        self._day, self.calls_today = "", 0
        try:
            saved = json.loads(COUNTER.read_text())
            self._day, self.calls_today = saved["day"], int(saved["calls"])
        except Exception:
            pass
        self._client = httpx.Client(timeout=40.0, headers={"User-Agent": APP_USER_AGENT})

    @property
    def enabled(self) -> bool:
        return bool(get_settings().dart_api_key)

    def _key(self) -> str:
        k = get_settings().dart_api_key
        if not k:
            raise DartKeyMissing("DART_API_KEY가 설정되지 않았습니다 (.env에 추가 후 백엔드 재시작)")
        return k.strip()

    def _call(self, path: str, params: dict) -> httpx.Response:
        with self._lock:
            today = time.strftime("%Y-%m-%d")
            if today != self._day:
                self._day, self.calls_today = today, 0
            if self.calls_today >= self.daily_cap:
                raise ProviderError(f"DART 요청 제한: 오늘 {self.calls_today}건 사용 (자체 한도 {self.daily_cap}건)")
            self.calls_today += 1
            if self.calls_today % 20 == 0:
                try:
                    COUNTER.write_text(json.dumps({"day": self._day, "calls": self.calls_today}))
                except OSError:
                    pass
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        for attempt in range(3):
            try:
                r = self._client.get(f"{BASE}/{path}", params={"crtfc_key": self._key(), **params})
                r.raise_for_status()
                return r
            except httpx.HTTPError as e:
                if attempt == 2:
                    msg = _redact(str(e))      # httpx puts the full URL (with the key) in its messages
                    health.fail(self.name, msg)
                    raise ProviderError(f"DART {path}: {msg}") from None
                time.sleep(1.5 * (attempt + 1))
        raise AssertionError("unreachable")

    def _json(self, path: str, params: dict) -> list[dict]:
        d = self._call(path, params).json()
        st = str(d.get("status", ""))
        if st == "000":
            health.ok(self.name)
            return d.get("list") or []
        if st == NO_DATA:
            return []
        msg = STATUS.get(st, d.get("message") or f"status {st}")
        health.fail(self.name, msg)
        raise ProviderError(f"DART {path}: {msg}")

    # ------------------------------------------------------------------ endpoints
    def corp_codes(self) -> list[dict]:
        """Listed (or once-listed) companies: corp_code, corp_name, stock_code, modify_date."""
        r = self._call("corpCode.xml", {})
        if not r.content.startswith(b"PK"):      # an error comes back as XML/JSON instead of a zip
            txt = r.content[:400].decode("utf-8", "replace")
            st = next((c for c in STATUS if f">{c}<" in txt or f'"{c}"' in txt), None)
            raise ProviderError(f"DART corpCode: {STATUS.get(st, txt[:120])}")
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read(z.namelist()[0])
        out = []
        for el in ET.fromstring(xml).iter("list"):
            code = (el.findtext("stock_code") or "").strip()
            if code:
                out.append({"corp_code": el.findtext("corp_code").strip(), "corp_name": (el.findtext("corp_name") or "").strip(),
                            "stock_code": code, "modify_date": (el.findtext("modify_date") or "").strip()})
        health.ok(self.name)
        return out

    def multi_accounts(self, corp_codes: list[str], year: int, reprt: str) -> list[dict]:
        """Main BS/IS accounts for many companies (CFS and OFS rows, keyed by stock_code)."""
        rows: list[dict] = []
        for i in range(0, len(corp_codes), 100):
            rows += self._json("fnlttMultiAcnt.json", {"corp_code": ",".join(corp_codes[i:i + 100]),
                                                        "bsns_year": str(year), "reprt_code": reprt})
        return rows

    def full_statements(self, corp_code: str, year: int, reprt: str, fs_div: str = "CFS") -> list[dict]:
        return self._json("fnlttSinglAcntAll.json", {"corp_code": corp_code, "bsns_year": str(year),
                                                      "reprt_code": reprt, "fs_div": fs_div})

    def dividends(self, corp_code: str, year: int) -> list[dict]:
        return self._json("alotMatter.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"})


dart = OpenDart()
