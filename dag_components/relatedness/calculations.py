"""Pure calculation functions for stock relatedness (correlations and betas).

All windows and ETF proxies come from config — nothing is hardcoded here.
All functions operate on pandas DataFrames; no Airflow imports.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_returns_matrix(
    price_history: dict[str, list[dict]], tickers: list[str]
) -> pd.DataFrame:
    """Build a daily-returns DataFrame (columns=tickers, rows=DatetimeIndex).

    Uses close prices only. NaN where a ticker has no data on a given date.
    """
    series: dict[str, pd.Series] = {}
    for ticker in tickers:
        rows = price_history.get(ticker)
        if not rows:
            continue
        df = pd.DataFrame(rows)[["date", "close"]]
        s = df.set_index("date")["close"].astype(float)
        s.index = pd.to_datetime(s.index)
        series[ticker] = s.sort_index()

    if not series:
        return pd.DataFrame()

    prices = pd.DataFrame(series).sort_index()
    return prices.pct_change()


def correlation_pairs(
    returns: pd.DataFrame,
    window_days: int,
    equity_tickers: list[str],
) -> list[tuple[str, str, int, float]]:
    """Return (ticker_a, ticker_b, window_days, pearson_r) for all equity pairs.

    Canonical ordering: ticker_a < ticker_b alphabetically, so there is exactly
    one row per pair regardless of argument order.
    Pairs where correlation is NaN (insufficient overlap) are dropped.
    """
    if returns.empty:
        return []

    max_date = returns.index.max()
    cutoff = max_date - pd.Timedelta(days=window_days)
    window = returns[returns.index > cutoff]

    cols = sorted([t for t in equity_tickers if t in window.columns])
    if len(cols) < 2:
        return []

    corr = window[cols].corr()

    r_idx_arr, c_idx_arr = np.triu_indices(len(cols), k=1)
    result: list[tuple[str, str, int, float]] = []
    for r_idx, c_idx in zip(r_idx_arr, c_idx_arr, strict=False):
        r_val = corr.iat[r_idx, c_idx]
        if pd.notna(r_val):
            result.append((cols[r_idx], cols[c_idx], window_days, round(float(r_val), 6)))
    return result


def beta_values(
    returns: pd.DataFrame,
    equity_tickers: list[str],
    etf_tickers: list[str],
    window_days: int,
) -> list[tuple[str, str, int, float]]:
    """Return (ticker, etf, window_days, beta) for all equity × ETF pairs.

    beta_i = cov(r_i, r_etf) / var(r_etf)
    Pairs with fewer than 10 aligned observations are dropped.
    """
    if returns.empty:
        return []

    max_date = returns.index.max()
    cutoff = max_date - pd.Timedelta(days=window_days)
    window = returns[returns.index > cutoff]

    etf_cols = [e for e in etf_tickers if e in window.columns]
    equity_cols = [t for t in equity_tickers if t in window.columns]

    result: list[tuple[str, str, int, float]] = []
    for etf in etf_cols:
        etf_series = window[etf].dropna()
        if len(etf_series) < 10:
            continue
        etf_var = float(etf_series.var())
        if etf_var == 0 or np.isnan(etf_var):
            continue

        # Compute cov(each equity, etf) / var(etf) via the covariance matrix.
        # DataFrame.cov() uses pairwise-complete observations — handles NaN gaps.
        subset_cols = equity_cols + [etf]
        cov_matrix = window[subset_cols].cov()
        if etf not in cov_matrix.columns:
            continue

        cov_with_etf = cov_matrix[etf].drop(etf)
        for ticker in equity_cols:
            if ticker not in cov_with_etf.index:
                continue
            cov_val = cov_with_etf[ticker]
            if pd.notna(cov_val):
                result.append((ticker, etf, window_days, round(float(cov_val / etf_var), 6)))

    return result
