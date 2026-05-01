"""
Polygon.io API client — US equities, sector ETFs, and paid-tier indices.

Rate limiting and backoff session are inherited from BaseMarketClient.
All throttling is config-driven: changing POLYGON_TIER in config.py is
the only change needed to remove rate limits.
"""

from __future__ import annotations

from urllib.parse import urljoin

from plugins.base_client import BaseMarketClient, rate_limited_call

BASE_URL = "https://api.polygon.io"


class PolygonClient(BaseMarketClient):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = self._get_session()

    @rate_limited_call
    def get_daily_bars(self, ticker: str, from_date: str, to_date: str) -> dict:
        """Fetch OHLCV daily bars. Returns raw Polygon response dict."""
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
        """Fetch metadata (name, sector, market_cap) for a US ticker."""
        url = urljoin(BASE_URL, f"/v3/reference/tickers/{ticker}")
        resp = self.session.get(
            url,
            params={"apiKey": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_ohlcv(self, ticker: str, start: str, end: str) -> list[dict]:
        """
        Fetch OHLCV daily bars, normalized to the same shape as YFinanceClient.
        Returns an empty list on market holidays.
        """
        resp = self.get_daily_bars(ticker, start, end)
        if self.is_market_holiday(resp):
            return []
        return [
            {
                "ticker": ticker,
                "date": end,
                "open": float(bar["o"]),
                "high": float(bar["h"]),
                "low": float(bar["l"]),
                "close": float(bar["c"]),
                "volume": int(bar.get("v", 0)),
                "currency": "USD",
                "source": "polygon",
            }
            for bar in resp.get("results", [])
        ]

    def fetch_metadata(self, ticker: str) -> dict:
        """Fetch ticker metadata, normalized to the same shape as YFinanceClient."""
        resp = self.get_ticker_details(ticker)
        r = resp.get("results", {})
        return {
            "name": r.get("name"),
            "sector": r.get("sic_description"),
            "industry": r.get("sic_description"),
            "market_cap": r.get("market_cap"),
            "exchange": r.get("primary_exchange"),
        }

    def is_market_holiday(self, response: dict) -> bool:
        """True if Polygon returned an empty result set (holiday or no trading)."""
        return response.get("resultsCount", 0) == 0 or not response.get("results")
