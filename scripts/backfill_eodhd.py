"""
EODHD historical backfill — fetch full OHLCV history for all tickers.

One API call per ticker (EODHD accepts a full date range in a single request).
Each ticker is committed immediately — safe to kill and re-run, no data lost.
All writes are idempotent: ON CONFLICT DO UPDATE.

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_eodhd.py
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_eodhd.py --start 2020-01-01

Default start: 5 years ago. End: today.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.watchlist import get_all_tickers
from plugins.eodhdclient import EODHDClient
from plugins.routing import resolve_vix_tickers
from scripts._db import get_connection, load_env


def _upsert(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill OHLCV history via EODHD")
    parser.add_argument(
        "--start",
        default=(date.today() - timedelta(days=5 * 365)).strftime("%Y-%m-%d"),
        help="Start date YYYY-MM-DD (default: 5 years ago)",
    )
    args = parser.parse_args()

    load_env()

    start = args.start
    end = date.today().strftime("%Y-%m-%d")

    api_key = os.environ["EODHD_API_KEY"]
    client = EODHDClient(api_key)

    vix_ticker, vvix_ticker = resolve_vix_tickers()
    tickers = get_all_tickers() + [vix_ticker, vvix_ticker]

    print(f"EODHD backfill: {len(tickers)} tickers | {start} → {end}")
    print()

    conn = get_connection()
    total_rows = 0
    failed: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        try:
            bars = client.fetch_ohlcv(ticker, start, end)
        except Exception as exc:
            print(f"  [{i:>3}/{len(tickers)}] {ticker:<16} FAILED — {exc}")
            failed.append(ticker)
            continue

        if not bars:
            print(f"  [{i:>3}/{len(tickers)}] {ticker:<16} no data")
            continue

        rows = [
            (
                b["ticker"],
                b["date"],
                b["open"],
                b["high"],
                b["low"],
                b["close"],
                b["volume"],
                b["currency"],
                b["source"],
            )
            for b in bars
        ]
        _upsert(conn, rows)
        total_rows += len(rows)
        print(
            f"  [{i:>3}/{len(tickers)}] {ticker:<16} {len(rows):>5} bars  (running total: {total_rows:,})"
        )

    conn.close()
    print()
    print(f"Done. {total_rows:,} rows upserted across {len(tickers) - len(failed)} tickers.")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}")


if __name__ == "__main__":
    main()
