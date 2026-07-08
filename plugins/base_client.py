"""
Base HTTP client for Project Signal data providers.

BaseMarketClient — shared backoff session, extended by all provider clients.
                   EODHDClient is the only active client; session is pre-configured
                   with exponential backoff on 429/5xx.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


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
