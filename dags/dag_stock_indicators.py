"""
dag_stock_indicators — Nightly technical indicator computation.
Triggered by dag_orchestrator after dag_stock_ingest. Run ad-hoc via Airflow UI as needed.

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
    schedule=None,
    tags=["indicators"],
)


@builder.build
def dag_stock_indicators():
    history = fetch_price_history()
    rows = compute_indicators(history)
    upsert_stock_signals(rows)
