"""
Tests for polygon_client.py.

All external HTTP calls are mocked — no real API keys needed.
"""

import time
from unittest.mock import patch

import pytest
import requests
import responses as resp_lib

from plugins.polygon_client import (
    RATE_LIMIT,
    RATE_WINDOW,
    PolygonClient,
    _call_times,
    get_polygon_session,
    rate_limited_call,
)

# ---------------------------------------------------------------------------
# get_polygon_session
# ---------------------------------------------------------------------------


def test_session_mounts_https_adapter():
    session = get_polygon_session()
    adapter = session.get_adapter("https://api.polygon.io")
    assert adapter is not None
    assert adapter.max_retries.total == 5
    assert adapter.max_retries.backoff_factor == 2
    assert 429 in adapter.max_retries.status_forcelist
    assert 500 in adapter.max_retries.status_forcelist


def test_session_retry_respects_retry_after():
    session = get_polygon_session()
    adapter = session.get_adapter("https://api.polygon.io")
    assert adapter.max_retries.respect_retry_after_header is True


# ---------------------------------------------------------------------------
# rate_limited_call decorator
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_calls_under_limit():
    """Calls under the rate limit should not trigger any sleep."""
    _call_times.clear()

    call_count = [0]

    @rate_limited_call
    def dummy():
        call_count[0] += 1

    with patch("plugins.polygon_client.time.sleep") as mock_sleep:
        dummy()
        mock_sleep.assert_not_called()
        assert call_count[0] == 1


def test_rate_limiter_sleeps_when_limit_reached():
    """When the call buffer is full, the decorator must call time.sleep."""
    _call_times.clear()

    now = time.time()
    # Fill the buffer as if RATE_LIMIT calls just happened
    _call_times.extend([now] * RATE_LIMIT)

    @rate_limited_call
    def dummy():
        pass

    with patch("plugins.polygon_client.time.sleep") as mock_sleep:
        with patch("plugins.polygon_client.time.time", return_value=now):
            dummy()
        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        assert sleep_arg > 0, "sleep duration must be positive"


def test_rate_limiter_expires_old_calls():
    """Calls older than RATE_WINDOW should not count against the limit."""
    _call_times.clear()

    old = time.time() - RATE_WINDOW - 1
    _call_times.extend([old] * RATE_LIMIT)

    @rate_limited_call
    def dummy():
        pass

    with patch("plugins.polygon_client.time.sleep") as mock_sleep:
        dummy()
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# PolygonClient.get_daily_bars
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_get_daily_bars_returns_parsed_json():
    _call_times.clear()

    payload = {
        "ticker": "AAPL",
        "resultsCount": 1,
        "results": [{"o": 170.0, "h": 175.0, "l": 169.0, "c": 173.0, "v": 50000000}],
    }

    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-05",
        json=payload,
        status=200,
    )

    client = PolygonClient(api_key="test_key")
    result = client.get_daily_bars("AAPL", "2024-01-01", "2024-01-05")

    assert result["ticker"] == "AAPL"
    assert result["resultsCount"] == 1
    assert len(result["results"]) == 1


@resp_lib.activate
def test_get_daily_bars_raises_on_4xx():
    _call_times.clear()

    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v2/aggs/ticker/FAKE/range/1/day/2024-01-01/2024-01-05",
        json={"error": "Not found"},
        status=404,
    )

    client = PolygonClient(api_key="test_key")
    with pytest.raises(requests.exceptions.HTTPError):
        client.get_daily_bars("FAKE", "2024-01-01", "2024-01-05")


# ---------------------------------------------------------------------------
# PolygonClient.get_ticker_details
# ---------------------------------------------------------------------------


@resp_lib.activate
def test_get_ticker_details_returns_parsed_json():
    _call_times.clear()

    payload = {
        "results": {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "market_cap": 3000000000000,
            "sic_description": "Technology",
        }
    }

    resp_lib.add(
        resp_lib.GET,
        "https://api.polygon.io/v3/reference/tickers/AAPL",
        json=payload,
        status=200,
    )

    client = PolygonClient(api_key="test_key")
    result = client.get_ticker_details("AAPL")

    assert result["results"]["ticker"] == "AAPL"
    assert result["results"]["name"] == "Apple Inc."


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
    response = {
        "resultsCount": 1,
        "results": [{"c": 173.0}],
    }
    assert client.is_market_holiday(response) is False
