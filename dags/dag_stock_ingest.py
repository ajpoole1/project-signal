"""
dag_stock_ingest — Nightly OHLCV + metadata ingest from EODHD.
Triggered by dag_orchestrator. Run ad-hoc via Airflow UI as needed.

Task flow:
    validate_watchlist → fetch_ohlcv  → validate_raw → upsert_raw_prices
                       ↘ fetch_metadata  (parallel with fetch_ohlcv)
"""

from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan

from dag_components.dag_builder import SignalDAG  # noqa: E402
from dag_components.ingest.tasks import (
    fetch_metadata,
    fetch_ohlcv,
    upsert_raw_prices,
    validate_raw,
    validate_watchlist,
)

builder = SignalDAG(
    dag_id="dag_stock_ingest",
    schedule=None,
    tags=["ingest"],
)


@builder.build
def dag_stock_ingest():
    tickers = validate_watchlist()
    bars = fetch_ohlcv(tickers)
    fetch_metadata(tickers)
    validated = validate_raw(bars, tickers)
    upsert_raw_prices(validated)
