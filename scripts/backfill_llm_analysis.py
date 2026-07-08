"""
Backfill llm_analysis from historical stock_signals.

Applies the same algorithmic classification as dag_llm_analysis but skips
the Sonnet daily brief. Safe to re-run (ON CONFLICT DO NOTHING on ticker, date).

Processes ticker-by-ticker, two queries each (signals + prices). Commits per
ticker so the job is safe to kill and restart.

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_llm_analysis.py
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_llm_analysis.py --start 2024-12-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from dag_components.llm import calculations as calc
from scripts._db import get_connection, load_env

BACKFILL_MODEL = "algorithmic"
TREND_WINDOW = 3  # prior rows used to compute signal_trend


def _load_tickers(conn, start_date: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ticker FROM stock_signals
            WHERE date >= %s AND ABS(composite_vix_adj) >= %s
            ORDER BY ticker
            """,
            (start_date, config.LLM_SIGNAL_THRESHOLD),
        )
        return [r[0] for r in cur.fetchall()]


def _load_signals(conn, ticker: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date, composite_vix_adj, vix_regime, vix_trend, vol_environment,
                   sma_50, sma_200, bb_upper, bb_lower, rsi_14
            FROM stock_signals WHERE ticker = %s ORDER BY date
            """,
            (ticker,),
        )
        rows = cur.fetchall()
    return [
        {
            "date": r[0],
            "composite_vix_adj": float(r[1]) if r[1] is not None else None,
            "vix_regime": r[2],
            "vix_trend": r[3],
            "vol_environment": r[4],
            "sma_50": float(r[5]) if r[5] is not None else None,
            "sma_200": float(r[6]) if r[6] is not None else None,
            "bb_upper": float(r[7]) if r[7] is not None else None,
            "bb_lower": float(r[8]) if r[8] is not None else None,
            "rsi_14": float(r[9]) if r[9] is not None else None,
        }
        for r in rows
    ]


def _load_prices(conn, ticker: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, high, low, close FROM raw_prices WHERE ticker = %s ORDER BY date",
            (ticker,),
        )
        rows = cur.fetchall()
    return [
        {
            "date": str(r[0]),
            "high": float(r[1]) if r[1] is not None else None,
            "low": float(r[2]) if r[2] is not None else None,
            "close": float(r[3]) if r[3] is not None else None,
        }
        for r in rows
    ]


def _process_ticker(
    conn,
    ticker: str,
    start_date: str,
    all_signals: list[dict],
    all_prices: list[dict],
) -> int:
    threshold = config.LLM_SIGNAL_THRESHOLD
    price_by_date = {r["date"]: i for i, r in enumerate(all_prices)}
    inserted = 0

    with conn.cursor() as cur:
        for i, signal in enumerate(all_signals):
            if str(signal["date"]) < start_date:
                continue

            score = signal.get("composite_vix_adj")
            if score is None or abs(score) < threshold:
                continue

            date_str = str(signal["date"])

            # price rows up to and including this date for key level candidates
            price_idx = price_by_date.get(date_str)
            price_rows_up_to = all_prices[: price_idx + 1] if price_idx is not None else []
            if not price_rows_up_to:
                continue
            current_price = price_rows_up_to[-1]["close"]
            if current_price is None:
                continue

            # trend from the preceding TREND_WINDOW rows plus current
            history_window = all_signals[max(0, i - TREND_WINDOW) : i + 1]
            trend = calc.signal_trend(history_window)

            bias = calc.classify_bias(score)
            confidence = calc.classify_confidence(score, trend)
            reasoning = calc.build_reasoning(signal, trend)
            candidates = calc.build_key_level_candidates(signal, price_rows_up_to)
            key_levels = calc.classify_key_levels(candidates, current_price)

            cur.execute(
                """
                INSERT INTO llm_analysis
                    (ticker, date, bias, confidence, key_levels, reasoning, model, prompt_tokens)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, date) DO NOTHING
                """,
                (
                    ticker,
                    signal["date"],
                    bias,
                    confidence,
                    json.dumps(key_levels),
                    reasoning,
                    BACKFILL_MODEL,
                    0,
                ),
            )
            inserted += cur.rowcount

    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill llm_analysis from historical stock_signals"
    )
    default_start = str(date.today() - timedelta(days=548))  # ~18 months
    parser.add_argument(
        "--start",
        metavar="YYYY-MM-DD",
        default=default_start,
        help=f"Earliest signal date to backfill (default: {default_start})",
    )
    args = parser.parse_args()

    load_env()

    conn = get_connection()

    tickers = _load_tickers(conn, args.start)
    print(
        f"LLM analysis backfill from {args.start}: {len(tickers)} tickers with signals above threshold"
    )
    print()

    total_inserted = 0
    skipped = 0

    for i, ticker in enumerate(tickers, 1):
        all_signals = _load_signals(conn, ticker)
        if not all_signals:
            skipped += 1
            continue

        all_prices = _load_prices(conn, ticker)
        inserted = _process_ticker(conn, ticker, args.start, all_signals, all_prices)
        conn.commit()

        total_inserted += inserted

        if i % 50 == 0 or i == len(tickers):
            print(
                f"  [{i:>5}/{len(tickers)}] {ticker:<16}  inserted={inserted:>4}  total={total_inserted:>8,}"
            )

    conn.close()

    print()
    print("Done.")
    print(f"  Inserted: {total_inserted:,} llm_analysis rows")
    print(f"  Skipped:  {skipped} tickers (no signal data)")
    print()
    print("Next: re-run backfill_predictions.py to populate signal_predictions for new dates.")


if __name__ == "__main__":
    main()
