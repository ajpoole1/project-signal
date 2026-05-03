"""
Backfill stock_signals over the full raw_prices history.

For each ticker, loads the complete price history once, computes all indicator
rolling series in a single pandas pass, then writes one stock_signals row per
trading date. Re-runnable — all writes are idempotent (ON CONFLICT DO UPDATE).

Indicators computed:
  SMA 50 / SMA 200, MACD + signal + histogram, RSI 14, Bollinger Bands (upper/lower),
  VIX regime, VIX trend, VVIX vol environment, composite_score, composite_vix_adj.

Warmup NULLs are expected and handled: composite_score normalises by the
weight of indicators that are valid, so every date gets a score.

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_indicators.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from config.watchlist import get_all_tickers
from dag_components.indicators import calculations as calc
from plugins.routing import resolve_vix_tickers


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


def _f(series: pd.Series, i: int) -> float | None:
    """Extract a float from position i, returning None for NaN."""
    v = series.iloc[i]
    return float(v) if pd.notna(v) else None


def _load_close(conn, ticker: str) -> pd.Series:
    """Load close prices as a DatetimeIndex Series, ordered ascending."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, close FROM raw_prices WHERE ticker = %s ORDER BY date",
            (ticker,),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        [float(r[1]) for r in rows],
        index=pd.to_datetime([r[0] for r in rows]),
    )


def _load_vix_context(conn) -> dict[str, dict[str, float]]:
    """
    Load VIX and VVIX close series plus VIX 20-day SMA, keyed by date string.
    Returns {"vix_close": {...}, "vvix_close": {...}, "vix_sma": {...}}.
    """
    vix_ticker, vvix_ticker = resolve_vix_tickers()
    vix_close = _load_close(conn, vix_ticker)
    vvix_close = _load_close(conn, vvix_ticker)
    vix_sma = calc.sma(vix_close, config.VIX_SMA_WINDOW)

    def _to_dict(s: pd.Series) -> dict[str, float]:
        return {str(dt.date()): float(v) for dt, v in s.items() if pd.notna(v)}

    return {
        "vix_close": _to_dict(vix_close),
        "vvix_close": _to_dict(vvix_close),
        "vix_sma": _to_dict(vix_sma),
    }


def _compute_rows(ticker: str, close: pd.Series, vix_ctx: dict) -> list[tuple]:
    """
    Compute all indicators over the full price history and return a list of
    tuples ready for execute_values insertion into stock_signals.
    """
    sma50 = calc.sma(close, config.SMA_SHORT_WINDOW)
    sma200 = calc.sma(close, config.SMA_LONG_WINDOW)
    macd_line, macd_sig_line, macd_hist_line = calc.macd(close)
    rsi_series = calc.rsi(close)
    bb_upper_series, bb_lower_series = calc.bollinger_bands(close)

    rows = []
    for i in range(len(close)):
        dt = close.index[i]
        date_str = str(dt.date())
        close_val = float(close.iloc[i])

        sma50_v = _f(sma50, i)
        sma200_v = _f(sma200, i)
        macd_v = _f(macd_line, i)
        macd_sig_v = _f(macd_sig_line, i)
        macd_hist_v = _f(macd_hist_line, i)
        rsi_v = _f(rsi_series, i)
        bb_upper_v = _f(bb_upper_series, i)
        bb_lower_v = _f(bb_lower_series, i)

        # VIX context — look up by date string
        vix_close_v = vix_ctx["vix_close"].get(date_str)
        vvix_close_v = vix_ctx["vvix_close"].get(date_str)
        vix_sma_v = vix_ctx["vix_sma"].get(date_str)

        vix_regime = vix_trend = vix_mult = None
        vol_environment = None

        if vix_close_v is not None:
            vix_regime, vix_mult = calc.classify_vix_regime(vix_close_v)
            if vix_sma_v is not None:
                vix_trend = calc.classify_vix_trend(vix_close_v, vix_sma_v)

        if vvix_close_v is not None:
            vol_environment = calc.classify_vol_environment(vvix_close_v)

        composite = calc.compute_composite(close_val, sma50_v, sma200_v, macd_v, macd_sig_v, rsi_v)
        composite_adj = (
            round(composite * vix_mult, 4)
            if composite is not None and vix_mult is not None
            else None
        )

        rows.append(
            (
                ticker,
                date_str,
                sma50_v,
                sma200_v,
                macd_v,
                macd_sig_v,
                macd_hist_v,
                rsi_v,
                bb_upper_v,
                bb_lower_v,
                vix_close_v,
                vvix_close_v,
                "eodhd",
                vix_regime,
                vix_trend,
                vol_environment,
                composite,
                composite_adj,
            )
        )
    return rows


def _upsert(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO stock_signals (
                ticker, date,
                sma_50, sma_200, macd, macd_signal, macd_hist, rsi_14,
                bb_upper, bb_lower,
                vix_close, vvix_close, vix_source,
                vix_regime, vix_trend, vol_environment,
                composite_score, composite_vix_adj
            ) VALUES %s
            ON CONFLICT (ticker, date) DO UPDATE SET
                sma_50            = EXCLUDED.sma_50,
                sma_200           = EXCLUDED.sma_200,
                macd              = EXCLUDED.macd,
                macd_signal       = EXCLUDED.macd_signal,
                macd_hist         = EXCLUDED.macd_hist,
                rsi_14            = EXCLUDED.rsi_14,
                bb_upper          = EXCLUDED.bb_upper,
                bb_lower          = EXCLUDED.bb_lower,
                vix_close         = EXCLUDED.vix_close,
                vvix_close        = EXCLUDED.vvix_close,
                vix_source        = EXCLUDED.vix_source,
                vix_regime        = EXCLUDED.vix_regime,
                vix_trend         = EXCLUDED.vix_trend,
                vol_environment   = EXCLUDED.vol_environment,
                composite_score   = EXCLUDED.composite_score,
                composite_vix_adj = EXCLUDED.composite_vix_adj
            """,
            rows,
        )
    conn.commit()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    _load_env(root / ".env")

    tickers = get_all_tickers()
    print(f"Indicator backfill: {len(tickers)} tickers")
    print()

    conn = _get_connection()

    print("Loading VIX/VVIX context...")
    vix_ctx = _load_vix_context(conn)
    print(f"  VIX dates: {len(vix_ctx['vix_close'])}  VVIX dates: {len(vix_ctx['vvix_close'])}")
    print()

    total_rows = 0
    skipped = 0

    for i, ticker in enumerate(tickers, 1):
        close = _load_close(conn, ticker)
        if close.empty:
            print(f"  [{i:>3}/{len(tickers)}] {ticker:<16} no price data, skipping")
            skipped += 1
            continue

        rows = _compute_rows(ticker, close, vix_ctx)
        _upsert(conn, rows)
        total_rows += len(rows)

        if i % 50 == 0 or i == len(tickers):
            print(
                f"  [{i:>3}/{len(tickers)}] {ticker:<16} {len(rows):>5} rows  (running total: {total_rows:,})"
            )

    conn.close()
    print()
    print(f"Done. {total_rows:,} rows upserted across {len(tickers) - skipped} tickers.")


if __name__ == "__main__":
    main()
