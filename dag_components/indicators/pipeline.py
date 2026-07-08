"""Pure (airflow-free) indicator pipeline helpers.

DB reads, VIX context assembly, per-ticker row computation, and batch upsert —
everything the nightly indicator task does *except* the Airflow plumbing. Kept
out of tasks.py so it imports without Airflow installed, which lets
scripts/verify_indicators_batch.py run the parity + peak-RSS gate against native
Postgres from WSL (Airflow only exists inside the container). The DAG task in
tasks.py is a thin wrapper over these functions, so the gate exercises the exact
code the pipeline runs.

All functions take a raw psycopg2 connection/cursor — no PostgresHook — so they
work identically in-container and out.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from psycopg2.extras import execute_values

from config import config
from dag_components.indicators import calculations as calc

_SIGNAL_INSERT = """
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
"""

_PRICE_QUERY = """
    SELECT ticker, date, open, high, low, close, volume
    FROM raw_prices
    WHERE ticker = ANY(%s)
      AND date BETWEEN %s AND %s
    ORDER BY ticker, date
"""


def rss_mb() -> float:
    """Resident set size of this process in MiB (psutil, falling back to /proc).

    Mirrors scripts/analyze_events.py._rss_mb so per-batch memory reporting is
    consistent with the event-study numbers. Returns NaN if RSS is unavailable
    (never raises — RSS logging must not be able to fail a batch).
    """
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        pass
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return float("nan")


def _scalar(series: pd.Series, key: str) -> float | None:
    """Return a float from a Series by label, or None if missing / NaN."""
    val = series.get(key)
    if val is None or pd.isna(val):
        return None
    return float(val)


class VixContext:
    """VIX/VVIX-derived scalars applied identically to every equity row.

    Computed once per run before the batch loop so a ticker's output never
    depends on which batch it landed in.
    """

    __slots__ = (
        "vix_close",
        "vix_mult",
        "vix_regime",
        "vix_trend",
        "vvix_close",
        "vol_environment",
    )

    def __init__(self) -> None:
        self.vix_close: float | None = None
        self.vix_mult: float | None = None
        self.vix_regime: str | None = None
        self.vix_trend: str | None = None
        self.vvix_close: float | None = None
        self.vol_environment: str | None = None


def read_prices(
    conn,
    tickers: list[str],
    start_dt: date,
    target_dt: date,
) -> dict[str, list[dict]]:
    """Read raw_prices for `tickers` into {ticker: [bar, ...]} on a raw connection.

    Scoped to a caller-supplied ticker slice so only one batch is ever resident.
    """
    with conn.cursor() as cur:
        cur.execute(_PRICE_QUERY, (tickers, start_dt, target_dt))
        rows = cur.fetchall()
    history: dict[str, list[dict]] = {}
    for ticker, dt, open_, high, low, close, volume in rows:
        history.setdefault(ticker, []).append(
            {
                "date": str(dt),
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": int(volume),
            }
        )
    return history


def build_vix_context(
    conn,
    vix_ticker: str,
    vvix_ticker: str,
    start_dt: date,
    target_dt: date,
    target_date: str,
) -> VixContext:
    """Derive the run-wide VIX/VVIX context from the two index series."""
    ctx = VixContext()
    history = read_prices(conn, [vix_ticker, vvix_ticker], start_dt, target_dt)

    vix_rows = history.get(vix_ticker, [])
    if vix_rows:
        vix_series = (
            pd.DataFrame(vix_rows).sort_values("date").set_index("date")["close"].astype(float)
        )
        ctx.vix_close = _scalar(vix_series, target_date)
        if ctx.vix_close is not None:
            ctx.vix_regime, ctx.vix_mult = calc.classify_vix_regime(ctx.vix_close)
            vix_sma_val = _scalar(calc.sma(vix_series, config.VIX_SMA_WINDOW), target_date)
            if vix_sma_val is not None:
                ctx.vix_trend = calc.classify_vix_trend(ctx.vix_close, vix_sma_val)

    vvix_rows = history.get(vvix_ticker, [])
    if vvix_rows:
        vvix_series = (
            pd.DataFrame(vvix_rows).sort_values("date").set_index("date")["close"].astype(float)
        )
        ctx.vvix_close = _scalar(vvix_series, target_date)
        if ctx.vvix_close is not None:
            ctx.vol_environment = calc.classify_vol_environment(ctx.vvix_close)

    return ctx


def compute_ticker_row(
    ticker: str,
    ticker_rows: list[dict],
    target_date: str,
    vix: VixContext,
) -> dict | None:
    """Compute one signal row for one ticker, or None if it has no data today.

    This is the exact per-ticker math the pre-batching compute_indicators ran;
    the parity gate calls it directly so batched output is identical to legacy.
    """
    if not ticker_rows:
        return None

    df = pd.DataFrame(ticker_rows).sort_values("date")
    close = df.set_index("date")["close"].astype(float)
    if target_date not in close.index:
        return None

    target_close = float(close[target_date])

    sma50_val = _scalar(calc.sma(close, config.SMA_SHORT_WINDOW), target_date)
    sma200_val = _scalar(calc.sma(close, config.SMA_LONG_WINDOW), target_date)

    macd_line, macd_signal_line, macd_hist_line = calc.macd(close)
    macd_val = _scalar(macd_line, target_date)
    macd_sig_val = _scalar(macd_signal_line, target_date)
    macd_hist_val = _scalar(macd_hist_line, target_date)

    rsi_val = _scalar(calc.rsi(close), target_date)

    bb_upper_series, bb_lower_series = calc.bollinger_bands(close)
    bb_upper_val = _scalar(bb_upper_series, target_date)
    bb_lower_val = _scalar(bb_lower_series, target_date)

    composite = calc.compute_composite(
        target_close, sma50_val, sma200_val, macd_val, macd_sig_val, rsi_val
    )
    composite_adj = None
    if composite is not None and vix.vix_mult is not None:
        composite_adj = round(composite * vix.vix_mult, 4)

    return {
        "ticker": ticker,
        "date": target_date,
        "sma_50": sma50_val,
        "sma_200": sma200_val,
        "macd": macd_val,
        "macd_signal": macd_sig_val,
        "macd_hist": macd_hist_val,
        "rsi_14": rsi_val,
        "bb_upper": bb_upper_val,
        "bb_lower": bb_lower_val,
        "vix_close": vix.vix_close,
        "vvix_close": vix.vvix_close,
        "vix_source": "eodhd",
        "vix_regime": vix.vix_regime,
        "vix_trend": vix.vix_trend,
        "vol_environment": vix.vol_environment,
        "composite_score": composite,
        "composite_vix_adj": composite_adj,
    }


def upsert_batch(cur, rows: list[dict]) -> None:
    """Upsert one batch of signal rows on an open cursor (caller commits)."""
    if not rows:
        return
    records = [
        (
            r["ticker"],
            r["date"],
            r["sma_50"],
            r["sma_200"],
            r["macd"],
            r["macd_signal"],
            r["macd_hist"],
            r["rsi_14"],
            r["bb_upper"],
            r["bb_lower"],
            r["vix_close"],
            r["vvix_close"],
            r["vix_source"],
            r["vix_regime"],
            r["vix_trend"],
            r["vol_environment"],
            r["composite_score"],
            r["composite_vix_adj"],
        )
        for r in rows
    ]
    execute_values(cur, _SIGNAL_INSERT, records, page_size=10_000)
