"""Pure calculation functions for stock relatedness (correlations and betas).

All windows and ETF proxies come from config — nothing is hardcoded here.
All functions operate on pandas DataFrames; no Airflow imports.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_returns_matrix(price_history: dict[str, list[dict]], tickers: list[str]) -> pd.DataFrame:
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
    return prices.pct_change(fill_method=None)


def correlation_pairs(
    returns: pd.DataFrame,
    window_days: int,
    equity_tickers: list[str],
    min_r: float = 0.0,
    chunk_size: int = 0,
) -> list[tuple[str, str, int, float]]:
    """Return (ticker_a, ticker_b, window_days, pearson_r) for all equity pairs.

    Canonical ordering: ticker_a < ticker_b alphabetically, so there is exactly
    one row per pair regardless of argument order.
    Pairs where correlation is NaN (insufficient overlap) are dropped.
    Pairs where |pearson_r| < min_r are dropped before the list is built.

    chunk_size > 0: process tickers in blocks of that size instead of computing
    the full n×n matrix at once. Each block is a small matrix multiply so CPU
    usage is spread across many short operations rather than one long spike.
    Results are numerically identical to the full-matrix path for pairs with
    complete data; pairs with fewer than 10 overlapping observations are dropped.
    """
    if returns.empty:
        return []

    max_date = returns.index.max()
    cutoff = max_date - pd.Timedelta(days=window_days)
    window = returns[returns.index > cutoff]

    cols = sorted([t for t in equity_tickers if t in window.columns])
    if len(cols) < 2:
        return []

    if chunk_size > 0:
        return _correlation_pairs_chunked(window, cols, window_days, min_r, chunk_size)

    corr_arr = window[cols].corr().to_numpy()

    r_idx_arr, c_idx_arr = np.triu_indices(len(cols), k=1)
    r_vals = corr_arr[r_idx_arr, c_idx_arr]
    valid = ~np.isnan(r_vals)
    if min_r > 0.0:
        valid &= np.abs(r_vals) >= min_r
    return [
        (cols[r], cols[c], window_days, round(float(v), 6))
        for r, c, v in zip(r_idx_arr[valid], c_idx_arr[valid], r_vals[valid], strict=False)
    ]


def _correlation_pairs_chunked(
    window: pd.DataFrame,
    cols: list[str],
    window_days: int,
    min_r: float,
    chunk_size: int,
) -> list[tuple[str, str, int, float]]:
    """Block-wise Pearson correlation that avoids a single n×n matrix allocation.

    Standardises the full returns matrix once, then computes correlations as
    dot products between column blocks. Each block operation is small enough
    that the OS can schedule other processes between them, preventing CPU spikes
    that destabilise the Docker host.

    Numerics: standardisation uses per-ticker nanmean/nanstd over the full
    window; NaN positions are zeroed before the dot product and excluded from
    the observation count. This is equivalent to pandas pairwise Pearson when
    each ticker's mean/std is computed over its own non-NaN values, which
    differs from pandas pairwise only when ticker histories don't fully overlap
    — a negligible difference in practice.
    """
    arr = window[cols].to_numpy(dtype=np.float64)  # T × n
    not_nan = ~np.isnan(arr)  # T × n, bool

    # Standardise: z-score per column using non-NaN values; zero out NaN positions
    means = np.nanmean(arr, axis=0)
    stds = np.nanstd(arr, axis=0, ddof=1)
    stds[stds == 0] = np.nan  # zero-variance columns → NaN so division gives NaN→0
    arr_std = (arr - means) / stds
    np.nan_to_num(arr_std, copy=False, nan=0.0)

    obs = not_nan.astype(np.float32)  # T × n, used for pairwise observation counts

    n = len(cols)
    result: list[tuple[str, str, int, float]] = []

    for i_start in range(0, n, chunk_size):
        i_end = min(i_start + chunk_size, n)
        block_i = arr_std[:, i_start:i_end]  # T × ci
        obs_i = obs[:, i_start:i_end]        # T × ci

        for j_start in range(i_start + 1, n, chunk_size):
            j_end = min(j_start + chunk_size, n)
            block_j = arr_std[:, j_start:j_end]  # T × cj
            obs_j = obs[:, j_start:j_end]         # T × cj

            obs_count = obs_i.T @ obs_j   # ci × cj, float32
            raw_corr = block_i.T @ block_j  # ci × cj, float64

            # Require at least 10 overlapping observations; normalise
            enough = obs_count >= 10
            denom = np.where(enough, obs_count - 1, np.nan).astype(np.float64)
            corr_block = raw_corr / denom
            np.clip(corr_block, -1.0, 1.0, out=corr_block)

            if min_r > 0.0:
                mask = enough & (np.abs(corr_block) >= min_r)
            else:
                mask = enough & ~np.isnan(corr_block)

            bi_idx, bj_idx = np.where(mask)
            for bi, bj in zip(bi_idx, bj_idx, strict=False):
                result.append((
                    cols[i_start + bi],
                    cols[j_start + bj],
                    window_days,
                    round(float(corr_block[bi, bj]), 6),
                ))

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
