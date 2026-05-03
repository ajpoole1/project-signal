"""Task implementations for dag_stock_relatedness."""

from __future__ import annotations

import logging
from datetime import timedelta

from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook
from psycopg2.extras import execute_values

from config import config
from config.watchlist import get_equity_tickers
from dag_components.relatedness import calculations as calc

log = logging.getLogger(__name__)

_ETF_TICKERS = list(config.SECTOR_ETFS.values())


@task()
def fetch_price_history() -> dict[str, list[dict]]:
    """Read raw_prices for all equity tickers + sector ETFs over the max correlation window."""
    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    start_dt = target_dt - timedelta(days=config.RELATEDNESS_HISTORY_DAYS)

    equity_tickers = get_equity_tickers()
    all_tickers = equity_tickers + _ETF_TICKERS

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    rows = hook.get_records(
        """
        SELECT ticker, date, close
        FROM raw_prices
        WHERE ticker = ANY(%s)
          AND date BETWEEN %s AND %s
        ORDER BY ticker, date
        """,
        parameters=(all_tickers, start_dt, target_dt),
    )

    history: dict[str, list[dict]] = {}
    for ticker, dt, close in rows:
        history.setdefault(ticker, []).append({"date": str(dt), "close": float(close)})

    log.info(
        "Loaded price history for %d tickers between %s and %s",
        len(history),
        start_dt,
        target_dt,
    )
    return history


@task()
def compute_and_upsert_correlations(price_history: dict[str, list[dict]]) -> int:
    """Compute Pearson r for all equity pairs × all windows, upsert to relatedness_matrix."""
    equity_tickers = get_equity_tickers()
    all_tickers = equity_tickers + _ETF_TICKERS

    returns = calc.build_returns_matrix(price_history, all_tickers)
    if returns.empty:
        log.warning("No return data available — skipping correlation computation.")
        return 0

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    conn = hook.get_conn()
    cur = conn.cursor()

    total = 0
    for window in config.CORRELATION_WINDOWS:
        pairs = calc.correlation_pairs(returns, window, equity_tickers)
        if not pairs:
            log.info("Window %d: no correlation pairs computed.", window)
            continue

        execute_values(
            cur,
            """
            INSERT INTO relatedness_matrix (ticker_a, ticker_b, window_days, pearson_r)
            VALUES %s
            ON CONFLICT (ticker_a, ticker_b, window_days) DO UPDATE SET
                pearson_r   = EXCLUDED.pearson_r,
                computed_at = NOW()
            """,
            pairs,
            page_size=10_000,
        )
        total += len(pairs)
        log.info("Window %d: upserted %d correlation pairs.", window, len(pairs))

    conn.commit()
    cur.close()
    log.info("Total correlation rows upserted: %d", total)
    return total


@task()
def compute_and_upsert_betas(price_history: dict[str, list[dict]]) -> int:
    """Compute beta for all equity tickers × ETF proxies × windows, upsert to sector_beta."""
    equity_tickers = get_equity_tickers()
    all_tickers = equity_tickers + _ETF_TICKERS

    returns = calc.build_returns_matrix(price_history, all_tickers)
    if returns.empty:
        log.warning("No return data available — skipping beta computation.")
        return 0

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    conn = hook.get_conn()
    cur = conn.cursor()

    total = 0
    for window in config.BETA_WINDOWS:
        betas = calc.beta_values(returns, equity_tickers, _ETF_TICKERS, window)
        if not betas:
            log.info("Window %d: no beta values computed.", window)
            continue

        execute_values(
            cur,
            """
            INSERT INTO sector_beta (ticker, etf_proxy, beta, window_days)
            VALUES %s
            ON CONFLICT (ticker, etf_proxy, window_days) DO UPDATE SET
                beta        = EXCLUDED.beta,
                computed_at = NOW()
            """,
            betas,
            page_size=10_000,
        )
        total += len(betas)
        log.info("Window %d: upserted %d beta rows.", window, len(betas))

    conn.commit()
    cur.close()
    log.info("Total beta rows upserted: %d", total)
    return total
