"""Tests for plugins/eodhdclient.py — all HTTP calls mocked."""

from __future__ import annotations

import pytest
import requests
import responses as resp_lib

from plugins.eodhdclient import EODHDClient

_KEY = "test_key"
_BASE = "https://eodhd.com/api"


# ---------------------------------------------------------------------------
# _eodhd_symbol
# ---------------------------------------------------------------------------


class TestEODHDSymbol:
    def _client(self):
        return EODHDClient(api_key=_KEY)

    def test_us_ticker_gets_dot_us(self):
        assert self._client()._eodhd_symbol("AAPL") == "AAPL.US"

    def test_etf_gets_dot_us(self):
        assert self._client()._eodhd_symbol("SPY") == "SPY.US"

    def test_tsx_ticker_passes_through(self):
        assert self._client()._eodhd_symbol("AC.TO") == "AC.TO"

    def test_tsxv_ticker_passes_through(self):
        assert self._client()._eodhd_symbol("GGD.V") == "GGD.V"

    def test_vix_indx_passes_through(self):
        assert self._client()._eodhd_symbol("VIX.INDX") == "VIX.INDX"

    def test_vvix_indx_passes_through(self):
        assert self._client()._eodhd_symbol("VVIX.INDX") == "VVIX.INDX"


# ---------------------------------------------------------------------------
# fetch_ohlcv
# ---------------------------------------------------------------------------


def _eod_payload(close: float = 100.0, adj_close: float = 100.0) -> list[dict]:
    return [
        {
            "date": "2024-06-03",
            "open": 99.0,
            "high": 102.0,
            "low": 98.0,
            "close": close,
            "adjusted_close": adj_close,
            "volume": 1_000_000,
        }
    ]


class TestFetchOHLCV:
    @resp_lib.activate
    def test_us_ticker_returns_normalized_bar(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/AAPL.US", json=_eod_payload(), status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("AAPL", "2024-06-03", "2024-06-03")
        assert len(bars) == 1
        bar = bars[0]
        assert bar["ticker"] == "AAPL"
        assert bar["date"] == "2024-06-03"
        assert bar["currency"] == "USD"
        assert bar["source"] == "eodhd"

    @resp_lib.activate
    def test_tsx_ticker_uses_cad_currency(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/AC.TO", json=_eod_payload(), status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("AC.TO", "2024-06-03", "2024-06-03")
        assert bars[0]["currency"] == "CAD"

    @resp_lib.activate
    def test_tsxv_ticker_uses_cad_currency(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/GGD.V", json=_eod_payload(), status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("GGD.V", "2024-06-03", "2024-06-03")
        assert bars[0]["currency"] == "CAD"

    @resp_lib.activate
    def test_close_uses_adjusted_close(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/AAPL.US", json=_eod_payload(close=110.0, adj_close=100.0), status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("AAPL", "2024-06-03", "2024-06-03")
        assert bars[0]["close"] == 100.0

    @resp_lib.activate
    def test_ohlc_scaled_by_adjustment_factor(self):
        # close=110, adj_close=99 → factor=0.9
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/AAPL.US", json=_eod_payload(close=110.0, adj_close=99.0), status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("AAPL", "2024-06-03", "2024-06-03")
        bar = bars[0]
        factor = 99.0 / 110.0
        assert bar["high"] == pytest.approx(102.0 * factor, rel=1e-5)
        assert bar["low"] == pytest.approx(98.0 * factor, rel=1e-5)

    @resp_lib.activate
    def test_no_adjustment_when_close_equals_adj_close(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/AAPL.US", json=_eod_payload(close=100.0, adj_close=100.0), status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("AAPL", "2024-06-03", "2024-06-03")
        bar = bars[0]
        assert bar["open"] == pytest.approx(99.0)
        assert bar["high"] == pytest.approx(102.0)
        assert bar["low"] == pytest.approx(98.0)

    @resp_lib.activate
    def test_empty_response_returns_empty_list(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/AAPL.US", json=[], status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("AAPL", "2024-01-01", "2024-01-01")
        assert bars == []

    @resp_lib.activate
    def test_http_error_raises(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/FAKE.US", json={"message": "Not found"}, status=404)
        with pytest.raises(requests.exceptions.HTTPError):
            EODHDClient(_KEY).fetch_ohlcv("FAKE", "2024-06-03", "2024-06-03")

    @resp_lib.activate
    def test_multiple_bars_returned(self):
        payload = [
            {"date": "2024-06-03", "open": 99.0, "high": 102.0, "low": 98.0, "close": 100.0, "adjusted_close": 100.0, "volume": 1_000_000},
            {"date": "2024-06-04", "open": 100.0, "high": 103.0, "low": 99.0, "close": 101.0, "adjusted_close": 101.0, "volume": 900_000},
        ]
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/AAPL.US", json=payload, status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("AAPL", "2024-06-03", "2024-06-04")
        assert len(bars) == 2
        assert bars[0]["date"] == "2024-06-03"
        assert bars[1]["date"] == "2024-06-04"

    @resp_lib.activate
    def test_vix_indx_ticker_uses_usd(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/eod/VIX.INDX", json=_eod_payload(), status=200)
        bars = EODHDClient(_KEY).fetch_ohlcv("VIX.INDX", "2024-06-03", "2024-06-03")
        assert bars[0]["currency"] == "USD"
        assert bars[0]["ticker"] == "VIX.INDX"


# ---------------------------------------------------------------------------
# fetch_metadata
# ---------------------------------------------------------------------------


class TestFetchMetadata:
    @resp_lib.activate
    def test_returns_normalized_dict(self):
        payload = {
            "Name": "Apple Inc.",
            "Sector": "Technology",
            "Industry": "Consumer Electronics",
            "MarketCapitalization": 3_000_000_000_000,
            "Exchange": "NASDAQ",
        }
        resp_lib.add(resp_lib.GET, f"{_BASE}/fundamentals/AAPL.US", json=payload, status=200)
        meta = EODHDClient(_KEY).fetch_metadata("AAPL")
        assert meta["name"] == "Apple Inc."
        assert meta["sector"] == "Technology"
        assert meta["industry"] == "Consumer Electronics"
        assert meta["market_cap"] == 3_000_000_000_000
        assert meta["exchange"] == "NASDAQ"

    @resp_lib.activate
    def test_tsx_ticker_calls_correct_symbol(self):
        payload = {"Name": "Air Canada", "Sector": "Industrials", "Industry": "Airlines",
                   "MarketCapitalization": 5_000_000_000, "Exchange": "TO"}
        resp_lib.add(resp_lib.GET, f"{_BASE}/fundamentals/AC.TO", json=payload, status=200)
        meta = EODHDClient(_KEY).fetch_metadata("AC.TO")
        assert meta["name"] == "Air Canada"

    @resp_lib.activate
    def test_missing_fields_return_none(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/fundamentals/XYZ.US", json={}, status=200)
        meta = EODHDClient(_KEY).fetch_metadata("XYZ")
        assert meta["name"] is None
        assert meta["sector"] is None

    @resp_lib.activate
    def test_http_error_raises(self):
        resp_lib.add(resp_lib.GET, f"{_BASE}/fundamentals/FAKE.US", json={}, status=403)
        with pytest.raises(requests.exceptions.HTTPError):
            EODHDClient(_KEY).fetch_metadata("FAKE")
