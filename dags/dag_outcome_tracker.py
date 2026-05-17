"""
dag_outcome_tracker — Daily prediction tracking and weekly accuracy rollup.
Schedule: 06:00 UTC Sun–Fri (01:00 EST) — runs after ingest at 05:00 UTC.

Task flow:
    populate_predictions → resolve_matured_predictions → compute_accuracy_rollup

Notes:
- populate_predictions and resolve_matured_predictions run every day.
- compute_accuracy_rollup runs on Sundays only (guarded inside the task).
- On weekdays markets are closed Saturday so populate finds no new data on Sunday;
  the DAG is scheduled Sun–Fri (0-5 in cron) so the Sunday rollup fires at 06:00
  before dag_parameter_review runs at 11:00.
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
    schedule="0 6 * * 0-5",  # 06:00 UTC Sun–Fri; includes Sunday for weekly rollup
    retries=2,
    tags=["outcome"],
)


@builder.build
def dag_outcome_tracker():
    inserted = populate_predictions()
    resolved = resolve_matured_predictions()
    inserted >> resolved  # enforce ordering; resolve does not consume inserted count
    compute_accuracy_rollup(resolved)
