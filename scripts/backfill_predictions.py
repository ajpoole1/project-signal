"""
Retroactively populate signal_predictions from historical llm_analysis data.

Reads all historical llm_analysis rows (non-neutral bias), joins with
stock_signals and raw_prices, inserts into signal_predictions, and
immediately resolves outcomes since all historical prices are available.

Uses signal_version = "v1.0-backfill" to distinguish from live predictions.
Commits per-ticker — safe to kill and re-run.

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_predictions.py
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_predictions.py --start 2021-01-01
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from dag_components.outcome_tracker import calculations as calc

BACKFILL_VERSION = "v1.0-backfill"
_HORIZONS = [5, 10, 20]


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
        host=os.environ.get("POSTGRES_HOST", "host.docker.internal"),
        dbname="signal",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def _load_tickers(conn, start_date: str | None) -> list[str]:
    with conn.cursor() as cur:
        if start_date:
            cur.execute(
                """
                SELECT DISTINCT ticker FROM llm_analysis
                WHERE bias != 'neutral' AND date >= %s
                ORDER BY ticker
                """,
                (start_date,),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT ticker FROM llm_analysis
                WHERE bias != 'neutral'
                ORDER BY ticker
                """
            )
        return [r[0] for r in cur.fetchall()]


def _load_analysis_rows(conn, ticker: str, start_date: str | None) -> list[dict]:
    """Load non-neutral llm_analysis rows joined with signal and price data."""
    with conn.cursor() as cur:
        if start_date:
            cur.execute(
                """
                SELECT la.date, la.bias, la.confidence,
                       ss.composite_vix_adj, ss.vix_regime, ss.vix_trend, ss.vol_environment,
                       rp.close
                FROM llm_analysis la
                JOIN stock_signals ss ON ss.ticker = la.ticker AND ss.date = la.date
                JOIN raw_prices    rp ON rp.ticker = la.ticker AND rp.date = la.date
                WHERE la.ticker = %s
                  AND la.bias != 'neutral'
                  AND la.date >= %s
                ORDER BY la.date
                """,
                (ticker, start_date),
            )
        else:
            cur.execute(
                """
                SELECT la.date, la.bias, la.confidence,
                       ss.composite_vix_adj, ss.vix_regime, ss.vix_trend, ss.vol_environment,
                       rp.close
                FROM llm_analysis la
                JOIN stock_signals ss ON ss.ticker = la.ticker AND ss.date = la.date
                JOIN raw_prices    rp ON rp.ticker = la.ticker AND rp.date = la.date
                WHERE la.ticker = %s
                  AND la.bias != 'neutral'
                ORDER BY la.date
                """,
                (ticker,),
            )
        rows = cur.fetchall()
    return [
        {
            "signal_date": r[0],
            "bias": r[1],
            "confidence": float(r[2]) if r[2] is not None else None,
            "composite_vix_adj": float(r[3]) if r[3] is not None else None,
            "vix_regime": r[4],
            "vix_trend": r[5],
            "vol_environment": r[6],
            "price_at_signal": float(r[7]),
        }
        for r in rows
    ]


def _load_future_prices(conn, ticker: str) -> dict[str, list[dict]]:
    """Load all close prices for a ticker as a date-ordered list."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, close FROM raw_prices WHERE ticker = %s ORDER BY date",
            (ticker,),
        )
        rows = cur.fetchall()
    # Return list of {"date": str, "close": float} in ascending order
    return [{"date": str(r[0]), "close": float(r[1])} for r in rows]


def _get_future_rows(all_prices: list[dict], signal_date) -> list[dict]:
    """Return price rows strictly after signal_date."""
    signal_str = str(signal_date)
    return [r for r in all_prices if r["date"] > signal_str]


def _insert_predictions(conn, ticker: str, rows: list[dict]) -> int:
    with conn.cursor() as cur:
        inserted = 0
        for row in rows:
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
                    row["signal_date"],
                    row["bias"],
                    row["confidence"],
                    row["composite_vix_adj"],
                    row["vix_regime"],
                    row["vix_trend"],
                    row["vol_environment"],
                    row["price_at_signal"],
                    BACKFILL_VERSION,
                ),
            )
            inserted += cur.rowcount
    return inserted


def _resolve_predictions(conn, ticker: str, rows: list[dict], all_prices: list[dict]) -> int:
    threshold = config.CORRECT_SIGNAL_THRESHOLD
    max_horizon = max(_HORIZONS)
    resolved = 0

    with conn.cursor() as cur:
        for row in rows:
            future = _get_future_rows(all_prices, row["signal_date"])
            if len(future) < max_horizon:
                continue  # not enough data yet

            base = row["price_at_signal"]
            p5 = calc.nth_trading_day_price(future, 5)
            p10 = calc.nth_trading_day_price(future, 10)
            p20 = calc.nth_trading_day_price(future, 20)

            r5 = calc.compute_return(base, p5)
            r10 = calc.compute_return(base, p10)
            r20 = calc.compute_return(base, p20)

            c5 = calc.is_correct(row["bias"], r5, threshold)
            c10 = calc.is_correct(row["bias"], r10, threshold)
            c20 = calc.is_correct(row["bias"], r20, threshold)

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
                (p5, p10, p20, r5, r10, r20, c5, c10, c20, ticker, row["signal_date"]),
            )
            resolved += cur.rowcount
    return resolved


def _print_running_stats(
    i: int,
    total: int,
    ticker: str,
    inserted: int,
    resolved: int,
    total_inserted: int,
    total_resolved: int,
) -> None:
    pct = total_resolved / total_inserted if total_inserted else 0
    print(
        f"  [{i:>4}/{total}] {ticker:<16}  inserted={inserted:>4}  resolved={resolved:>4}"
        f"  running: {total_inserted:>7,} ins / {total_resolved:>7,} res ({pct:.0%})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill signal predictions from llm_analysis history"
    )
    parser.add_argument(
        "--start", metavar="YYYY-MM-DD", help="Only backfill records on/after this date"
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    _load_env(root / ".env")

    conn = _get_connection()

    tickers = _load_tickers(conn, args.start)
    start_msg = f" (from {args.start})" if args.start else " (full history)"
    print(f"Prediction backfill{start_msg}: {len(tickers)} tickers with non-neutral signals")
    print()

    total_inserted = 0
    total_resolved = 0
    skipped = 0

    for i, ticker in enumerate(tickers, 1):
        analysis_rows = _load_analysis_rows(conn, ticker, args.start)
        if not analysis_rows:
            skipped += 1
            continue

        all_prices = _load_future_prices(conn, ticker)

        inserted = _insert_predictions(conn, ticker, analysis_rows)
        resolved = _resolve_predictions(conn, ticker, analysis_rows, all_prices)
        conn.commit()

        total_inserted += inserted
        total_resolved += resolved

        if i % 25 == 0 or i == len(tickers):
            _print_running_stats(
                i, len(tickers), ticker, inserted, resolved, total_inserted, total_resolved
            )

    conn.close()

    print()
    print("Done.")
    print(f"  Inserted:  {total_inserted:,} predictions")
    print(
        f"  Resolved:  {total_resolved:,} outcomes ({total_resolved / total_inserted:.0%})"
        if total_inserted
        else "  Resolved:  0"
    )
    print(f"  Skipped:   {skipped} tickers (no data)")
    if total_inserted > total_resolved:
        print(
            f"  Pending:   {total_inserted - total_resolved:,} predictions (not yet matured — run again after {max(_HORIZONS)} more trading days)"
        )


if __name__ == "__main__":
    main()
