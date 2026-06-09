"""
dag_outcome_tracker — Daily prediction tracking and weekly accuracy rollup.
Triggered by dag_orchestrator after dag_llm_analysis. Run ad-hoc via Airflow UI as needed.

Task flow:
    populate_predictions → resolve_matured_predictions → compute_accuracy_rollup

Notes:
- populate_predictions and resolve_matured_predictions run every day.
- compute_accuracy_rollup runs on Sundays only (guarded inside the task).
"""

from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan

from dag_components.dag_builder import SignalDAG
from dag_components.outcome_tracker.tasks import (
    compute_accuracy_rollup,
    populate_predictions,
    resolve_matured_predictions,
)

builder = SignalDAG(
    dag_id="dag_outcome_tracker",
    schedule=None,
    retries=2,
    tags=["outcome"],
)


@builder.build
def dag_outcome_tracker():
    inserted = populate_predictions()
    resolved = resolve_matured_predictions()
    inserted >> resolved  # enforce ordering; resolve does not consume inserted count
    compute_accuracy_rollup(resolved)
