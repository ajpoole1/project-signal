"""
Ticker watchlist for Project Signal.

WATCHLIST: 10 test tickers for Phase 1 dev/validation.
Extend this list (never the DAG files) when expanding coverage.

Volatile tickers and ETF proxies are injected automatically by the ingest DAG.
Personal or sensitive ticker additions go in config/personal/watchlist_extra.py (gitignored).
"""

WATCHLIST: list[str] = [
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

# Injected automatically — do not remove
INDEX_TICKERS: list[str] = [
    "I:VIX",
    "I:VVIX",
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
    """Returns the full ticker set: watchlist + indexes + sector ETFs."""
    return WATCHLIST + INDEX_TICKERS + SECTOR_ETF_TICKERS
