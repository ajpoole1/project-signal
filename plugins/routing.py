"""
Ticker routing for Project Signal.

DAG tasks never inspect ticker format directly — they call these functions.
Single data source: EODHDClient handles US equities, TSX/TSX-V, and indices.

Legacy PolygonClient and YFinanceClient are retained in plugins/ for reference
but are no longer used by the active pipeline.
"""

from __future__ import annotations

from plugins.eodhdclient import EODHDClient

_EODHD_VIX = "VIX.INDX"
_EODHD_VVIX = "VVIX.INDX"


def get_client_for_ticker(ticker: str, api_key: str) -> EODHDClient:
    """Return an EODHDClient for any ticker.

    All routing is now handled inside EODHDClient._eodhd_symbol():
      .TO / .V suffixes   → passed through as-is (TSX / TSX-V)
      .INDX suffix        → passed through as-is (indices)
      everything else     → .US appended (US equities and ETFs)
    """
    return EODHDClient(api_key)


def resolve_vix_tickers() -> tuple[str, str]:
    """Return the VIX and VVIX ticker strings for use in DB queries and API calls."""
    return _EODHD_VIX, _EODHD_VVIX


def is_index_ticker(ticker: str) -> bool:
    """True for VIX/VVIX tickers."""
    return ticker in {_EODHD_VIX, _EODHD_VVIX}
