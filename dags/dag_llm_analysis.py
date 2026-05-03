"""
dag_llm_analysis — Nightly per-ticker LLM signal interpretation + daily brief.
Schedule: 09:00 UTC weekdays (04:00 EST) — runs after dag_stock_indicators (08:00 UTC).

Task flow:
    select_tickers → analyze_and_upsert → generate_brief → push_to_jarvis
"""

from airflow.models.dag import DAG  # noqa: F401 — satisfies DagBag safe-mode file scan

from dag_components.dag_builder import SignalDAG
from dag_components.llm.tasks import (
    analyze_and_upsert,
    generate_brief,
    push_to_jarvis,
    select_tickers,
)

builder = SignalDAG(
    dag_id="dag_llm_analysis",
    schedule="0 9 * * 1-5",  # 09:00 UTC = 04:00 EST, weekdays
    tags=["llm"],
)


@builder.build
def dag_llm_analysis():
    tickers = select_tickers()
    count = analyze_and_upsert(tickers)
    brief = generate_brief(count)
    push_to_jarvis(brief)
