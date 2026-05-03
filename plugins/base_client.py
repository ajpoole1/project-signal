"""
Base HTTP client for Project Signal data providers.

BaseMarketClient  — shared backoff session, extended by all provider clients.
rate_limited_call — decorator applied to PolygonClient methods only.
                    Config-driven: no-op on paid tiers, enforces 5 req/min on free.
"""

from __future__ import annotations

import time
from functools import wraps

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Polygon rate limits — self-contained here since PolygonClient is reference-only.
_POLYGON_TIER = "free"
_POLYGON_RATE_LIMITS = {
    "free": {"calls_per_min": 5},
    "starter": {"calls_per_min": 999},
    "developer": {"calls_per_min": 999},
}


def rate_limited_call(func):
    """Rate-limit decorator for PolygonClient methods."""
    call_times: list[float] = []

    @wraps(func)
    def wrapper(*args, **kwargs):
        limit = _POLYGON_RATE_LIMITS[_POLYGON_TIER]["calls_per_min"]
        now = time.time()
        call_times[:] = [t for t in call_times if now - t < 60]

        if len(call_times) >= limit:
            sleep_for = 60 - (now - call_times[0])
            if sleep_for > 0:
                time.sleep(sleep_for + 0.1)

        call_times.append(time.time())
        return func(*args, **kwargs)

    return wrapper


class BaseMarketClient:
    """
    Shared base for all market data clients.
    Provides a requests Session with exponential backoff pre-configured.
    Subclasses add provider-specific fetch methods.
    """

    def _get_session(self) -> requests.Session:
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
