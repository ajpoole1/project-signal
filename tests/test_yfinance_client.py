"""
Tests for yfinance_client.py.

All yfinance calls are mocked — no live network required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from plugins.yfinance_client import YFinanceClient


def _make_df(ticker: str, is_tsx: bool = False) -> pd.DataFrame:
    """Build a minimal one-row DataFrame shaped like yfinance .history() output."""
    idx = pd.DatetimeIndex(["2024-01-02"], name="Date")
    return pd.DataFrame(
        {
            "Open": [150.0],
            "High": [155.0],
            "Low": [149.0],
            "Close": [153.0],
            "Volume": [1_000_000],
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# fetch_ohlcv — US ticker
# ---------------------------------------------------------------------------


def test_fetch_ohlcv_us_ticker_returns_usd():
    df = _make_df("AAPL")

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("plugins.yfinance_client.yf.Ticker", return_value=mock_ticker):
        with patch("plugins.yfinance_client.time.sleep"):
            client = YFinanceClient()
            result = client.fetch_ohlcv("AAPL", "2024-01-02", "2024-01-02")

    assert len(result) == 1
    bar = result[0]
    assert bar["ticker"] == "AAPL"
    assert bar["currency"] == "USD"
    assert bar["source"] == "yfinance"
    assert bar["close"] == 153.0
    assert bar["volume"] == 1_000_000


def test_fetch_ohlcv_tsx_ticker_returns_cad():
    df = _make_df("RY.TO", is_tsx=True)

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("plugins.yfinance_client.yf.Ticker", return_value=mock_ticker):
        with patch("plugins.yfinance_client.time.sleep"):
            client = YFinanceClient()
            result = client.fetch_ohlcv("RY.TO", "2024-01-02", "2024-01-02")

    assert result[0]["currency"] == "CAD"
    assert result[0]["ticker"] == "RY.TO"


def test_fetch_ohlcv_returns_empty_on_no_data():
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()

    with patch("plugins.yfinance_client.yf.Ticker", return_value=mock_ticker):
        with patch("plugins.yfinance_client.time.sleep"):
            client = YFinanceClient()
            result = client.fetch_ohlcv("AAPL", "2024-01-01", "2024-01-01")

    assert result == []


def test_fetch_ohlcv_end_is_exclusive_adds_one_day():
    """yfinance end is exclusive — we must add 1 day internally so the target date is included."""
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()

    with patch("plugins.yfinance_client.yf.Ticker", return_value=mock_ticker):
        with patch("plugins.yfinance_client.time.sleep"):
            client = YFinanceClient()
            client.fetch_ohlcv("AAPL", "2024-01-02", "2024-01-02")

    call_kwargs = mock_ticker.history.call_args[1]
    assert call_kwargs["start"] == "2024-01-02"
    assert call_kwargs["end"] == "2024-01-03"


def test_fetch_ohlcv_missing_volume_defaults_to_zero():
    idx = pd.DatetimeIndex(["2024-01-02"], name="Date")
    df = pd.DataFrame(
        {"Open": [100.0], "High": [105.0], "Low": [99.0], "Close": [103.0]},
        index=idx,
    )

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df

    with patch("plugins.yfinance_client.yf.Ticker", return_value=mock_ticker):
        with patch("plugins.yfinance_client.time.sleep"):
            client = YFinanceClient()
            result = client.fetch_ohlcv("^VIX", "2024-01-02", "2024-01-02")

    assert result[0]["volume"] == 0


# ---------------------------------------------------------------------------
# fetch_metadata
# ---------------------------------------------------------------------------


def test_fetch_metadata_us_ticker():
    mock_ticker = MagicMock()
    mock_ticker.info = {
        "longName": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "marketCap": 3_000_000_000_000,
        "exchange": "NMS",
    }

    with patch("plugins.yfinance_client.yf.Ticker", return_value=mock_ticker):
        with patch("plugins.yfinance_client.time.sleep"):
            client = YFinanceClient()
            meta = client.fetch_metadata("AAPL")

    assert meta["name"] == "Apple Inc."
    assert meta["sector"] == "Technology"
    assert meta["exchange"] == "NMS"


def test_fetch_metadata_tsx_exchange_label():
    mock_ticker = MagicMock()
    mock_ticker.info = {
        "longName": "Royal Bank of Canada",
        "sector": "Financial Services",
        "industry": "Banks—Diversified",
        "marketCap": 200_000_000_000,
        "exchange": "TOR",
    }

    with patch("plugins.yfinance_client.yf.Ticker", return_value=mock_ticker):
        with patch("plugins.yfinance_client.time.sleep"):
            client = YFinanceClient()
            meta = client.fetch_metadata("RY.TO")

    assert meta["exchange"] == "TSX"


def test_fetch_metadata_missing_fields_return_none():
    mock_ticker = MagicMock()
    mock_ticker.info = {}

    with patch("plugins.yfinance_client.yf.Ticker", return_value=mock_ticker):
        with patch("plugins.yfinance_client.time.sleep"):
            client = YFinanceClient()
            meta = client.fetch_metadata("UNKNOWN")

    assert meta["name"] is None
    assert meta["sector"] is None
    assert meta["market_cap"] is None
