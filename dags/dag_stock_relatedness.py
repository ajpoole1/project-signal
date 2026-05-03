"""
dag_stock_relatedness — Weekly correlation and beta computation.
Schedule: 10:00 UTC Sundays (05:00 EST) — no market activity, full week of data available.

Task flow:
    fetch_price_history → compute_and_upsert_correlations
                        → compute_and_upsert_betas
"""

from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan

from dag_components.dag_builder import SignalDAG
from dag_components.relatedness.tasks import (
    compute_and_upsert_betas,
    compute_and_upsert_correlations,
    fetch_price_history,
)

builder = SignalDAG(
    dag_id="dag_stock_relatedness",
    schedule="0 10 * * 0",  # 10:00 UTC = 05:00 EST, Sundays
    tags=["relatedness"],
)


@builder.build
def dag_stock_relatedness():
    history = fetch_price_history()
    compute_and_upsert_correlations(history)
    compute_and_upsert_betas(history)
