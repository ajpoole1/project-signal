"""
Polygon.io API client with config-driven rate limiting and exponential backoff.

Rate limiting is controlled entirely by POLYGON_TIER in config.py.
No sleep() calls should exist anywhere else in the codebase.
"""

import time
from functools import wraps
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.config import POLYGON_TIER, RATE_LIMITS

BASE_URL = "https://api.polygon.io"
RATE_WINDOW = 60  # seconds

_rate_config = RATE_LIMITS[POLYGON_TIER]
RATE_LIMIT = _rate_config["calls_per_min"]

_call_times: list[float] = []


def rate_limited_call(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        now = time.time()
        _call_times[:] = [t for t in _call_times if now - t < RATE_WINDOW]

        if len(_call_times) >= RATE_LIMIT:
            sleep_for = RATE_WINDOW - (now - _call_times[0])
            if sleep_for > 0:
                time.sleep(sleep_for + 0.1)

        _call_times.append(time.time())
        return func(*args, **kwargs)

    return wrapper


def get_polygon_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


class PolygonClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = get_polygon_session()

    @rate_limited_call
    def get_daily_bars(self, ticker: str, from_date: str, to_date: str) -> dict:
        """Fetch OHLCV daily bars for a ticker. Returns raw Polygon response dict."""
        url = urljoin(BASE_URL, f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}")
        resp = self.session.get(
            url,
            params={"apiKey": self.api_key, "adjusted": "true", "sort": "asc"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    @rate_limited_call
    def get_ticker_details(self, ticker: str) -> dict:
        """Fetch metadata (name, sector, market_cap) for a ticker."""
        url = urljoin(BASE_URL, f"/v3/reference/tickers/{ticker}")
        resp = self.session.get(
            url,
            params={"apiKey": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def is_market_holiday(self, response: dict) -> bool:
        """Returns True if Polygon returned an empty result set (market holiday)."""
        return response.get("resultsCount", 0) == 0 or not response.get("results")
