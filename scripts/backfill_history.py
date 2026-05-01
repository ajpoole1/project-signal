"""
Backfill historical OHLCV data via yfinance.

Pulls 400 calendar days of history for all watchlist tickers + VIX/VVIX and
upserts into raw_prices. Safe to re-run — all writes are idempotent.

Usage (from project root):
    python scripts/backfill_history.py
    python scripts/backfill_history.py --days 500   # override lookback

Reads credentials from .env in the project root. POSTGRES_HOST defaults to
localhost (correct when running from WSL against native Windows Postgres).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

# Allow imports from project root (plugins, config)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.watchlist import get_all_tickers
from plugins.routing import resolve_vix_tickers
from plugins.yfinance_client import YFinanceClient


def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _get_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        dbname="signal",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


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
    parser = argparse.ArgumentParser(description="Backfill OHLCV history via yfinance")
    parser.add_argument("--days", type=int, default=400, help="Calendar days to look back")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    _load_env(root / ".env")

    end = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    vix_ticker, vvix_ticker = resolve_vix_tickers()
    tickers = get_all_tickers() + [vix_ticker, vvix_ticker]

    print(f"Backfilling {len(tickers)} tickers | {start} → {end}")

    client = YFinanceClient()
    conn = _get_connection()
    total_rows = 0

    for i, ticker in enumerate(tickers, 1):
        try:
            bars = client.fetch_ohlcv(ticker, start, end)
        except Exception as exc:
            print(f"  [{i}/{len(tickers)}] {ticker}: FAILED — {exc}")
            continue

        if not bars:
            print(f"  [{i}/{len(tickers)}] {ticker}: no data")
            continue

        rows = [
            (
                b["ticker"], b["date"],
                b["open"], b["high"], b["low"], b["close"],
                b["volume"], b["currency"], b["source"],
            )
            for b in bars
        ]
        _upsert(conn, rows)
        total_rows += len(rows)
        print(f"  [{i}/{len(tickers)}] {ticker}: {len(rows)} rows upserted")

    conn.close()
    print(f"\nDone. {total_rows} total rows upserted across {len(tickers)} tickers.")


if __name__ == "__main__":
    main()
