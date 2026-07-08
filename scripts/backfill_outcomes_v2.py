"""
Backfill prediction_outcomes (v2 long-format table) from full signal_predictions history.

Loads each ticker's full price series once; SPY series loaded once globally.
Resolves all horizons for all historical predictions; runs assign_episodes per ticker.
Commits per-ticker — safe to kill and re-run (idempotent upserts).

Prerequisites (Phase 1 data hygiene):
  1. Full EODHD re-backfill completed (backfill_eodhd.py)
  2. backfill_indicators.py re-run
  3. backfill_predictions.py re-run
  4. yfinance rows purged: DELETE FROM raw_prices WHERE source = 'yfinance'
  5. Warrant / test-symbol tickers flagged has_data=False and removed from raw_prices

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_outcomes_v2.py
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_outcomes_v2.py --start 2022-01-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from dag_components.outcome_tracker import calculations as calc
from dag_components.outcome_tracker import calculations_v2 as calc_v2
from scripts._db import get_connection, load_env

HORIZONS = config.V2_PREDICTION_HORIZONS
MAX_HORIZON = max(HORIZONS)
VOL_LOOKBACK = config.V2_VOL_LOOKBACK_DAYS
K = config.V2_THRESHOLD_VOL_MULT
BETA_WINDOW = config.V2_BETA_WINDOW
MAX_RATIO = config.V2_MAX_DAILY_RATIO
MIN_PRICE = config.V2_MIN_PRICE
EPISODE_GAP = config.V2_EPISODE_GAP_DAYS
SHRINKAGE_W = config.V2_BETA_SHRINKAGE_W


def _load_tickers(conn, start_date: str | None) -> list[str]:
    with conn.cursor() as cur:
        if start_date:
            cur.execute(
                """
                SELECT DISTINCT ticker FROM signal_predictions
                WHERE bias != 'neutral'
                  AND price_at_signal >= %s
                  AND signal_date >= %s
                ORDER BY ticker
                """,
                (MIN_PRICE, start_date),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT ticker FROM signal_predictions
                WHERE bias != 'neutral' AND price_at_signal >= %s
                ORDER BY ticker
                """,
                (MIN_PRICE,),
            )
        return [r[0] for r in cur.fetchall()]


def _load_predictions(conn, ticker: str, start_date: str | None) -> list[dict]:
    with conn.cursor() as cur:
        if start_date:
            cur.execute(
                """
                SELECT signal_date, bias, price_at_signal
                FROM signal_predictions
                WHERE ticker = %s AND bias != 'neutral'
                  AND price_at_signal >= %s
                  AND signal_date >= %s
                ORDER BY signal_date
                """,
                (ticker, MIN_PRICE, start_date),
            )
        else:
            cur.execute(
                """
                SELECT signal_date, bias, price_at_signal
                FROM signal_predictions
                WHERE ticker = %s AND bias != 'neutral'
                  AND price_at_signal >= %s
                ORDER BY signal_date
                """,
                (ticker, MIN_PRICE),
            )
        return [
            {"signal_date": str(r[0]), "bias": r[1], "price_at_signal": float(r[2])}
            for r in cur.fetchall()
        ]


def _load_prices(conn, ticker: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, close FROM raw_prices WHERE ticker = %s ORDER BY date",
            (ticker,),
        )
        return [{"date": str(r[0]), "close": float(r[1])} for r in cur.fetchall()]


def _load_spy(conn) -> tuple[dict[str, float], list[str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT date, close FROM raw_prices WHERE ticker = 'SPY' ORDER BY date")
        rows = cur.fetchall()
    by_date = {str(r[0]): float(r[1]) for r in rows}
    return by_date, sorted(by_date.keys())


def _load_beta(conn, ticker: str) -> float:
    """Load raw beta from sector_beta and apply Vasicek shrinkage toward 1.0.

    beta_used = SHRINKAGE_W * beta_hat + (1 - SHRINKAGE_W) * 1.0
    Falls back to 1.0 if no beta row exists.
    Shrinkage reduces the impact of extreme betas from thinly-traded names
    without clipping stored values (sector_beta remains unmodified).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT beta FROM sector_beta WHERE ticker = %s AND etf_proxy = 'SPY' AND window_days = %s",
            (ticker, BETA_WINDOW),
        )
        row = cur.fetchone()
    if row is None:
        return 1.0
    beta_hat = float(row[0])
    return SHRINKAGE_W * beta_hat + (1.0 - SHRINKAGE_W) * 1.0


def _upsert_outcome(cur, row: dict) -> int:
    cur.execute(
        """
        INSERT INTO prediction_outcomes
            (ticker, signal_date, horizon_days, price_at_signal,
             future_price, raw_return, benchmark_return, beta_used,
             excess_return, vol_at_signal, threshold_used,
             direction_correct, threshold_correct, resolved_at)
        VALUES (%(ticker)s, %(signal_date)s, %(horizon_days)s, %(price_at_signal)s,
                %(future_price)s, %(raw_return)s, %(benchmark_return)s, %(beta_used)s,
                %(excess_return)s, %(vol_at_signal)s, %(threshold_used)s,
                %(direction_correct)s, %(threshold_correct)s, NOW())
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
        row,
    )
    return cur.rowcount


def _upsert_unresolved(cur, row: dict) -> int:
    cur.execute(
        """
        INSERT INTO prediction_outcomes
            (ticker, signal_date, horizon_days, price_at_signal,
             vol_at_signal, threshold_used)
        VALUES (%(ticker)s, %(signal_date)s, %(horizon_days)s, %(price_at_signal)s,
                %(vol_at_signal)s, %(threshold_used)s)
        ON CONFLICT (ticker, signal_date, horizon_days) DO NOTHING
        """,
        row,
    )
    return cur.rowcount


def _process_ticker(
    conn,
    ticker: str,
    predictions: list[dict],
    spy_by_date: dict[str, float],
    spy_dates: list[str],
) -> tuple[int, int, int]:
    """Returns (seeded_unresolved, resolved, quarantined)."""
    all_prices = _load_prices(conn, ticker)
    all_closes = [r["close"] for r in all_prices]
    date_to_idx = {r["date"]: i for i, r in enumerate(all_prices)}
    beta = _load_beta(conn, ticker)

    def _spy_after(sig_date: str) -> list[dict]:
        return [{"close": spy_by_date[d]} for d in spy_dates if d > sig_date]

    seeded = resolved = quarantined = 0

    with conn.cursor() as cur:
        for pred in predictions:
            signal_date = pred["signal_date"]
            bias = pred["bias"]
            base_price = pred["price_at_signal"]

            signal_idx = date_to_idx.get(signal_date)
            if signal_idx is None:
                continue

            future_prices = all_prices[signal_idx + 1 :]
            hist_closes = all_closes[: signal_idx + 1]
            vol = calc_v2.trailing_daily_vol(hist_closes, VOL_LOOKBACK)
            spy_future = _spy_after(signal_date)
            spy_signal_price = spy_by_date.get(signal_date)

            for h in HORIZONS:
                threshold = calc_v2.scaled_threshold(vol, h, K) if vol is not None else None

                if len(future_prices) < h:
                    seeded += _upsert_unresolved(
                        cur,
                        {
                            "ticker": ticker,
                            "signal_date": signal_date,
                            "horizon_days": h,
                            "price_at_signal": base_price,
                            "vol_at_signal": vol,
                            "threshold_used": threshold,
                        },
                    )
                    continue

                forward_window = future_prices[:h]

                if calc_v2.has_price_seam(forward_window, MAX_RATIO):
                    seeded += _upsert_unresolved(
                        cur,
                        {
                            "ticker": ticker,
                            "signal_date": signal_date,
                            "horizon_days": h,
                            "price_at_signal": base_price,
                            "vol_at_signal": vol,
                            "threshold_used": threshold,
                        },
                    )
                    quarantined += 1
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

                resolved += _upsert_outcome(
                    cur,
                    {
                        "ticker": ticker,
                        "signal_date": signal_date,
                        "horizon_days": h,
                        "price_at_signal": base_price,
                        "future_price": future_price,
                        "raw_return": raw_ret,
                        "benchmark_return": bench_ret,
                        "beta_used": beta,
                        "excess_return": excess,
                        "vol_at_signal": vol,
                        "threshold_used": threshold,
                        "direction_correct": direction_correct,
                        "threshold_correct": threshold_correct,
                    },
                )

        # Episode assignment for all outcome rows on this ticker
        cur.execute(
            """
            SELECT po.signal_date, sp.bias
            FROM prediction_outcomes po
            JOIN signal_predictions sp
              ON sp.ticker = po.ticker AND sp.signal_date = po.signal_date
            WHERE po.ticker = %s
            GROUP BY po.signal_date, sp.bias
            ORDER BY po.signal_date
            """,
            (ticker,),
        )
        episode_rows = cur.fetchall()
        if episode_rows:
            stamped = calc_v2.assign_episodes(
                [{"ticker": ticker, "signal_date": str(r[0]), "bias": r[1]} for r in episode_rows],
                EPISODE_GAP,
            )
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
    return seeded, resolved, quarantined


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill prediction_outcomes (v2) from signal_predictions history"
    )
    parser.add_argument(
        "--start", metavar="YYYY-MM-DD", help="Only process signals on/after this date"
    )
    args = parser.parse_args()

    load_env()

    conn = get_connection()

    print("Loading SPY prices...")
    spy_by_date, spy_dates = _load_spy(conn)
    print(f"  SPY: {len(spy_by_date)} trading days")

    tickers = _load_tickers(conn, args.start)
    start_msg = f" (from {args.start})" if args.start else " (full history)"
    print(f"\nOutcome v2 backfill{start_msg}: {len(tickers)} eligible tickers")
    print()

    total_seeded = total_resolved = total_quarantined = 0

    for i, ticker in enumerate(tickers, 1):
        predictions = _load_predictions(conn, ticker, args.start)
        if not predictions:
            continue

        seeded, resolved, quarantined = _process_ticker(
            conn, ticker, predictions, spy_by_date, spy_dates
        )
        total_seeded += seeded
        total_resolved += resolved
        total_quarantined += quarantined

        if i % 50 == 0 or i == len(tickers):
            print(
                f"  [{i:>4}/{len(tickers)}] {ticker:<16} "
                f"resolved={resolved:>4}  seeded={seeded:>4}  quarantined={quarantined:>3} | "
                f"running totals: resolved={total_resolved:>8,}  quarantined={total_quarantined:>6,}"
            )

    conn.close()

    print()
    print("Done.")
    print(f"  Resolved:     {total_resolved:,} outcome rows")
    print(f"  Unresolved:   {total_seeded:,} outcome rows (pending future data)")
    print(
        f"  Quarantined:  {total_quarantined:,} rows (price seam detected — re-run after re-backfill)"
    )
    print(f"  Horizons:     {HORIZONS}")


if __name__ == "__main__":
    main()
