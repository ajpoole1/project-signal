"""Task implementations for dag_stock_ingest."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook
from psycopg2.extras import execute_values

from config.watchlist import get_all_tickers
from plugins.routing import get_client_for_ticker, is_index_ticker, resolve_vix_tickers

log = logging.getLogger(__name__)


@task()
def validate_watchlist() -> list[str]:
    vix_ticker, vvix_ticker = resolve_vix_tickers()
    tickers = get_all_tickers() + [vix_ticker, vvix_ticker]
    log.info("Watchlist: %d tickers (including %s, %s)", len(tickers), vix_ticker, vvix_ticker)
    return tickers


@task()
def fetch_ohlcv(tickers: list[str]) -> list[dict]:
    context = get_current_context()
    # data_interval_start = previous scheduled run time = the trading day that has closed.
    # data_interval_end = now (midnight EST) — market hasn't opened yet, Polygon has no data.
    target_date = context["data_interval_start"].strftime("%Y-%m-%d")

    api_key = os.environ["POLYGON_API_KEY"]
    bars: list[dict] = []
    empty_count = 0

    for ticker in tickers:
        client = get_client_for_ticker(ticker, api_key)
        try:
            result = client.fetch_ohlcv(ticker, target_date, target_date)
        except Exception as exc:
            log.warning("%s: fetch failed — %s", ticker, exc)
            empty_count += 1
            continue

        if not result:
            log.info("%s: no data on %s", ticker, target_date)
            empty_count += 1
            continue

        bars.extend(result)

    log.info("Fetched %d bars (%d tickers with no data)", len(bars), empty_count)
    return bars


@task()
def fetch_metadata(tickers: list[str]) -> None:
    api_key = os.environ["POLYGON_API_KEY"]
    hook = PostgresHook(postgres_conn_id="signal_postgres")

    for ticker in tickers:
        if is_index_ticker(ticker):
            continue

        row = hook.get_first(
            "SELECT updated_at FROM ticker_metadata WHERE ticker = %s",
            parameters=(ticker,),
        )
        if row and row[0] and (datetime.now(tz=row[0].tzinfo) - row[0]).days < 7:
            log.info("%s: metadata fresh, skipping", ticker)
            continue

        client = get_client_for_ticker(ticker, api_key)
        try:
            meta = client.fetch_metadata(ticker)
            hook.run(
                """
                INSERT INTO ticker_metadata (ticker, name, sector, industry, market_cap, exchange, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (ticker) DO UPDATE SET
                    name       = EXCLUDED.name,
                    sector     = EXCLUDED.sector,
                    industry   = EXCLUDED.industry,
                    market_cap = EXCLUDED.market_cap,
                    exchange   = EXCLUDED.exchange,
                    updated_at = NOW()
                """,
                parameters=(
                    ticker,
                    meta.get("name"),
                    meta.get("sector"),
                    meta.get("industry"),
                    meta.get("market_cap"),
                    meta.get("exchange"),
                ),
            )
            log.info("%s: metadata upserted", ticker)
        except Exception as exc:
            log.warning("%s: metadata fetch failed — %s", ticker, exc)


@task()
def validate_raw(bars: list[dict], tickers: list[str]) -> list[dict]:
    if not bars:
        log.info("No bars returned — market holiday. Nothing to validate.")
        return []

    fetched = {b["ticker"] for b in bars}
    missing = set(tickers) - fetched
    missing_pct = len(missing) / len(tickers)

    if missing_pct > 0.20:
        raise ValueError(
            f"{missing_pct:.1%} of tickers missing data "
            f"({len(missing)}/{len(tickers)}): {sorted(missing)}"
        )

    valid = [b for b in bars if b["close"] > 0 and b["volume"] >= 0]
    log.info(
        "validate_raw: %d/%d bars valid, %d tickers missing",
        len(valid),
        len(bars),
        len(missing),
    )
    return valid


@task()
def upsert_raw_prices(bars: list[dict]) -> None:
    if not bars:
        log.info("No bars to upsert.")
        return

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    rows = [
        (
            b["ticker"],
            b["date"],
            b["open"],
            b["high"],
            b["low"],
            b["close"],
            b["volume"],
            b.get("currency", "USD"),
            b.get("source", "polygon"),
        )
        for b in bars
    ]

    conn = hook.get_conn()
    cur = conn.cursor()
    execute_values(
        cur,
        """
        INSERT INTO raw_prices (ticker, date, open, high, low, close, volume, currency, source)
        VALUES %s
        ON CONFLICT (ticker, date) DO UPDATE SET
            open       = EXCLUDED.open,
            high       = EXCLUDED.high,
            low        = EXCLUDED.low,
            close      = EXCLUDED.close,
            volume     = EXCLUDED.volume,
            currency   = EXCLUDED.currency,
            source     = EXCLUDED.source,
            fetched_at = NOW()
        """,
        rows,
    )
    conn.commit()
    cur.close()
    log.info("Upserted %d rows to raw_prices", len(rows))
