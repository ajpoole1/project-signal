"""
Ticker watchlist for Project Signal.

US and TSX tickers are loaded from config/ticker_universe.json (has_data=True only).
SECTOR_ETF_TICKERS remain hardcoded — they are Phase 4 infrastructure (beta proxies)
and are not part of the personal equity universe in the JSON.

VIX/VVIX tickers are NOT listed here. They are resolved at runtime via
plugins.routing.resolve_vix_tickers() based on config.VIX_SOURCE, so
upgrading Polygon tiers never requires touching this file.

Personal or sensitive ticker additions go in config/watchlist_personal.py
(gitignored). That file should define US_TICKERS_EXTRA and TSX_TICKERS_EXTRA
lists which get appended automatically by get_all_tickers().
"""

from __future__ import annotations

import json
from pathlib import Path

_UNIVERSE_PATH = Path(__file__).parent / "ticker_universe.json"

with open(_UNIVERSE_PATH) as _f:
    _UNIVERSE: dict = json.load(_f)

_TICKERS_MAP: dict = _UNIVERSE["tickers"]

US_TICKERS: list[str] = [
    ticker
    for ticker, info in _TICKERS_MAP.items()
    if info["has_data"] and info["exchange"] == "us"
]

TSX_TICKERS: list[str] = [
    ticker
    for ticker, info in _TICKERS_MAP.items()
    if info["has_data"] and info["exchange"] in ("tsx", "tsx_venture")
]

# Sector ETFs stay hardcoded — Phase 4 beta proxy infrastructure, not in personal universe
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
    """US + TSX only — excludes sector ETFs. Used by Phase 4 relatedness DAG."""
    extra_us: list[str] = []
    extra_tsx: list[str] = []

    try:
        from config import watchlist_personal as personal  # type: ignore[import]

        extra_us = getattr(personal, "US_TICKERS_EXTRA", [])
        extra_tsx = getattr(personal, "TSX_TICKERS_EXTRA", [])
    except ImportError:
        pass

    return US_TICKERS + extra_us + TSX_TICKERS + extra_tsx
