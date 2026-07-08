"""
Backfill signal_features (Phase 2+3) over the full raw_prices + stock_signals history.

For each ticker, loads the complete price and indicator history once, computes all
feature rolling series in a single pandas pass, then writes one signal_features row
per trading date. Re-runnable — all writes are idempotent (ON CONFLICT DO UPDATE).

Reports NULL rates per feature column at the end so warmup behaviour is visible.

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_features.py
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_features.py --start 2022-01-01
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.watchlist import get_all_tickers
from dag_components.features import calculations as calc
from scripts._db import get_connection, load_env


def _load_prices(conn, ticker: str, start_date: str | None) -> pd.DataFrame:
    with conn.cursor() as cur:
        if start_date:
            cur.execute(
                """
                SELECT date, open, high, low, close, volume
                FROM raw_prices WHERE ticker = %s AND date >= %s ORDER BY date
                """,
                (ticker, start_date),
            )
        else:
            cur.execute(
                "SELECT date, open, high, low, close, volume FROM raw_prices "
                "WHERE ticker = %s ORDER BY date",
                (ticker,),
            )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_signals(conn, ticker: str, start_date: str | None) -> pd.DataFrame:
    with conn.cursor() as cur:
        if start_date:
            cur.execute(
                """
                SELECT date, sma_50, sma_200, macd_hist, rsi_14, bb_upper, bb_lower
                FROM stock_signals WHERE ticker = %s AND date >= %s ORDER BY date
                """,
                (ticker, start_date),
            )
        else:
            cur.execute(
                """
                SELECT date, sma_50, sma_200, macd_hist, rsi_14, bb_upper, bb_lower
                FROM stock_signals WHERE ticker = %s ORDER BY date
                """,
                (ticker,),
            )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows, columns=["date", "sma_50", "sma_200", "macd_hist", "rsi_14", "bb_upper", "bb_lower"]
    )
    for col in ["sma_50", "sma_200", "macd_hist", "rsi_14", "bb_upper", "bb_lower"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _upsert_features(conn, ticker: str, feat_df: pd.DataFrame) -> int:
    def _f(val):
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    def _s(val):
        return None if (val is None or (isinstance(val, float) and math.isnan(val))) else str(val)

    rows = []
    for _, row in feat_df.iterrows():
        rows.append(
            (
                ticker,
                str(row["date"]),
                _f(row.get("dist_sma50")),
                _f(row.get("dist_sma200")),
                _f(row.get("dist_sma200_z")),
                _f(row.get("rsi_centered")),
                _f(row.get("bb_pctb")),
                _f(row.get("macd_hist_norm")),
                _f(row.get("dist_52w_high")),
                _f(row.get("dist_52w_low")),
                _f(row.get("vol_ratio")),
                _f(row.get("ret_21d")),
                _f(row.get("ret_63d")),
                _f(row.get("adx_14")),
                _f(row.get("sma200_slope")),
                _s(row.get("ticker_regime")),
                _s(row.get("trend_direction")),
            )
        )

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO signal_features
                (ticker, date,
                 dist_sma50, dist_sma200, dist_sma200_z, rsi_centered, bb_pctb,
                 macd_hist_norm, dist_52w_high, dist_52w_low, vol_ratio,
                 ret_21d, ret_63d,
                 adx_14, sma200_slope, ticker_regime, trend_direction)
            VALUES %s
            ON CONFLICT (ticker, date) DO UPDATE SET
                dist_sma50      = EXCLUDED.dist_sma50,
                dist_sma200     = EXCLUDED.dist_sma200,
                dist_sma200_z   = EXCLUDED.dist_sma200_z,
                rsi_centered    = EXCLUDED.rsi_centered,
                bb_pctb         = EXCLUDED.bb_pctb,
                macd_hist_norm  = EXCLUDED.macd_hist_norm,
                dist_52w_high   = EXCLUDED.dist_52w_high,
                dist_52w_low    = EXCLUDED.dist_52w_low,
                vol_ratio       = EXCLUDED.vol_ratio,
                ret_21d         = EXCLUDED.ret_21d,
                ret_63d         = EXCLUDED.ret_63d,
                adx_14          = EXCLUDED.adx_14,
                sma200_slope    = EXCLUDED.sma200_slope,
                ticker_regime   = EXCLUDED.ticker_regime,
                trend_direction = EXCLUDED.trend_direction
            """,
            rows,
        )
        return cur.rowcount


FEATURE_COLS = [
    "dist_sma50",
    "dist_sma200",
    "dist_sma200_z",
    "rsi_centered",
    "bb_pctb",
    "macd_hist_norm",
    "dist_52w_high",
    "dist_52w_low",
    "vol_ratio",
    "ret_21d",
    "ret_63d",
    "adx_14",
    "sma200_slope",
    "ticker_regime",
    "trend_direction",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill signal_features from full history")
    parser.add_argument("--start", metavar="YYYY-MM-DD", help="Only backfill from this date")
    args = parser.parse_args()

    load_env()

    conn = get_connection()
    tickers = get_all_tickers()
    start_msg = f" (from {args.start})" if args.start else " (full history)"
    print(f"Feature backfill{start_msg}: {len(tickers)} tickers")
    print()

    total_rows = 0
    skipped = 0
    null_counts: dict[str, int] = dict.fromkeys(FEATURE_COLS, 0)
    total_feature_rows = 0

    for i, ticker in enumerate(tickers, 1):
        pdf = _load_prices(conn, ticker, args.start)
        sdf = _load_signals(conn, ticker, args.start)

        if pdf.empty or sdf.empty:
            skipped += 1
            continue

        # Merge on date — use inner join so only dates with both price and signal exist
        pdf["date"] = pd.to_datetime(pdf["date"]).dt.strftime("%Y-%m-%d")
        sdf["date"] = pd.to_datetime(sdf["date"]).dt.strftime("%Y-%m-%d")
        merged = pdf.merge(sdf, on="date", how="inner").sort_values("date").reset_index(drop=True)

        if merged.empty:
            skipped += 1
            continue

        try:
            feat_df = calc.compute_feature_frame(merged)
        except Exception as exc:
            print(f"  WARN {ticker}: compute failed — {exc}")
            skipped += 1
            continue

        written = _upsert_features(conn, ticker, feat_df)
        conn.commit()
        total_rows += written

        # Accumulate null stats
        for col in FEATURE_COLS:
            if col in feat_df.columns:
                null_counts[col] += int(feat_df[col].isna().sum())
        total_feature_rows += len(feat_df)

        if i % 100 == 0 or i == len(tickers):
            print(
                f"  [{i:>4}/{len(tickers)}] {ticker:<16}  rows={written:>5}  running_total={total_rows:>8,}"
            )

    conn.close()

    print()
    print("Done.")
    print(f"  Written:  {total_rows:,} rows across {len(tickers) - skipped} tickers")
    print(f"  Skipped:  {skipped} tickers (no price or signal data)")
    print()
    print("NULL rates per feature (warmup-only NULLs expected for long-window features):")
    if total_feature_rows > 0:
        for col in FEATURE_COLS:
            rate = null_counts.get(col, 0) / total_feature_rows
            print(f"  {col:<20} {rate:>6.1%}")


if __name__ == "__main__":
    main()
