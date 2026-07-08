"""Task implementations for dag_outcome_tracker and dag_parameter_review."""

from __future__ import annotations

import logging

from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook

from config import config
from dag_components.outcome_tracker import calculations as calc
from dag_components.outcome_tracker import calculations_v2 as calc_v2

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
                 vix_regime, vix_trend, vol_environment, price_at_signal,
                 signal_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, signal_date, signal_version) DO NOTHING
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
        SELECT ticker, signal_date, signal_version, bias, price_at_signal
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

    for ticker, signal_date, signal_version, bias, price_at_signal in unresolved:
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
            WHERE ticker = %s AND signal_date = %s AND signal_version = %s
            """,
            (p5, p10, p20, r5, r10, r20, c5, c10, c20, ticker, signal_date, signal_version),
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


@task()
def resolve_outcomes_v2() -> int:
    """Seed and resolve prediction_outcomes (v2 long-format table).

    For each row in signal_predictions that has no corresponding entry in
    prediction_outcomes for a given horizon, seeds the row and resolves it
    if sufficient future prices exist.

    Skips tickers where price_at_signal < V2_MIN_PRICE (sub-dollar filter).
    Quarantines (seeds but does not resolve) any horizon whose forward price
    window contains a corporate-action seam (daily ratio > V2_MAX_DAILY_RATIO).

    Beta is looked up from sector_beta WHERE etf_proxy = 'SPY' AND
    window_days = V2_BETA_WINDOW; falls back to 1.0 if missing.

    Episode assignment runs per ticker across its full signal sequence and
    back-fills episode_id / is_episode_head on already-seeded rows too,
    so re-runs keep episode metadata current.
    """
    hook = PostgresHook(postgres_conn_id="signal_postgres")

    horizons = config.V2_PREDICTION_HORIZONS
    vol_lookback = config.V2_VOL_LOOKBACK_DAYS
    k = config.V2_THRESHOLD_VOL_MULT
    beta_window = config.V2_BETA_WINDOW
    max_ratio = config.V2_MAX_DAILY_RATIO
    min_price = config.V2_MIN_PRICE

    # Load all predictions that need v2 seeding (any horizon missing)
    predictions = hook.get_records(
        """
        SELECT DISTINCT sp.ticker, sp.signal_date, sp.bias, sp.price_at_signal,
                        sp.signal_version
        FROM signal_predictions sp
        WHERE sp.bias != 'neutral'
          AND sp.price_at_signal >= %s
          AND NOT EXISTS (
              SELECT 1 FROM prediction_outcomes po
              WHERE po.ticker = sp.ticker
                AND po.signal_date = sp.signal_date
                AND po.resolved_at IS NOT NULL
          )
        ORDER BY sp.ticker, sp.signal_date
        """,
        parameters=(min_price,),
    )

    if not predictions:
        log.info("resolve_outcomes_v2: nothing to process")
        return 0

    # Fetch SPY prices once globally
    spy_rows = hook.get_records(
        "SELECT date, close FROM raw_prices WHERE ticker = 'SPY' ORDER BY date"
    )
    spy_by_date: dict[str, float] = {str(r[0]): float(r[1]) for r in spy_rows}
    spy_dates_sorted = sorted(spy_by_date.keys())

    def _spy_after(signal_date_str: str) -> list[dict]:
        return [{"close": spy_by_date[d]} for d in spy_dates_sorted if d > signal_date_str]

    conn = hook.get_conn()
    cur = conn.cursor()
    seeded = 0
    resolved = 0

    # Group by ticker for efficient price + beta loading
    from itertools import groupby

    for ticker, ticker_preds in groupby(predictions, key=lambda r: r[0]):
        ticker_pred_list = list(ticker_preds)

        # Load full price history for this ticker
        price_rows_raw = hook.get_records(
            "SELECT date, close FROM raw_prices WHERE ticker = %s ORDER BY date",
            parameters=(ticker,),
        )
        all_prices = [{"date": str(r[0]), "close": float(r[1])} for r in price_rows_raw]
        all_closes = [r["close"] for r in all_prices]
        date_to_idx = {r["date"]: i for i, r in enumerate(all_prices)}

        # Load beta (SPY proxy) and apply Vasicek shrinkage toward 1.0.
        # beta_used = W * beta_hat + (1 - W) * 1.0; stored sector_beta unchanged.
        beta_row = hook.get_first(
            """
            SELECT beta FROM sector_beta
            WHERE ticker = %s AND etf_proxy = 'SPY' AND window_days = %s
            """,
            parameters=(ticker, beta_window),
        )
        shrinkage_w = config.V2_BETA_SHRINKAGE_W
        beta_hat = float(beta_row[0]) if beta_row else 1.0
        beta = shrinkage_w * beta_hat + (1.0 - shrinkage_w) * 1.0

        for row in ticker_pred_list:
            _, signal_date, bias, price_at_signal, _ = row
            signal_date_str = str(signal_date)
            base_price = float(price_at_signal)

            signal_idx = date_to_idx.get(signal_date_str)
            if signal_idx is None:
                continue

            # Prices strictly after signal date
            future_prices = all_prices[signal_idx + 1 :]

            # Trailing vol: prices up to and including signal date
            hist_closes = all_closes[: signal_idx + 1]
            vol = calc_v2.trailing_daily_vol(hist_closes, vol_lookback)

            spy_future = _spy_after(signal_date_str)
            spy_signal_price = spy_by_date.get(signal_date_str)

            for h in horizons:
                # Check if already seeded
                existing = hook.get_first(
                    "SELECT resolved_at FROM prediction_outcomes "
                    "WHERE ticker = %s AND signal_date = %s AND horizon_days = %s",
                    parameters=(ticker, signal_date, h),
                )
                if existing and existing[0] is not None:
                    continue  # already resolved, skip

                threshold = calc_v2.scaled_threshold(vol, h, k) if vol is not None else None

                if len(future_prices) < h:
                    # Not enough data yet — seed unresolved if not already seeded
                    if not existing:
                        cur.execute(
                            """
                            INSERT INTO prediction_outcomes
                                (ticker, signal_date, horizon_days, price_at_signal,
                                 vol_at_signal, threshold_used)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (ticker, signal_date, horizon_days) DO NOTHING
                            """,
                            (ticker, signal_date, h, base_price, vol, threshold),
                        )
                        seeded += cur.rowcount
                    continue

                forward_window = future_prices[:h]

                # Quarantine check
                if calc_v2.has_price_seam(forward_window, max_ratio):
                    if not existing:
                        cur.execute(
                            """
                            INSERT INTO prediction_outcomes
                                (ticker, signal_date, horizon_days, price_at_signal,
                                 vol_at_signal, threshold_used)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (ticker, signal_date, horizon_days) DO NOTHING
                            """,
                            (ticker, signal_date, h, base_price, vol, threshold),
                        )
                        seeded += cur.rowcount
                    log.debug(
                        "resolve_outcomes_v2: quarantined %s %s h=%d (price seam)",
                        ticker,
                        signal_date_str,
                        h,
                    )
                    continue

                future_price = float(forward_window[h - 1]["close"])
                raw_ret = calc.compute_return(base_price, future_price)

                bench_ret = (
                    calc_v2.benchmark_window_return(spy_future, h, spy_signal_price)
                    if spy_signal_price
                    else None
                )
                excess = (
                    calc_v2.excess_return(raw_ret, beta, bench_ret)
                    if raw_ret is not None and bench_ret is not None
                    else None
                )

                direction_correct, threshold_correct = calc_v2.outcome_flags(
                    bias, excess, threshold
                )

                cur.execute(
                    """
                    INSERT INTO prediction_outcomes
                        (ticker, signal_date, horizon_days, price_at_signal,
                         future_price, raw_return, benchmark_return, beta_used,
                         excess_return, vol_at_signal, threshold_used,
                         direction_correct, threshold_correct, resolved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (ticker, signal_date, horizon_days) DO UPDATE SET
                        future_price      = EXCLUDED.future_price,
                        raw_return        = EXCLUDED.raw_return,
                        benchmark_return  = EXCLUDED.benchmark_return,
                        beta_used         = EXCLUDED.beta_used,
                        excess_return     = EXCLUDED.excess_return,
                        vol_at_signal     = EXCLUDED.vol_at_signal,
                        threshold_used    = EXCLUDED.threshold_used,
                        direction_correct = EXCLUDED.direction_correct,
                        threshold_correct = EXCLUDED.threshold_correct,
                        resolved_at       = NOW()
                    """,
                    (
                        ticker,
                        signal_date,
                        h,
                        base_price,
                        future_price,
                        raw_ret,
                        bench_ret,
                        beta,
                        excess,
                        vol,
                        threshold,
                        direction_correct,
                        threshold_correct,
                    ),
                )
                resolved += cur.rowcount

        # Episode assignment: stamp all prediction_outcomes rows for this ticker
        po_rows = hook.get_records(
            """
            SELECT signal_date, bias FROM prediction_outcomes po
            JOIN signal_predictions sp
              ON sp.ticker = po.ticker AND sp.signal_date = po.signal_date
            WHERE po.ticker = %s
            GROUP BY po.signal_date, sp.bias
            ORDER BY po.signal_date
            """,
            parameters=(ticker,),
        )
        if po_rows:
            episode_input = [
                {"ticker": ticker, "signal_date": str(r[0]), "bias": r[1]} for r in po_rows
            ]
            stamped = calc_v2.assign_episodes(episode_input, config.V2_EPISODE_GAP_DAYS)
            for s in stamped:
                cur.execute(
                    """
                    UPDATE prediction_outcomes
                    SET episode_id = %s, is_episode_head = %s
                    WHERE ticker = %s AND signal_date = %s
                    """,
                    (s["episode_id"], s["is_episode_head"], ticker, s["signal_date"]),
                )

        conn.commit()

    cur.close()
    log.info("resolve_outcomes_v2: seeded=%d resolved=%d rows", seeded, resolved)
    return resolved
