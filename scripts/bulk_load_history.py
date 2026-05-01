"""
Bulk load historical OHLCV data from CSV into raw_prices.

Usage:
    python scripts/bulk_load_history.py --file data/history.csv --source polygon

CSV format (header required):
    ticker,date,open,high,low,close,volume

All writes are idempotent (INSERT ... ON CONFLICT DO UPDATE).
"""

import argparse
import csv
import os
import sys
from datetime import datetime

import psycopg2

# Phase 1 stub — implement in Phase 2 alongside dag_stock_ingest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk load OHLCV history into raw_prices")
    parser.add_argument("--file", required=True, help="Path to CSV file")
    parser.add_argument("--source", default="polygon", help="Data source label")
    return parser.parse_args()


def get_connection():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def main() -> None:
    args = parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    rows = []
    with open(args.file, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                (
                    row["ticker"],
                    row["date"],
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    int(row["volume"]),
                    args.source,
                    datetime.now().isoformat(),
                )
            )

    conn = get_connection()
    cur = conn.cursor()

    cur.executemany(
        """
        INSERT INTO raw_prices (ticker, date, open, high, low, close, volume, source, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker, date) DO UPDATE SET
            open       = EXCLUDED.open,
            high       = EXCLUDED.high,
            low        = EXCLUDED.low,
            close      = EXCLUDED.close,
            volume     = EXCLUDED.volume,
            source     = EXCLUDED.source,
            fetched_at = EXCLUDED.fetched_at
        """,
        rows,
    )

    conn.commit()
    cur.close()
    conn.close()
    print(f"Loaded {len(rows)} rows from {args.file}")


if __name__ == "__main__":
    main()
