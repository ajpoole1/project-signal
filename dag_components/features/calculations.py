"""Pure calculation functions for Signal v2 feature layer (Phase 2) and
regime classification (Phase 3).

No Airflow imports. No DB calls. Input: a DataFrame with columns
[date, open, high, low, close, volume, rsi_14, macd_hist, bb_upper, bb_lower,
 sma_50, sma_200] sorted ascending by date for a single ticker.

Output: the same DataFrame with all feature columns appended.

All windows read from config — nothing hardcoded here.
All operations are vectorized pandas; no per-row Python loops.
"""

from __future__ import annotations

import pandas as pd

from config import config


def compute_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all Phase 2 + Phase 3 features for one ticker's history.

    Expects df sorted ascending by date with at minimum:
        close, high, low, volume, rsi_14, macd_hist, bb_upper, bb_lower,
        sma_50, sma_200

    Returns df with feature columns added. Existing columns are unchanged.
    NaN is expected for warmup rows and for features whose window has not
    yet filled — callers treat NULL as valid absence.
    """
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"].astype(float)
    sma50 = df["sma_50"]
    sma200 = df["sma_200"]
    rsi = df["rsi_14"]
    macd_hist = df["macd_hist"]
    bb_upper = df["bb_upper"]
    bb_lower = df["bb_lower"]

    # ------------------------------------------------------------------
    # Phase 2 features
    # ------------------------------------------------------------------

    # dist_sma50: (close - SMA50) / SMA50
    df["dist_sma50"] = (close - sma50) / sma50.replace(0, float("nan"))

    # dist_sma200: (close - SMA200) / SMA200
    df["dist_sma200"] = (close - sma200) / sma200.replace(0, float("nan"))

    # dist_sma200_z: z-score of dist_sma200 over trailing V2_FEATURE_SMA_LONG_Z_WINDOW
    z_window = config.V2_FEATURE_SMA_LONG_Z_WINDOW
    dist200 = df["dist_sma200"]
    roll_mean = dist200.rolling(z_window, min_periods=z_window).mean()
    roll_std = dist200.rolling(z_window, min_periods=z_window).std()
    df["dist_sma200_z"] = (dist200 - roll_mean) / roll_std.replace(0, float("nan"))

    # rsi_centered: (RSI - 50) / 50  → [-1, 1] range (RSI is [0,100])
    df["rsi_centered"] = (rsi - 50.0) / 50.0

    # bb_pctb: (close - bb_lower) / (bb_upper - bb_lower)
    bb_width = (bb_upper - bb_lower).replace(0, float("nan"))
    df["bb_pctb"] = (close - bb_lower) / bb_width

    # macd_hist_norm: macd_hist / close (price-scale-free)
    df["macd_hist_norm"] = macd_hist / close.replace(0, float("nan"))

    # dist_52w_high: close / rolling 252d max(high) - 1  (always <= 0)
    w52 = config.V2_FEATURE_52W_WINDOW
    roll_high = high.rolling(w52, min_periods=w52).max()
    df["dist_52w_high"] = (close / roll_high.replace(0, float("nan"))) - 1.0

    # dist_52w_low: close / rolling 252d min(low) - 1  (always >= 0)
    roll_low = low.rolling(w52, min_periods=w52).min()
    df["dist_52w_low"] = (close / roll_low.replace(0, float("nan"))) - 1.0

    # vol_ratio: volume / SMA20(volume)
    vol_sma = volume.rolling(
        config.V2_FEATURE_VOL_SMA_WINDOW, min_periods=config.V2_FEATURE_VOL_SMA_WINDOW
    ).mean()
    df["vol_ratio"] = volume / vol_sma.replace(0, float("nan"))

    # ret_21d: 21d trailing pct return
    df["ret_21d"] = close.pct_change(config.V2_FEATURE_RET_MED_WINDOW, fill_method=None)

    # ret_63d: 63d trailing pct return
    df["ret_63d"] = close.pct_change(config.V2_FEATURE_RET_LONG_WINDOW, fill_method=None)

    # ------------------------------------------------------------------
    # Phase 3 features
    # ------------------------------------------------------------------

    # ADX(14) — Wilder smoothing, consistent with RSI implementation style
    df["adx_14"] = _adx(high, low, close, config.V2_ADX_WINDOW)

    # sma200_slope: 63d pct change of the SMA200 series itself
    df["sma200_slope"] = sma200.pct_change(config.V2_SLOPE_WINDOW, fill_method=None)

    # ticker_regime: 'trending' if ADX >= threshold OR |sma200_slope| >= threshold
    adx = df["adx_14"]
    slope = df["sma200_slope"]
    is_trending = (adx >= config.V2_ADX_TREND_MIN) | (slope.abs() >= config.V2_SLOPE_TREND_MIN)
    df["ticker_regime"] = is_trending.map({True: "trending", False: "ranging"})
    # NaN in adx or slope → regime is NaN (not enough history)
    regime_null_mask = adx.isna() | slope.isna()
    df.loc[regime_null_mask, "ticker_regime"] = None

    # trend_direction: 'up' | 'down' | None
    # Only set when ticker_regime == 'trending'; direction from slope sign
    df["trend_direction"] = None
    trending_mask = df["ticker_regime"] == "trending"
    df.loc[trending_mask & (slope > 0), "trend_direction"] = "up"
    df.loc[trending_mask & (slope < 0), "trend_direction"] = "down"

    return df


def _adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int,
) -> pd.Series:
    """Average Directional Index using Wilder smoothing.

    Returns a Series of ADX values (0–100 range, NaN during warmup).
    Warmup requires 2*window rows before producing a valid value.
    """
    # True range
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(
        axis=1
    )

    # Directional movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(0.0, index=high.index)
    minus_dm = pd.Series(0.0, index=high.index)
    plus_cond = (up_move > down_move) & (up_move > 0)
    minus_cond = (down_move > up_move) & (down_move > 0)
    plus_dm[plus_cond] = up_move[plus_cond]
    minus_dm[minus_cond] = down_move[minus_cond]

    # Wilder smoothing (alpha = 1/window, same as RSI implementation)
    alpha = 1.0 / window
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    smooth_plus = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    smooth_minus = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    # Directional indicators
    atr_safe = atr.replace(0, float("nan"))
    plus_di = 100.0 * smooth_plus / atr_safe
    minus_di = 100.0 * smooth_minus / atr_safe

    # DX and ADX
    di_sum = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    # Null out warmup rows (need at least 2*window rows for ADX to stabilise)
    warmup = 2 * window
    adx.iloc[:warmup] = float("nan")

    return adx


# Expose window requirement so callers can determine minimum history needed
def min_history_required() -> int:
    """Minimum number of rows needed before any feature is non-null."""
    return max(
        config.V2_FEATURE_SMA_LONG,  # 200 — dist_sma200
        config.V2_FEATURE_52W_WINDOW,  # 252 — dist_52w_high/low
        config.V2_FEATURE_SMA_LONG_Z_WINDOW,  # 252 — dist_sma200_z
        config.V2_FEATURE_RET_LONG_WINDOW,  # 63  — ret_63d
        2 * config.V2_ADX_WINDOW,  # 28  — adx_14
        config.V2_SLOPE_WINDOW + config.V2_FEATURE_SMA_LONG,  # 63+200 — sma200_slope
    )
