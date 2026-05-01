"""
DAG constructor utilities for Airflow pipelines.

DAGBuilder — generic base class, no project-specific defaults.
    Future: extract this class to a central airflow-utils library shared
    across all projects. Each project subclasses it with its own defaults.

SignalDAG — Project Signal defaults. Stays in this repo after extraction.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.decorators import dag as airflow_dag


class DAGBuilder:
    """
    Generic reusable DAG constructor.

    Standardizes DAG creation so every pipeline in a project shares the same
    structural defaults. Subclass and override class-level attributes to set
    project-specific defaults without touching the constructor mechanics.

    Usage:
        builder = DAGBuilder(
            dag_id="my_dag",
            schedule="0 9 * * 1-5",
            start_date=datetime(2025, 1, 1),
            default_args={"retries": 2},
            tags=["my_project"],
        )

        @builder.build
        def my_dag():
            ...

        # my_dag is now a DAG instance — no trailing call needed.

    Note for DAG files: Airflow's DagBag safe-mode requires both the strings
    "dag" and "airflow" to appear in the file before it will load it. Since
    this constructor hides all airflow imports, every DAG file must include:
        from airflow.models.dag import DAG  # noqa: F401
    """

    def __init__(
        self,
        dag_id: str,
        schedule: str,
        start_date: datetime,
        default_args: dict | None = None,
        tags: list[str] | None = None,
        catchup: bool = False,
    ) -> None:
        self.dag_id = dag_id
        self.schedule = schedule
        self.start_date = start_date
        self.default_args = default_args or {}
        self.tags = tags or []
        self.catchup = catchup

    def build(self, func):
        """
        Decorator: wraps func as an Airflow DAG and immediately instantiates it.

        The decorated name in module globals becomes a DAG instance, which is
        what Airflow's DagBag looks for when scanning DAG files.
        """
        dag_factory = airflow_dag(
            dag_id=self.dag_id,
            schedule=self.schedule,
            start_date=self.start_date,
            catchup=self.catchup,
            default_args=self.default_args,
            tags=self.tags,
        )(func)
        return dag_factory()


class SignalDAG(DAGBuilder):
    """
    Project Signal standard DAG constructor.

    Applies Signal-wide defaults for all pipelines in this project.
    Override any default by passing the kwarg explicitly.

    When DAGBuilder is extracted to a central airflow-utils repo, this class
    stays in project-signal and imports DAGBuilder from that package.
    """

    _START_DATE = datetime(2025, 1, 1)
    _BASE_TAGS = ["signal"]
    _DEFAULT_ARGS = {
        "owner": "signal",
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
    }

    def __init__(
        self,
        dag_id: str,
        schedule: str,
        tags: list[str] | None = None,
        retries: int | None = None,
        retry_delay: timedelta | None = None,
    ) -> None:
        default_args = dict(self._DEFAULT_ARGS)
        if retries is not None:
            default_args["retries"] = retries
        if retry_delay is not None:
            default_args["retry_delay"] = retry_delay

        super().__init__(
            dag_id=dag_id,
            schedule=schedule,
            start_date=self._START_DATE,
            default_args=default_args,
            tags=self._BASE_TAGS + (tags or []),
        )
