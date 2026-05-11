"""Task implementations for dag_outcome_tracker and dag_parameter_review."""

from __future__ import annotations

import logging

from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook

from config import config
from dag_components.outcome_tracker import calculations as calc

log = logging.getLogger(__name__)

_HORIZONS = [5, 10, 20]


@task()
def populate_predictions() -> int:
    """Insert today's non-neutral signals into signal_predictions.

    Joins llm_analysis + stock_signals + raw_prices for the execution date.
    Uses ON CONFLICT DO NOTHING — safe to re-run without overwriting resolved outcomes.
    Skips neutral signals.
    """
    context = get_current_context()
    target_dt = context["data_interval_start"].date()

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    rows = hook.get_records(
        """
        SELECT
            la.ticker,
            la.date                 AS signal_date,
            la.bias,
            la.confidence,
            ss.composite_vix_adj,
            ss.vix_regime,
            ss.vix_trend,
            ss.vol_environment,
            rp.close                AS price_at_signal
        FROM llm_analysis la
        JOIN stock_signals ss ON ss.ticker = la.ticker AND ss.date = la.date
        JOIN raw_prices    rp ON rp.ticker = la.ticker AND rp.date = la.date
        WHERE la.date = %s
          AND la.bias != 'neutral'
        """,
        parameters=(target_dt,),
    )

    if not rows:
        log.info("populate_predictions: no non-neutral signals for %s", target_dt)
        return 0

    from config import config as cfg  # read version at execution time

    signal_version = cfg.SIGNAL_VERSION

    conn = hook.get_conn()
    cur = conn.cursor()
    inserted = 0
    for row in rows:
        (
            ticker,
            signal_date,
            bias,
            confidence,
            composite_vix_adj,
            vix_regime,
            vix_trend,
            vol_environment,
            price_at_signal,
        ) = row
        cur.execute(
            """
            INSERT INTO signal_predictions
                (ticker, signal_date, bias, confidence, composite_vix_adj,
                 vix_regime, vix_trend, vol_environment, price_at_signal, signal_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, signal_date) DO NOTHING
            """,
            (
                ticker,
                signal_date,
                bias,
                float(confidence) if confidence is not None else None,
                float(composite_vix_adj) if composite_vix_adj is not None else None,
                vix_regime,
                vix_trend,
                vol_environment,
                float(price_at_signal),
                signal_version,
            ),
        )
        inserted += cur.rowcount

    conn.commit()
    cur.close()
    log.info("populate_predictions: inserted %d predictions for %s", inserted, target_dt)
    return inserted


@task()
def resolve_matured_predictions() -> int:
    """Resolve outcomes for predictions where all horizons have matured.

    A prediction is ready to resolve when raw_prices has >= 20 rows for that
    ticker after the signal_date (i.e. 20 trading days have elapsed).
    Prices are counted by row offset — never calendar arithmetic.
    """
    hook = PostgresHook(postgres_conn_id="signal_postgres")

    unresolved = hook.get_records(
        """
        SELECT ticker, signal_date, bias, price_at_signal
        FROM signal_predictions
        WHERE resolved_at IS NULL
        ORDER BY signal_date, ticker
        """
    )

    if not unresolved:
        log.info("resolve_matured_predictions: nothing to resolve")
        return 0

    threshold = config.CORRECT_SIGNAL_THRESHOLD
    max_horizon = max(_HORIZONS)
    resolved_count = 0

    conn = hook.get_conn()
    cur = conn.cursor()

    for ticker, signal_date, bias, price_at_signal in unresolved:
        future_prices = hook.get_records(
            """
            SELECT close
            FROM raw_prices
            WHERE ticker = %s AND date > %s
            ORDER BY date ASC
            LIMIT %s
            """,
            parameters=(ticker, signal_date, max_horizon),
        )
        price_rows = [{"close": float(r[0])} for r in future_prices]

        if len(price_rows) < max_horizon:
            # Not enough trading days have elapsed yet
            continue

        base = float(price_at_signal)
        p5 = calc.nth_trading_day_price(price_rows, 5)
        p10 = calc.nth_trading_day_price(price_rows, 10)
        p20 = calc.nth_trading_day_price(price_rows, 20)

        r5 = calc.compute_return(base, p5)
        r10 = calc.compute_return(base, p10)
        r20 = calc.compute_return(base, p20)

        c5 = calc.is_correct(bias, r5, threshold)
        c10 = calc.is_correct(bias, r10, threshold)
        c20 = calc.is_correct(bias, r20, threshold)

        cur.execute(
            """
            UPDATE signal_predictions SET
                actual_price_5d  = %s,
                actual_price_10d = %s,
                actual_price_20d = %s,
                return_5d        = %s,
                return_10d       = %s,
                return_20d       = %s,
                correct_5d       = %s,
                correct_10d      = %s,
                correct_20d      = %s,
                resolved_at      = NOW()
            WHERE ticker = %s AND signal_date = %s
            """,
            (p5, p10, p20, r5, r10, r20, c5, c10, c20, ticker, signal_date),
        )
        resolved_count += 1

    conn.commit()
    cur.close()
    log.info("resolve_matured_predictions: resolved %d predictions", resolved_count)
    return resolved_count


@task()
def compute_accuracy_rollup(resolved_count: int) -> int:
    """Aggregate resolved predictions into signal_accuracy. Runs on Sundays only.

    Groups by (signal_version, vix_regime, vol_environment, bias, horizon_days).
    Only writes groups with >= ACCURACY_MIN_SAMPLE_SIZE resolved predictions.
    """
    context = get_current_context()
    if context["data_interval_start"].weekday() != 6:  # 6 = Sunday
        log.info("compute_accuracy_rollup: skipping (not Sunday)")
        return 0

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    rows = hook.get_records(
        """
        SELECT
            signal_version, vix_regime, vol_environment, bias,
            return_5d,  return_10d,  return_20d,
            correct_5d, correct_10d, correct_20d
        FROM signal_predictions
        WHERE resolved_at IS NOT NULL
        """
    )

    predictions = [
        {
            "signal_version": r[0],
            "vix_regime": r[1],
            "vol_environment": r[2],
            "bias": r[3],
            "return_5d": float(r[4]) if r[4] is not None else None,
            "return_10d": float(r[5]) if r[5] is not None else None,
            "return_20d": float(r[6]) if r[6] is not None else None,
            "correct_5d": r[7],
            "correct_10d": r[8],
            "correct_20d": r[9],
        }
        for r in rows
    ]

    accuracy_rows = calc.compute_accuracy_rollup(
        predictions,
        horizons=config.PREDICTION_HORIZONS,
        min_sample_size=config.ACCURACY_MIN_SAMPLE_SIZE,
    )

    if not accuracy_rows:
        log.info("compute_accuracy_rollup: no groups met min sample size")
        return 0

    conn = hook.get_conn()
    cur = conn.cursor()
    for row in accuracy_rows:
        cur.execute(
            """
            INSERT INTO signal_accuracy
                (signal_version, vix_regime, vol_environment, bias, horizon_days,
                 total_signals, correct_signals, accuracy_rate,
                 avg_return, avg_return_correct, avg_return_wrong, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (signal_version, vix_regime, vol_environment, bias, horizon_days)
            DO UPDATE SET
                total_signals    = EXCLUDED.total_signals,
                correct_signals  = EXCLUDED.correct_signals,
                accuracy_rate    = EXCLUDED.accuracy_rate,
                avg_return       = EXCLUDED.avg_return,
                avg_return_correct = EXCLUDED.avg_return_correct,
                avg_return_wrong = EXCLUDED.avg_return_wrong,
                computed_at      = NOW()
            """,
            (
                row["signal_version"],
                row["vix_regime"],
                row["vol_environment"],
                row["bias"],
                row["horizon_days"],
                row["total_signals"],
                row["correct_signals"],
                row["accuracy_rate"],
                row["avg_return"],
                row["avg_return_correct"],
                row["avg_return_wrong"],
            ),
        )

    conn.commit()
    cur.close()
    log.info("compute_accuracy_rollup: wrote %d accuracy rows", len(accuracy_rows))
    return len(accuracy_rows)
