"""
dag_parameter_review — Weekly parameter health report via Sonnet.
Triggered by dag_orchestrator on Sundays after dag_outcome_tracker. Run ad-hoc via Airflow UI as needed.

Task flow:
    read_pending_proposals → generate_review_brief → push_review_to_jarvis
"""

from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan

from dag_components.dag_builder import SignalDAG
from dag_components.outcome_tracker.parameter_review_tasks import (
    generate_review_brief,
    push_review_to_jarvis,
    read_pending_proposals,
)

builder = SignalDAG(
    dag_id="dag_parameter_review",
    schedule=None,
    retries=1,
    tags=["outcome", "parameter"],
)


@builder.build
def dag_parameter_review():
    data = read_pending_proposals()
    brief = generate_review_brief(data)
    push_review_to_jarvis(brief)
