"""
dag_orchestrator — Master pipeline sequencer for Project Signal.
catchup=False: a restart triggers at most one catch-up run, never a backlog.

Weekday (Mon–Fri):  ingest → indicators → llm_analysis → outcome_tracker
Sunday:             ingest → indicators → relatedness → llm_analysis → outcome_tracker → parameter_review

All sub-DAGs have schedule=None and are triggered exclusively from here.
Run individual DAGs ad-hoc via the Airflow UI when needed.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pendulum
from airflow.decorators import task
from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import BranchPythonOperator, get_current_context
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.trigger_rule import TriggerRule

from dag_components.dag_builder import DAGBuilder

log = logging.getLogger(__name__)


@task(trigger_rule=TriggerRule.ONE_FAILED)
def alert_on_failure() -> None:
    """Fire when any pipeline stage failed (ONE_FAILED trigger rule gates this task).

    Runs only on a failed nightly run and raises so the orchestrator run itself
    ends 'failed' — the box shutdown is unconditional either way (plan §5), so this
    is a visibility signal, not a control-flow gate. PR 3 replaces the log with the
    S3 run-summary writer + the platform alert path; until then a failed nightly is
    at least loud in the orchestrator's own task log and DAG state.
    """
    run_id = get_current_context().get("run_id", "<unknown>")
    log.error(
        "dag_orchestrator: nightly run %s failed — one or more stages did not succeed", run_id
    )
    raise RuntimeError(f"nightly pipeline run {run_id} failed — see upstream task logs")


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


# Schedule inside the nightly AWS window (~2–5am ET). The tz-aware start_date makes
# the cron America/Toronto-relative, so it stays 2:05am ET across DST rather than
# drifting an hour vs a fixed-UTC cron. The EventBridge Scheduler brings the box up
# ~2:00am ET (plan §3); this DAG fires a few minutes later, once deploy-on-boot and
# the scheduler are up.
builder = DAGBuilder(
    dag_id="dag_orchestrator",
    schedule="5 2 * * 0-5",  # 2:05am America/Toronto, Sun–Fri (no Saturday)
    start_date=pendulum.datetime(2025, 1, 1, tz="America/Toronto"),
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
    trigger_ingest = _trigger("dag_stock_ingest")
    trigger_indicators = _trigger("dag_stock_indicators")
    trigger_relatedness = _trigger("dag_stock_relatedness", poke_interval=120)
    trigger_llm = _trigger("dag_llm_analysis", poke_interval=30)
    trigger_outcome = _trigger("dag_outcome_tracker", poke_interval=30)
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

    # Fires iff at least one stage failed (ONE_FAILED, set on the task). Downstream
    # of every trigger stage so a failure anywhere in the chain surfaces loudly and
    # marks the run failed (plan §5). Box shutdown stays unconditional regardless.
    alert = alert_on_failure()

    # Weekday: ingest → indicators → [skip] → llm → outcome → [skip] → done
    # Sunday:  ingest → indicators → relatedness → llm → outcome → parameter_review → done
    trigger_ingest >> trigger_indicators >> branch_sunday
    branch_sunday >> [trigger_relatedness, skip_relatedness] >> join_post_relatedness
    join_post_relatedness >> trigger_llm >> trigger_outcome >> branch_end
    branch_end >> [trigger_param_review, skip_parameter_review] >> done

    # Every trigger stage feeds the failure alert; ONE_FAILED means it runs only
    # when one of them failed, and stays skipped on a fully-green run.
    [
        trigger_ingest,
        trigger_indicators,
        trigger_relatedness,
        trigger_llm,
        trigger_outcome,
        trigger_param_review,
    ] >> alert
