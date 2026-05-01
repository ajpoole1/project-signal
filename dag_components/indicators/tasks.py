"""Task implementations for dag_stock_indicators."""

from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook
from psycopg2.extras import execute_values

from config import config
from config.watchlist import get_all_tickers
from dag_components.indicators import calculations as calc
from plugins.routing import resolve_vix_tickers

log = logging.getLogger(__name__)


def _scalar(series: pd.Series, key: str) -> float | None:
    """Return a float from a Series by label, or None if missing / NaN."""
    val = series.get(key)
    if val is None or pd.isna(val):
        return None
    return float(val)


@task()
def fetch_price_history() -> dict[str, list[dict]]:
    """Read raw_prices for all tickers over the indicator lookback window."""
    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    start_dt = target_dt - timedelta(days=config.PRICE_HISTORY_DAYS + 30)

    vix_ticker, vvix_ticker = resolve_vix_tickers()
    tickers = get_all_tickers() + [vix_ticker, vvix_ticker]

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    rows = hook.get_records(
        """
        SELECT ticker, date, open, high, low, close, volume
        FROM raw_prices
        WHERE ticker = ANY(%s)
          AND date BETWEEN %s AND %s
        ORDER BY ticker, date
        """,
        parameters=(tickers, start_dt, target_dt),
    )

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

    log.info(
        "Loaded history for %d tickers between %s and %s",
        len(history),
        start_dt,
        target_dt,
    )
    return history


@task()
def compute_indicators(price_history: dict[str, list[dict]]) -> list[dict]:
    """Compute all technical indicators and produce one signal row per ticker."""
    context = get_current_context()
    target_date = str(context["data_interval_start"].date())

    vix_ticker, vvix_ticker = resolve_vix_tickers()
    equity_tickers = get_all_tickers()

    # --- VIX context (applied to every equity row) --------------------------
    vix_close = vix_mult = vix_regime = vix_trend = None
    vvix_close = vol_environment = None

    vix_rows = price_history.get(vix_ticker, [])
    if vix_rows:
        vix_df = pd.DataFrame(vix_rows).sort_values("date")
        vix_series = vix_df.set_index("date")["close"].astype(float)
        vix_close = _scalar(vix_series, target_date)
        if vix_close is not None:
            vix_regime, vix_mult = calc.classify_vix_regime(vix_close)
            vix_sma_series = calc.sma(vix_series, config.VIX_SMA_WINDOW)
            vix_sma_val = _scalar(vix_sma_series, target_date)
            if vix_sma_val is not None:
                vix_trend = calc.classify_vix_trend(vix_close, vix_sma_val)

    vvix_rows = price_history.get(vvix_ticker, [])
    if vvix_rows:
        vvix_df = pd.DataFrame(vvix_rows).sort_values("date")
        vvix_series = vvix_df.set_index("date")["close"].astype(float)
        vvix_close = _scalar(vvix_series, target_date)
        if vvix_close is not None:
            vol_environment = calc.classify_vol_environment(vvix_close)

    # --- Per-ticker indicator calculation ------------------------------------
    signal_rows: list[dict] = []

    for ticker in equity_tickers:
        ticker_rows = price_history.get(ticker, [])
        if not ticker_rows:
            log.info("%s: no price history, skipping", ticker)
            continue

        df = pd.DataFrame(ticker_rows).sort_values("date")
        close = df.set_index("date")["close"].astype(float)

        if target_date not in close.index:
            log.info("%s: no data for %s, skipping", ticker, target_date)
            continue

        target_close = float(close[target_date])

        # SMA
        sma50_series = calc.sma(close, config.SMA_SHORT_WINDOW)
        sma200_series = calc.sma(close, config.SMA_LONG_WINDOW)
        sma50_val = _scalar(sma50_series, target_date)
        sma200_val = _scalar(sma200_series, target_date)

        # MACD
        macd_line, macd_signal_line, macd_hist_line = calc.macd(close)
        macd_val = _scalar(macd_line, target_date)
        macd_sig_val = _scalar(macd_signal_line, target_date)
        macd_hist_val = _scalar(macd_hist_line, target_date)

        # RSI
        rsi_series = calc.rsi(close)
        rsi_val = _scalar(rsi_series, target_date)

        # Bollinger Bands
        bb_upper_series, bb_lower_series = calc.bollinger_bands(close)
        bb_upper_val = _scalar(bb_upper_series, target_date)
        bb_lower_val = _scalar(bb_lower_series, target_date)

        # Composite
        composite = calc.compute_composite(
            target_close, sma50_val, sma200_val, macd_val, macd_sig_val, rsi_val
        )
        composite_adj = None
        if composite is not None and vix_mult is not None:
            composite_adj = round(composite * vix_mult, 4)

        signal_rows.append(
            {
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
                "vix_close": vix_close,
                "vvix_close": vvix_close,
                "vix_source": config.VIX_SOURCE,
                "vix_regime": vix_regime,
                "vix_trend": vix_trend,
                "vol_environment": vol_environment,
                "composite_score": composite,
                "composite_vix_adj": composite_adj,
            }
        )

    log.info("Computed indicators for %d tickers on %s", len(signal_rows), target_date)
    return signal_rows


@task()
def upsert_stock_signals(rows: list[dict]) -> None:
    if not rows:
        log.info("No signal rows to upsert.")
        return

    hook = PostgresHook(postgres_conn_id="signal_postgres")
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

    conn = hook.get_conn()
    cur = conn.cursor()
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
        records,
    )
    conn.commit()
    cur.close()
    log.info("Upserted %d rows to stock_signals", len(records))
