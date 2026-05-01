"""
Ticker watchlist for Project Signal.

US_TICKERS  — routed to Polygon
TSX_TICKERS — routed to yfinance (permanent, regardless of Polygon tier)
SECTOR_ETF_TICKERS — routed to Polygon

VIX/VVIX tickers are NOT listed here. They are resolved at runtime via
plugins.routing.resolve_vix_tickers() based on config.VIX_SOURCE, so
upgrading Polygon tiers never requires touching this file.

Personal or sensitive ticker additions go in config/watchlist_personal.py
(gitignored). That file should define US_TICKERS_EXTRA and TSX_TICKERS_EXTRA
lists which get appended automatically by get_all_tickers().
"""

from __future__ import annotations

US_TICKERS: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "JPM",
    "XOM",
    "UNH",
]

TSX_TICKERS: list[str] = [
    "RY.TO",
    "TD.TO",
    "CNQ.TO",
]

SECTOR_ETF_TICKERS: list[str] = [
    "SPY",
    "QQQ",
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLY",
]


def get_all_tickers() -> list[str]:
    """
    Returns the full equity ticker set: US + TSX + sector ETFs.
    VIX/VVIX are excluded — callers that need them use resolve_vix_tickers().
    """
    extra_us: list[str] = []
    extra_tsx: list[str] = []

    try:
        from config import watchlist_personal as personal  # type: ignore[import]

        extra_us = getattr(personal, "US_TICKERS_EXTRA", [])
        extra_tsx = getattr(personal, "TSX_TICKERS_EXTRA", [])
    except ImportError:
        pass

    return US_TICKERS + extra_us + TSX_TICKERS + extra_tsx + SECTOR_ETF_TICKERS


def get_equity_tickers() -> list[str]:
    """US + TSX only — excludes sector ETFs. Used by relatedness DAG."""
    extra_us: list[str] = []
    extra_tsx: list[str] = []

    try:
        from config import watchlist_personal as personal  # type: ignore[import]

        extra_us = getattr(personal, "US_TICKERS_EXTRA", [])
        extra_tsx = getattr(personal, "TSX_TICKERS_EXTRA", [])
    except ImportError:
        pass

    return US_TICKERS + extra_us + TSX_TICKERS + extra_tsx
