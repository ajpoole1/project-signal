"""Task implementations for dag_parameter_review."""

from __future__ import annotations

import logging

import anthropic
from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook

from config import config

log = logging.getLogger(__name__)

_REVIEW_SYSTEM = (
    "You are a quantitative analyst reviewing a stock signal system's weekly performance. "
    "Write a concise parameter health report (under 250 words). "
    "Cover: overall accuracy trend, strongest and weakest VIX regime, "
    "and a clear approve/reject recommendation for each pending proposal. "
    "Be direct and data-driven."
)


@task()
def read_pending_proposals() -> dict:
    """Read pending parameter proposals and recent accuracy data from the DB."""
    hook = PostgresHook(postgres_conn_id="signal_postgres")

    proposals = hook.get_records(
        """
        SELECT id, param_name, current_value, proposed_value, rationale,
               accuracy_delta, sample_size, vix_regime_scope, vol_env_scope
        FROM parameter_proposals
        WHERE status = 'pending'
        ORDER BY proposed_at DESC
        """
    )

    accuracy = hook.get_records(
        """
        SELECT signal_version, vix_regime, bias, horizon_days,
               total_signals, correct_signals, accuracy_rate,
               avg_return, avg_return_correct
        FROM signal_accuracy
        ORDER BY signal_version, horizon_days, accuracy_rate DESC
        """
    )

    return {
        "proposals": [
            {
                "id": r[0],
                "param_name": r[1],
                "current_value": float(r[2]),
                "proposed_value": float(r[3]),
                "rationale": r[4],
                "accuracy_delta": float(r[5]) if r[5] is not None else None,
                "sample_size": r[6],
                "vix_regime_scope": r[7],
                "vol_env_scope": r[8],
            }
            for r in proposals
        ],
        "accuracy": [
            {
                "signal_version": r[0],
                "vix_regime": r[1],
                "bias": r[2],
                "horizon_days": r[3],
                "total_signals": r[4],
                "correct_signals": r[5],
                "accuracy_rate": float(r[6]),
                "avg_return": float(r[7]) if r[7] is not None else None,
                "avg_return_correct": float(r[8]) if r[8] is not None else None,
            }
            for r in accuracy
        ],
    }


def _build_review_prompt(data: dict, report_date: str) -> str:
    accuracy = data["accuracy"]
    proposals = data["proposals"]

    lines = [f"WEEKLY PARAMETER HEALTH REPORT — {report_date}", ""]

    if accuracy:
        lines.append("=== ACCURACY BY REGIME ===")
        for row in accuracy:
            lines.append(
                f"{row['signal_version']} | {row['vix_regime']:10s} | {row['bias']:8s} "
                f"| {row['horizon_days']:2d}d | {row['correct_signals']}/{row['total_signals']} "
                f"= {row['accuracy_rate']:.1%} | avg_ret={row['avg_return']:+.2%}"
                if row["avg_return"] is not None
                else f"{row['signal_version']} | {row['vix_regime']:10s} | {row['bias']:8s} "
                f"| {row['horizon_days']:2d}d | {row['correct_signals']}/{row['total_signals']} "
                f"= {row['accuracy_rate']:.1%}"
            )
        lines.append("")
    else:
        lines.append("No accuracy data available yet.")
        lines.append("")

    if proposals:
        lines.append("=== PENDING PROPOSALS ===")
        for p in proposals:
            scope = ""
            if p["vix_regime_scope"]:
                scope += f" [regime={p['vix_regime_scope']}]"
            if p["vol_env_scope"]:
                scope += f" [vol={p['vol_env_scope']}]"
            delta = f" (+{p['accuracy_delta']:.1%})" if p["accuracy_delta"] is not None else ""
            lines.append(
                f"#{p['id']} {p['param_name']}: {p['current_value']} → {p['proposed_value']}"
                f"{delta}{scope} | n={p['sample_size']}"
            )
            lines.append(f"  Rationale: {p['rationale']}")
        lines.append("")
    else:
        lines.append("No pending proposals.")
        lines.append("")

    lines.append("Please provide your analysis and approve/reject recommendation for each proposal.")
    return "\n".join(lines)


@task()
def generate_review_brief(data: dict) -> str | None:
    """Call Sonnet to generate the weekly parameter health report."""
    context = get_current_context()
    report_date = str(context["data_interval_start"].date())

    if not data["accuracy"] and not data["proposals"]:
        log.info("generate_review_brief: no accuracy data or proposals — skipping")
        return None

    prompt = _build_review_prompt(data, report_date)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL_ANALYSIS,
        max_tokens=600,
        system=_REVIEW_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    brief_text = response.content[0].text

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    conn = hook.get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO daily_brief (date, brief_text, ticker_count, model)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (date) DO UPDATE SET
            brief_text   = EXCLUDED.brief_text,
            ticker_count = EXCLUDED.ticker_count,
            model        = EXCLUDED.model,
            created_at   = NOW()
        """,
        (report_date, brief_text, len(data["proposals"]), config.ANTHROPIC_MODEL_ANALYSIS),
    )
    conn.commit()
    cur.close()

    log.info("generate_review_brief: wrote parameter review for %s", report_date)
    return brief_text


@task()
def push_review_to_jarvis(brief: str | None) -> None:
    """Stub — Phase 7 will push the parameter review to Jarvis."""
    if brief:
        log.info("push_review_to_jarvis: review ready (%d chars) — Phase 7 will deliver this", len(brief))
    else:
        log.info("push_review_to_jarvis: no review generated this week")
