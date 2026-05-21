"""Task implementations for dag_stock_relatedness."""

from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook
from psycopg2.extras import execute_values

from config import config
from config.watchlist import get_equity_tickers
from dag_components.relatedness import calculations as calc

log = logging.getLogger(__name__)

_ETF_TICKERS = list(config.SECTOR_ETFS.values())

_INSERT_CORRELATIONS = """
    INSERT INTO relatedness_matrix (ticker_a, ticker_b, window_days, pearson_r)
    VALUES %s
"""

_INSERT_BETAS = """
    INSERT INTO sector_beta (ticker, etf_proxy, beta, window_days)
    VALUES %s
    ON CONFLICT (ticker, etf_proxy, window_days) DO UPDATE SET
        beta        = EXCLUDED.beta,
        computed_at = NOW()
"""


def _read_returns(
    hook: PostgresHook,
    all_tickers: list[str],
    start_dt,
    target_dt,
) -> pd.DataFrame:
    """Read raw_prices from DB directly into a wide-format daily-returns DataFrame.

    Reads via pd.read_sql_query (streams rows into a DataFrame) rather than
    building a Python dict-of-dicts, keeping peak memory proportional to
    n_tickers × n_days rather than n_tickers × n_days × Python-object-overhead.
    """
    conn = hook.get_conn()
    raw = pd.read_sql_query(
        """
        SELECT ticker, date, close
        FROM raw_prices
        WHERE ticker = ANY(%s)
          AND date BETWEEN %s AND %s
        ORDER BY date, ticker
        """,
        con=conn,
        params=(all_tickers, start_dt, target_dt),
    )
    conn.close()

    if raw.empty:
        return pd.DataFrame()

    prices = raw.pivot(index="date", columns="ticker", values="close")
    prices.index = pd.to_datetime(prices.index)
    prices = prices.sort_index().astype(float)
    prices.columns.name = None
    return prices.pct_change(fill_method=None)


@task()
def fetch_price_history() -> int:
    """Verify raw_prices has data for this window. Returns count of tickers found.

    Replaces the old dict-returning version. Downstream tasks now read price
    data directly from the DB rather than consuming it via XCom, so this task
    exists only to enforce task ordering and provide a visibility checkpoint.
    """
    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    start_dt = target_dt - timedelta(days=config.RELATEDNESS_HISTORY_DAYS)

    equity_tickers = get_equity_tickers()
    all_tickers = equity_tickers + _ETF_TICKERS

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    result = hook.get_first(
        """
        SELECT COUNT(DISTINCT ticker)
        FROM raw_prices
        WHERE ticker = ANY(%s)
          AND date BETWEEN %s AND %s
        """,
        parameters=(all_tickers, start_dt, target_dt),
    )
    count = int(result[0]) if result else 0
    log.info(
        "Found price data for %d / %d tickers between %s and %s",
        count,
        len(all_tickers),
        start_dt,
        target_dt,
    )
    return count


@task(execution_timeout=timedelta(hours=2))
def compute_and_upsert_correlations(ticker_count: int) -> int:
    """Compute Pearson r for all equity pairs × all windows, write to relatedness_matrix.

    Reads price history directly from DB into a pandas DataFrame to avoid the
    memory cost of deserialising a large XCom dict. Only pairs with
    |pearson_r| >= RELATEDNESS_MIN_R are kept. The table is fully replaced each
    run (TRUNCATE then per-window INSERTs) so stale pairs never persist.
    """
    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    start_dt = target_dt - timedelta(days=config.RELATEDNESS_HISTORY_DAYS)

    equity_tickers = get_equity_tickers()
    all_tickers = equity_tickers + _ETF_TICKERS

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    returns = _read_returns(hook, all_tickers, start_dt, target_dt)
    if returns.empty:
        log.warning("No return data available — skipping correlation computation.")
        return 0

    log.info("Returns matrix: %d tickers × %d days", returns.shape[1], returns.shape[0])

    conn = hook.get_conn()
    cur = conn.cursor()

    # TRUNCATE before computing any window — clears stale pairs before new results
    # arrive. Each window is then committed independently so peak memory stays
    # bounded to one window's filtered pairs at a time.
    cur.execute("TRUNCATE TABLE relatedness_matrix")
    conn.commit()

    total = 0
    for window in config.CORRELATION_WINDOWS:
        pairs = calc.correlation_pairs(
            returns,
            window,
            equity_tickers,
            min_r=config.RELATEDNESS_MIN_R,
            chunk_size=config.CORRELATION_CHUNK_SIZE,
        )
        if not pairs:
            log.info(
                "Window %d: no pairs above |r|=%.2f threshold", window, config.RELATEDNESS_MIN_R
            )
            continue

        log.info(
            "Window %d: %d pairs above |r|=%.2f threshold",
            window,
            len(pairs),
            config.RELATEDNESS_MIN_R,
        )
        execute_values(cur, _INSERT_CORRELATIONS, pairs, page_size=10_000)
        conn.commit()
        total += len(pairs)
        log.info("Window %d: committed %d rows", window, len(pairs))

    cur.close()
    if total == 0:
        log.warning("No pairs above threshold — relatedness_matrix is empty.")
    else:
        log.info("relatedness_matrix replaced: %d total rows written", total)
    return total


@task()
def compute_and_upsert_betas(ticker_count: int) -> int:
    """Compute beta for all equity tickers × ETF proxies × windows, upsert to sector_beta."""
    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    start_dt = target_dt - timedelta(days=config.RELATEDNESS_HISTORY_DAYS)

    equity_tickers = get_equity_tickers()
    all_tickers = equity_tickers + _ETF_TICKERS

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    returns = _read_returns(hook, all_tickers, start_dt, target_dt)
    if returns.empty:
        log.warning("No return data available — skipping beta computation.")
        return 0

    conn = hook.get_conn()
    cur = conn.cursor()

    total = 0
    for window in config.BETA_WINDOWS:
        betas = calc.beta_values(returns, equity_tickers, _ETF_TICKERS, window)
        if not betas:
            log.info("Window %d: no beta values computed.", window)
            continue

        execute_values(cur, _INSERT_BETAS, betas, page_size=10_000)
        total += len(betas)
        log.info("Window %d: upserted %d beta rows.", window, len(betas))

    conn.commit()
    cur.close()
    log.info("Total beta rows upserted: %d", total)
    return total
