"""
EODHD client — single source for US equities, TSX/TSX-V, and indices.

Replaces both PolygonClient (US) and YFinanceClient (TSX/VIX).
Ticker format mapping:
  US equities / ETFs   → TICKER.US   (e.g. AAPL → AAPL.US)
  TSX                  → TICKER.TO   (passed through, already correct)
  TSX Venture          → TICKER.V    (passed through, already correct)
  VIX / VVIX           → VIX.INDX / VVIX.INDX

OHLCV always uses adjusted_close for the close field, and scales open/high/low
by the same adjustment factor so all four are consistent (matching yfinance
auto_adjust=True behaviour).
"""

from __future__ import annotations

from plugins.base_client import BaseMarketClient

_BASE_URL = "https://eodhd.com/api"
_EXCHANGE_SUFFIXES = (".TO", ".V", ".INDX")


class EODHDClient(BaseMarketClient):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = self._get_session()

    # ------------------------------------------------------------------
    # Public interface  (same shape as PolygonClient / YFinanceClient)
    # ------------------------------------------------------------------

    def fetch_ohlcv(self, ticker: str, start: str, end: str) -> list[dict]:
        """Fetch adjusted OHLCV bars for [start, end] inclusive.

        Returns a list of normalized dicts or [] on no-data (holiday/delisted).
        """
        resp = self.session.get(
            f"{_BASE_URL}/eod/{self._eodhd_symbol(ticker)}",
            params={
                "api_token": self.api_key,
                "fmt": "json",
                "from": start,
                "to": end,
                "period": "d",
            },
            timeout=30,
        )
        resp.raise_for_status()
        bars = resp.json()
        if not bars:
            return []

        currency = "CAD" if ticker.endswith(".TO") or ticker.endswith(".V") else "USD"
        result = []
        for bar in bars:
            raw_close = float(bar["close"])
            adj_close = float(bar["adjusted_close"])
            # Scale O/H/L by the same factor so all columns are split/dividend adjusted.
            factor = adj_close / raw_close if raw_close else 1.0
            result.append(
                {
                    "ticker": ticker,
                    "date": bar["date"],
                    "open": round(float(bar["open"]) * factor, 6),
                    "high": round(float(bar["high"]) * factor, 6),
                    "low": round(float(bar["low"]) * factor, 6),
                    "close": adj_close,
                    "volume": int(bar.get("volume", 0)),
                    "currency": currency,
                    "source": "eodhd",
                }
            )
        return result

    def fetch_splits(self, ticker: str, from_date: str) -> list[dict]:
        """Fetch stock splits on or after from_date.

        Returns list of {"date": str, "split": float} dicts (split ratio > 1 = forward split).
        Empty list if no splits or ticker not found.
        """
        resp = self.session.get(
            f"{_BASE_URL}/splits/{self._eodhd_symbol(ticker)}",
            params={"api_token": self.api_key, "fmt": "json", "from": from_date},
            timeout=30,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [
            {"date": row["date"], "split": float(row["split"])} for row in data if "date" in row
        ]

    def fetch_dividends(self, ticker: str, from_date: str) -> list[dict]:
        """Fetch dividends on or after from_date.

        Returns list of {"date": str, "value": float} dicts.
        Empty list if no dividends or ticker not found.
        """
        resp = self.session.get(
            f"{_BASE_URL}/div/{self._eodhd_symbol(ticker)}",
            params={"api_token": self.api_key, "fmt": "json", "from": from_date},
            timeout=30,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [
            {"date": row["date"], "value": float(row["value"])} for row in data if "date" in row
        ]

    def fetch_metadata(self, ticker: str) -> dict:
        """Fetch company fundamentals (General filter only — one API call)."""
        resp = self.session.get(
            f"{_BASE_URL}/fundamentals/{self._eodhd_symbol(ticker)}",
            params={
                "api_token": self.api_key,
                "fmt": "json",
                "filter": "General",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "name": data.get("Name"),
            "sector": data.get("Sector"),
            "industry": data.get("Industry"),
            "market_cap": data.get("MarketCapitalization"),
            "exchange": data.get("Exchange"),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _eodhd_symbol(self, ticker: str) -> str:
        """Convert an internal ticker to EODHD's symbol format.

        Tickers that already carry an exchange suffix (.TO, .V, .INDX)
        are passed through unchanged. All others are US equities and get .US.
        """
        if any(ticker.endswith(s) for s in _EXCHANGE_SUFFIXES):
            return ticker
        return f"{ticker}.US"
