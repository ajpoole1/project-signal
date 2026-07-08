"""
dag_stock_indicators — Nightly technical indicator computation.
Triggered by dag_orchestrator after dag_stock_ingest. Run ad-hoc via Airflow UI as needed.

Task flow:
    fetch_price_history → compute_and_upsert_indicators → compute_features

fetch_price_history is a lightweight count-check (ordering + UI checkpoint);
compute_and_upsert_indicators streams the universe in batches so the whole
price history never crosses a task boundary via XCom. See tasks.py for the
memory model.
"""

from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan

from dag_components.dag_builder import SignalDAG
from dag_components.features.tasks import compute_features
from dag_components.indicators.tasks import (
    compute_and_upsert_indicators,
    fetch_price_history,
)

builder = SignalDAG(
    dag_id="dag_stock_indicators",
    schedule=None,
    tags=["indicators"],
)


@builder.build
def dag_stock_indicators():
    ticker_count = fetch_price_history()
    upserted = compute_and_upsert_indicators(ticker_count)
    # v2 features run after stock_signals are written (needs rsi_14, macd_hist, etc.)
    upserted >> compute_features()
