"""FRED (Federal Reserve Bank of St. Louis) macro data.

* With FRED_API_KEY: official API `GET https://api.stlouisfed.org/fred/series/observations`
  (params: series_id, api_key, file_type=json, observation_start). Missing values are ".".
* Without a key: the public graph CSV endpoint `fredgraph.csv?id=...` (same data,
  no key, lower throughput). The provider label tells the UI which one served it.
"""
from __future__ import annotations

import datetime as dt
import io
import logging

import httpx
import pandas as pd

from ..config import APP_USER_AGENT, get_settings
from .base import NotFoundError, ProviderError, health

log = logging.getLogger(__name__)
API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


class FredProvider:
    name = "fred"

    def __init__(self) -> None:
        # Plain "app/version library/version" UA: FRED's edge stalls some UA formats (e.g. with parentheses).
        self._client = httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0),
                                    headers={"User-Agent": APP_USER_AGENT})

    @property
    def mode(self) -> str:
        return "api" if get_settings().fred_api_key else "public-csv"

    def series(self, series_id: str, start: dt.date | None = None) -> pd.Series:
        start = start or (dt.date.today() - dt.timedelta(days=365 * 12))
        key = get_settings().fred_api_key
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                s = self._via_api(series_id, start, key) if key else self._via_csv(series_id, start)
                health.ok("fred")
                return s
            except NotFoundError:
                raise
            except Exception as e:  # retry once - the public endpoint is occasionally slow
                last_err = e
        health.fail("fred", last_err or "unknown")
        raise ProviderError(f"FRED {series_id}: {last_err}")

    def _via_api(self, series_id: str, start: dt.date, key: str) -> pd.Series:
        r = self._client.get(API_URL, params={"series_id": series_id, "api_key": key, "file_type": "json",
                                              "observation_start": start.isoformat()})
        if r.status_code == 400 and "does not exist" in r.text:
            raise NotFoundError(series_id)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        vals = {o["date"]: (float(o["value"]) if o["value"] not in (".", "") else None) for o in obs}
        s = pd.Series(vals, dtype="float64")
        s.index = pd.to_datetime(s.index)
        return s.dropna().rename(series_id)

    def _via_csv(self, series_id: str, start: dt.date) -> pd.Series:
        r = self._client.get(CSV_URL, params={"id": series_id, "cosd": start.isoformat()})
        if r.status_code == 404:
            raise NotFoundError(series_id)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if df.shape[1] < 2:
            raise ProviderError(f"unexpected FRED csv for {series_id}")
        date_col, val_col = df.columns[0], df.columns[1]
        s = pd.to_numeric(df[val_col], errors="coerce")
        s.index = pd.to_datetime(df[date_col])
        return s.dropna().rename(series_id)


fred = FredProvider()
