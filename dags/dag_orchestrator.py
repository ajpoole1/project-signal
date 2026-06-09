"""
dag_orchestrator — Master pipeline sequencer for Project Signal.
catchup=False: a restart triggers at most one catch-up run, never a backlog.

Weekday (Mon–Fri):  ingest → indicators → llm_analysis → outcome_tracker
Sunday:             ingest → indicators → relatedness → llm_analysis → outcome_tracker → parameter_review

All sub-DAGs have schedule=None and are triggered exclusively from here.
Run individual DAGs ad-hoc via the Airflow UI when needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.trigger_rule import TriggerRule

from dag_components.dag_builder import DAGBuilder


def _branch_sunday(**context):
    # pendulum day_of_week: 0=Mon ... 6=Sun
    if context["logical_date"].day_of_week == 6:
        return "trigger_stock_relatedness"
    return "skip_relatedness"


def _branch_param_review(**context):
    if context["logical_date"].day_of_week == 6:
        return "trigger_parameter_review"
    return "skip_parameter_review"


def _trigger(dag_id: str, poke_interval: int = 60) -> TriggerDagRunOperator:
    return TriggerDagRunOperator(
        task_id=f"trigger_{dag_id.removeprefix('dag_')}",
        trigger_dag_id=dag_id,
        execution_date="{{ execution_date }}",
        reset_dag_run=True,
        wait_for_completion=True,
        poke_interval=poke_interval,
        failed_states=["failed"],
    )


builder = DAGBuilder(
    dag_id="dag_orchestrator",
    schedule="0 5 * * 0-5",  # 5am UTC, Sun–Fri (no Saturday)
    start_date=datetime(2025, 1, 1),
    default_args={
        "owner": "signal",
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["signal", "orchestrator"],
    catchup=False,
    max_active_runs=1,
)


@builder.build
def dag_orchestrator():
    trigger_ingest      = _trigger("dag_stock_ingest")
    trigger_indicators  = _trigger("dag_stock_indicators")
    trigger_relatedness = _trigger("dag_stock_relatedness", poke_interval=120)
    trigger_llm         = _trigger("dag_llm_analysis", poke_interval=30)
    trigger_outcome     = _trigger("dag_outcome_tracker", poke_interval=30)
    trigger_param_review = _trigger("dag_parameter_review", poke_interval=30)

    branch_sunday = BranchPythonOperator(
        task_id="branch_sunday",
        python_callable=_branch_sunday,
    )
    skip_relatedness = EmptyOperator(task_id="skip_relatedness")
    join_post_relatedness = EmptyOperator(
        task_id="join_post_relatedness",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

    branch_end = BranchPythonOperator(
        task_id="branch_end",
        python_callable=_branch_param_review,
    )
    skip_parameter_review = EmptyOperator(task_id="skip_parameter_review")
    done = EmptyOperator(
        task_id="done",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

    # Weekday: ingest → indicators → [skip] → llm → outcome → [skip] → done
    # Sunday:  ingest → indicators → relatedness → llm → outcome → parameter_review → done
    trigger_ingest >> trigger_indicators >> branch_sunday
    branch_sunday >> [trigger_relatedness, skip_relatedness] >> join_post_relatedness
    join_post_relatedness >> trigger_llm >> trigger_outcome >> branch_end
    branch_end >> [trigger_param_review, skip_parameter_review] >> done
