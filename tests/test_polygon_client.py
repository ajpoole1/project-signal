"""
Tests for polygon_client.py and base_client.py.

All external HTTP calls are mocked — no real API keys needed.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
import requests
import responses as resp_lib

from plugins.base_client import BaseMarketClient, rate_limited_call
from plugins.polygon_client import PolygonClient

# ---------------------------------------------------------------------------
# BaseMarketClient._get_session
# ---------------------------------------------------------------------------


def test_session_mounts_https_adapter():
    client = BaseMarketClient()
    session = client._get_session()
    adapter = session.get_adapter("https://api.polygon.io")
    assert adapter is not None
    assert adapter.max_retries.total == 5
    assert adapter.max_retries.backoff_factor == 2
    assert 429 in adapter.max_retries.status_forcelist
    assert 500 in adapter.max_retries.status_forcelist


def test_session_retry_respects_retry_after():
    client = BaseMarketClient()
    session = client._get_session()
    adapter = session.get_adapter("https://api.polygon.io")
    assert adapter.max_retries.respect_retry_after_header is True


# ---------------------------------------------------------------------------
# rate_limited_call decorator
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_calls_under_limit():
    """Calls under the rate limit should not trigger any sleep."""
    call_count = [0]

    @rate_limited_call
    def dummy():
        call_count[0] += 1

    with patch("plugins.base_client.time.sleep") as mock_sleep:
        dummy()
        mock_sleep.assert_not_called()
        assert call_count[0] == 1


def test_rate_limiter_sleeps_when_limit_reached():
    """When the call buffer is full, the decorator must call time.sleep."""

    @rate_limited_call
    def dummy():
        pass

    now = time.time()

    # Patch time.time so the decorator sees a full window of recent calls.
    # We need to saturate the closure's call_times by calling dummy() enough
    # times with a mocked time that keeps all entries within the 60-second window.
    with patch("plugins.base_client.time.time", return_value=now):
        with patch("plugins.base_client.time.sleep"):
            # Exhaust the free-tier limit (5) so the next call must sleep
            for _ in range(5):
                dummy()

        with patch("plugins.base_client.time.sleep") as mock_sleep:
            dummy()
            mock_sleep.assert_called_once()
            sleep_arg = mock_sleep.call_args[0][0]
            assert sleep_arg > 0


def test_rate_limiter_no_sleep_on_paid_tier():
    """On starter/developer tier (999 req/min), sleep should never be called."""

    @rate_limited_call
    def dummy():
        pass

    with patch("plugins.base_client.cfg") as mock_cfg:
        mock_cfg.POLYGON_TIER = "starter"
        mock_cfg.RATE_LIMITS = {"starter": {"calls_per_min": 999}}
        with patch("plugins.base_client.time.sleep") as mock_sleep:
            for _ in range(20):
                dummy()
            mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# PolygonClient.get_daily_bars
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_get_daily_bars_returns_parsed_json():
    payload = {
        "ticker": "AAPL",
        "resultsCount": 1,
        "results": [{"o": 170.0, "h": 175.0, "l": 169.0, "c": 173.0, "v": 50_000_000}],
    }
    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-05",
        json=payload,
        status=200,
    )

    with patch("plugins.base_client.time.sleep"):
        client = PolygonClient(api_key="test_key")
        result = client.get_daily_bars("AAPL", "2024-01-01", "2024-01-05")

    assert result["ticker"] == "AAPL"
    assert len(result["results"]) == 1


@resp_lib.activate
def test_get_daily_bars_raises_on_4xx():
    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v2/aggs/ticker/FAKE/range/1/day/2024-01-01/2024-01-05",
        json={"error": "Not found"},
        status=404,
    )

    with patch("plugins.base_client.time.sleep"):
        client = PolygonClient(api_key="test_key")
        with pytest.raises(requests.exceptions.HTTPError):
            client.get_daily_bars("FAKE", "2024-01-01", "2024-01-05")


# ---------------------------------------------------------------------------
# PolygonClient.fetch_ohlcv (normalized interface)
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_fetch_ohlcv_returns_normalized_bars():
    payload = {
        "resultsCount": 1,
        "results": [{"o": 170.0, "h": 175.0, "l": 169.0, "c": 173.0, "v": 50_000_000}],
    }
    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-02/2024-01-02",
        json=payload,
        status=200,
    )

    with patch("plugins.base_client.time.sleep"):
        client = PolygonClient(api_key="test_key")
        bars = client.fetch_ohlcv("AAPL", "2024-01-02", "2024-01-02")

    assert len(bars) == 1
    bar = bars[0]
    assert bar["ticker"] == "AAPL"
    assert bar["currency"] == "USD"
    assert bar["source"] == "polygon"
    assert bar["close"] == 173.0


@resp_lib.activate
def test_fetch_ohlcv_returns_empty_on_holiday():
    payload = {"resultsCount": 0, "results": []}
    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-01",
        json=payload,
        status=200,
    )

    with patch("plugins.base_client.time.sleep"):
        client = PolygonClient(api_key="test_key")
        bars = client.fetch_ohlcv("AAPL", "2024-01-01", "2024-01-01")

    assert bars == []


# ---------------------------------------------------------------------------
# PolygonClient.get_ticker_details / fetch_metadata
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_get_ticker_details_returns_parsed_json():
    payload = {
        "results": {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "market_cap": 3_000_000_000_000,
            "sic_description": "Technology",
        }
    }
    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v3/reference/tickers/AAPL",
        json=payload,
        status=200,
    )

    with patch("plugins.base_client.time.sleep"):
        client = PolygonClient(api_key="test_key")
        result = client.get_ticker_details("AAPL")

    assert result["results"]["ticker"] == "AAPL"
    assert result["results"]["name"] == "Apple Inc."


@resp_lib.activate
def test_fetch_metadata_returns_normalized_dict():
    payload = {
        "results": {
            "name": "Apple Inc.",
            "sic_description": "Technology",
            "market_cap": 3_000_000_000_000,
            "primary_exchange": "XNAS",
        }
    }
    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v3/reference/tickers/AAPL",
        json=payload,
        status=200,
    )

    with patch("plugins.base_client.time.sleep"):
        client = PolygonClient(api_key="test_key")
        meta = client.fetch_metadata("AAPL")

    assert meta["name"] == "Apple Inc."
    assert meta["exchange"] == "XNAS"
    assert meta["market_cap"] == 3_000_000_000_000


# ---------------------------------------------------------------------------
# PolygonClient.is_market_holiday
# ---------------------------------------------------------------------------


def test_is_market_holiday_empty_results():
    client = PolygonClient(api_key="test_key")
    assert client.is_market_holiday({"resultsCount": 0, "results": []}) is True


def test_is_market_holiday_missing_results_key():
    client = PolygonClient(api_key="test_key")
    assert client.is_market_holiday({"resultsCount": 0}) is True


def test_is_market_holiday_has_data():
    client = PolygonClient(api_key="test_key")
    assert client.is_market_holiday({"resultsCount": 1, "results": [{"c": 173.0}]}) is False
