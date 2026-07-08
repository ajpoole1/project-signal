"""Task implementations for the Signal v2 feature layer (Phases 2 + 3)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook

from config import config
from config.watchlist import get_all_tickers
from dag_components.features import calculations as calc

log = logging.getLogger(__name__)

# Minimum history rows required before any feature is non-null.
# Pull the full SMA200+252 window + 30-day buffer.
_HISTORY_DAYS = config.V2_FEATURE_SMA_LONG + config.V2_FEATURE_SMA_LONG_Z_WINDOW + 30


@task()
def compute_features() -> int:
    """Compute Phase 2+3 features for all tickers for the execution date.

    Reads raw_prices + stock_signals for each ticker, runs compute_feature_frame,
    and upserts the target date's row into signal_features.

    Returns the number of feature rows written.
    """
    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    target_str = str(target_dt)
    start_dt = target_dt - timedelta(days=_HISTORY_DAYS)

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    tickers = get_all_tickers()

    # Load OHLCV history for all tickers in one query
    price_rows = hook.get_records(
        """
        SELECT ticker, date, open, high, low, close, volume
        FROM raw_prices
        WHERE ticker = ANY(%s) AND date BETWEEN %s AND %s
        ORDER BY ticker, date
        """,
        parameters=(tickers, start_dt, target_dt),
    )

    # Load indicator history (rsi_14, macd_hist, bb_upper, bb_lower, sma_50, sma_200)
    signal_rows = hook.get_records(
        """
        SELECT ticker, date, sma_50, sma_200, macd_hist, rsi_14, bb_upper, bb_lower
        FROM stock_signals
        WHERE ticker = ANY(%s) AND date BETWEEN %s AND %s
        ORDER BY ticker, date
        """,
        parameters=(tickers, start_dt, target_dt),
    )

    # Build per-ticker DataFrames
    price_by_ticker: dict[str, list] = {}
    for ticker, dt, o, h, lo, c, v in price_rows:
        price_by_ticker.setdefault(ticker, []).append(
            {
                "date": str(dt),
                "open": float(o),
                "high": float(h),
                "low": float(lo),
                "close": float(c),
                "volume": int(v),
            }
        )

    sig_by_ticker: dict[str, list] = {}
    for ticker, dt, sma50, sma200, mh, rsi, bbu, bbl in signal_rows:
        sig_by_ticker.setdefault(ticker, []).append(
            {
                "date": str(dt),
                "sma_50": float(sma50) if sma50 is not None else None,
                "sma_200": float(sma200) if sma200 is not None else None,
                "macd_hist": float(mh) if mh is not None else None,
                "rsi_14": float(rsi) if rsi is not None else None,
                "bb_upper": float(bbu) if bbu is not None else None,
                "bb_lower": float(bbl) if bbl is not None else None,
            }
        )

    conn = hook.get_conn()
    written = 0

    for ticker in tickers:
        prices = price_by_ticker.get(ticker)
        signals = sig_by_ticker.get(ticker)
        if not prices or not signals:
            continue

        df = _build_df(prices, signals)
        if df is None or df.empty:
            continue

        try:
            feat_df = calc.compute_feature_frame(df)
        except Exception as exc:
            log.warning("%s: feature computation failed — %s", ticker, exc)
            continue

        target_row = feat_df[feat_df["date"] == target_str]
        if target_row.empty:
            continue

        row = target_row.iloc[0]
        written += _upsert_row(conn, ticker, target_str, row)

    conn.commit()
    log.info("compute_features: wrote %d signal_features rows for %s", written, target_dt)
    return written


def _build_df(prices: list[dict], signals: list[dict]) -> pd.DataFrame | None:
    """Merge price and signal lists into a single date-aligned DataFrame."""
    pdf = pd.DataFrame(prices).set_index("date")
    sdf = pd.DataFrame(signals).set_index("date")
    merged = pdf.join(sdf, how="inner")
    if merged.empty:
        return None
    merged = merged.reset_index().rename(columns={"index": "date"})
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged


def _upsert_row(conn, ticker: str, date_str: str, row: pd.Series) -> int:
    def _v(col: str):
        v = row.get(col)
        if v is None or (isinstance(v, float) and (v != v)):  # NaN check
            return None
        return float(v)

    def _s(col: str):
        v = row.get(col)
        if v is None:
            return None
        if isinstance(v, float) and pd.isna(v):
            return None
        return str(v)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signal_features
                (ticker, date,
                 dist_sma50, dist_sma200, dist_sma200_z, rsi_centered, bb_pctb,
                 macd_hist_norm, dist_52w_high, dist_52w_low, vol_ratio,
                 ret_21d, ret_63d,
                 adx_14, sma200_slope, ticker_regime, trend_direction)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            (
                ticker,
                date_str,
                _v("dist_sma50"),
                _v("dist_sma200"),
                _v("dist_sma200_z"),
                _v("rsi_centered"),
                _v("bb_pctb"),
                _v("macd_hist_norm"),
                _v("dist_52w_high"),
                _v("dist_52w_low"),
                _v("vol_ratio"),
                _v("ret_21d"),
                _v("ret_63d"),
                _v("adx_14"),
                _v("sma200_slope"),
                row.get("ticker_regime"),
                row.get("trend_direction"),
            ),
        )
        return cur.rowcount
