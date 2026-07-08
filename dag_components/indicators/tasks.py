"""Task implementations for dag_stock_indicators.

Thin Airflow layer over dag_components.indicators.pipeline (the pure,
airflow-free helpers). Memory model (see docs/architecture.md and the AWS
migration plan §6.1): the equity universe (~7k tickers × ~280 days OHLCV) is
never held in RAM as a whole, and never crosses a task boundary via XCom.
`fetch_price_history` is a lightweight count-check that enforces ordering and
gives a UI checkpoint (the relatedness template); `compute_and_upsert_indicators`
streams the universe in batches of INDICATOR_BATCH_SIZE, reading → computing →
upserting → releasing each batch so peak RSS stays flat at one batch's footprint
regardless of universe size. Per-batch RSS is logged so the peak-at-full-universe
number falls out of the first run for free.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook

from config import config
from config.watchlist import get_all_tickers
from dag_components.indicators import pipeline
from plugins.routing import resolve_vix_tickers

log = logging.getLogger(__name__)


@task()
def fetch_price_history() -> int:
    """Verify raw_prices has data for this window. Returns count of tickers found.

    Replaces the old dict-returning version. compute_and_upsert_indicators now
    reads price data directly from the DB in batches rather than consuming a
    universe-sized XCom dict, so this task exists only to enforce task ordering
    and provide a visibility checkpoint (mirrors the relatedness template).
    """
    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    start_dt = target_dt - timedelta(days=config.PRICE_HISTORY_DAYS + 30)

    vix_ticker, vvix_ticker = resolve_vix_tickers()
    all_tickers = get_all_tickers() + [vix_ticker, vvix_ticker]

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
def compute_and_upsert_indicators(ticker_count: int) -> int:
    """Compute indicators for the equity universe in batches; upsert each batch.

    Idempotency: writes go through stock_signals' ON CONFLICT (ticker, date) DO
    UPDATE (see pipeline.upsert_batch) — deterministic recomputation that is safe
    to re-resolve, so a rerun after partial failure overwrites rather than
    duplicates. Each batch commits independently, so a retry resumes from the
    first unwritten batch rather than redoing the whole universe.

    VIX/VVIX context is computed once up front (so a row never depends on its
    batch), then the equity universe is streamed in blocks of
    INDICATOR_BATCH_SIZE: read the batch's prices → compute → upsert → release.
    Peak RSS stays flat at one batch's footprint; per-batch RSS is logged and
    the peak reported for the migration plan's §6.1 reconciliation.
    """
    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    target_date = str(target_dt)
    start_dt = target_dt - timedelta(days=config.PRICE_HISTORY_DAYS + 30)

    vix_ticker, vvix_ticker = resolve_vix_tickers()
    equity_tickers = get_all_tickers()
    batch_size = config.INDICATOR_BATCH_SIZE

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    conn = hook.get_conn()  # one connection for the whole task: reads, VIX context, and upserts
    vix = pipeline.build_vix_context(
        conn, vix_ticker, vvix_ticker, start_dt, target_dt, target_date
    )

    cur = conn.cursor()

    total = 0
    peak_rss = 0.0
    n_batches = (len(equity_tickers) + batch_size - 1) // batch_size
    for b, i in enumerate(range(0, len(equity_tickers), batch_size), start=1):
        batch = equity_tickers[i : i + batch_size]
        history = pipeline.read_prices(conn, batch, start_dt, target_dt)

        rows = []
        for ticker in batch:
            row = pipeline.compute_ticker_row(ticker, history.get(ticker, []), target_date, vix)
            if row is not None:
                rows.append(row)

        pipeline.upsert_batch(cur, rows)
        conn.commit()  # per-batch commit — see docstring on idempotency/resume
        total += len(rows)

        rss = pipeline.rss_mb()
        peak_rss = max(peak_rss, rss)
        log.info(
            "Batch %d/%d (%d tickers): upserted %d rows | RSS %.0f MiB",
            b,
            n_batches,
            len(batch),
            len(rows),
            rss,
        )
        # Release the batch before the next slice — nothing universe-sized
        # accumulates across batches (RSS must stay flat, not ramp).
        del history, rows

    cur.close()
    conn.close()
    log.info(
        "Computed indicators for %d tickers on %s across %d batches | peak RSS %.0f MiB",
        total,
        target_date,
        n_batches,
        peak_rss,
    )
    return total
