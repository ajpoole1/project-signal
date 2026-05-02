"""
Ticker routing for Project Signal.

DAG tasks never inspect ticker format directly — they call these functions.
All source-selection logic lives here and in config.py.
"""

from __future__ import annotations

from config import config as cfg
from plugins.polygon_client import PolygonClient
from plugins.yfinance_client import YFinanceClient

_POLYGON_INDEX_TICKERS = {"I:VIX", "I:VVIX"}
_YFINANCE_INDEX_TICKERS = {"^VIX", "^VVIX"}
_ALL_INDEX_TICKERS = _POLYGON_INDEX_TICKERS | _YFINANCE_INDEX_TICKERS


def get_client_for_ticker(ticker: str, polygon_api_key: str) -> PolygonClient | YFinanceClient:
    """
    Return the correct data client for a given ticker.

    Routing rules (in priority order):
      1. .TO suffix          → YFinanceClient (TSX, permanent)
      2. .V suffix           → YFinanceClient (TSX Venture, permanent)
      3. VIX/VVIX symbols    → source determined by config.VIX_SOURCE
      4. Everything else     → PolygonClient (US equities, ETFs)
    """
    if ticker.endswith(".TO") or ticker.endswith(".V"):
        return YFinanceClient()

    if ticker in _ALL_INDEX_TICKERS:
        if cfg.VIX_SOURCE == "yfinance":
            return YFinanceClient()
        return PolygonClient(polygon_api_key)

    return PolygonClient(polygon_api_key)


def resolve_vix_tickers() -> tuple[str, str]:
    """
    Return the correct VIX and VVIX ticker strings for the configured source.

    yfinance : ("^VIX",  "^VVIX")
    polygon  : ("I:VIX", "I:VVIX")
    """
    if cfg.VIX_SOURCE == "polygon":
        return "I:VIX", "I:VVIX"
    return "^VIX", "^VVIX"


def is_index_ticker(ticker: str) -> bool:
    """True for VIX/VVIX tickers in either naming convention."""
    return ticker in _ALL_INDEX_TICKERS
