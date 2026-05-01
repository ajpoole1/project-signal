"""
dag_stock_indicators — Nightly technical indicator computation.
Schedule: 03:00 EST (08:00 UTC), weekdays — runs after dag_stock_ingest (05:00 UTC).

Task flow:
    fetch_price_history → compute_indicators → upsert_stock_signals
"""

from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan

from dag_components.dag_builder import SignalDAG
from dag_components.indicators.tasks import (
    compute_indicators,
    fetch_price_history,
    upsert_stock_signals,
)

builder = SignalDAG(
    dag_id="dag_stock_indicators",
    schedule="0 8 * * 1-5",  # 08:00 UTC = 03:00 EST — runs after ingest (05:00 UTC, ~2hr runtime at free tier)
    tags=["indicators"],
)


@builder.build
def dag_stock_indicators():
    history = fetch_price_history()
    rows = compute_indicators(history)
    upsert_stock_signals(rows)
