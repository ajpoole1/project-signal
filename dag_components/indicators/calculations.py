"""Pure calculation functions for stock technical indicators.

All windows and thresholds are read from config — nothing is hardcoded here.
All functions operate on plain Python lists or pandas Series; no pandas-ta dependency.
"""

from __future__ import annotations

import pandas as pd

from config import config

# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram) using config windows."""
    fast = ema(close, config.MACD_FAST)
    slow = ema(close, config.MACD_SLOW)
    macd_line = fast - slow
    signal_line = ema(macd_line, config.MACD_SIGNAL)
    return macd_line, signal_line, macd_line - signal_line


def rsi(close: pd.Series) -> pd.Series:
    """Wilder RSI using exponential smoothing (alpha = 1 / window)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    alpha = 1.0 / config.RSI_WINDOW
    avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()
    # avg_loss == 0 → rs = inf → RSI = 100 (no losses, fully overbought)
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger_bands(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (upper_band, lower_band) using config BB_WINDOW and BB_STD."""
    mid = sma(close, config.BB_WINDOW)
    std = close.rolling(window=config.BB_WINDOW, min_periods=config.BB_WINDOW).std()
    return mid + config.BB_STD * std, mid - config.BB_STD * std


# ---------------------------------------------------------------------------
# VIX / VVIX classification
# ---------------------------------------------------------------------------


def classify_vix_regime(vix_close: float) -> tuple[str, float]:
    """Return (regime_label, multiplier) for composite score adjustment."""
    for upper, label, multiplier in config.VIX_REGIMES:
        if vix_close < upper:
            return label, multiplier
    last = config.VIX_REGIMES[-1]
    return last[1], last[2]


def classify_vix_trend(vix_close: float, vix_sma: float) -> str:
    ratio = vix_close / vix_sma
    if ratio > config.VIX_TREND_UPPER:
        return "expanding"
    if ratio < config.VIX_TREND_LOWER:
        return "contracting"
    return "stable"


def classify_vol_environment(vvix_close: float) -> str:
    if vvix_close < config.VVIX_COMPLACENT_MAX:
        return "complacent"
    if vvix_close < config.VVIX_CLEAN_FEAR_MAX:
        return "clean_fear"
    if vvix_close < config.VVIX_CHAOTIC_MIN:
        return "elevated"
    if vvix_close < config.VVIX_SPIKE_THRESHOLD:
        return "chaotic"
    return "spike"


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


def _rsi_score(rsi_val: float) -> float:
    """Map RSI value to a sub-signal in {-1, -0.5, 0, 0.5, 1}."""
    if rsi_val < config.RSI_OVERSOLD:
        return 1.0
    if rsi_val < config.RSI_HEALTHY_LOW:
        return 0.5
    if rsi_val <= config.RSI_HEALTHY_HIGH:
        return 0.0
    return -1.0


def compute_composite(
    close: float,
    sma_50: float | None,
    sma_200: float | None,
    macd_val: float | None,
    macd_sig: float | None,
    rsi_val: float | None,
) -> float | None:
    """Weighted composite score in [-1, 1].

    Normalises by total weight present so missing indicators (None) don't
    drag the score toward zero — a ticker with only two valid indicators
    still gets a full [-1, 1] score from those two.
    """
    scores: dict[str, float] = {}

    if sma_200 is not None:
        scores["sma_200"] = 1.0 if close > sma_200 else -1.0
    if sma_50 is not None:
        scores["sma_50"] = 1.0 if close > sma_50 else -1.0
    if macd_val is not None and macd_sig is not None:
        scores["macd"] = 1.0 if macd_val > macd_sig else -1.0
    if rsi_val is not None:
        scores["rsi"] = _rsi_score(rsi_val)

    if not scores:
        return None

    weights = config.SIGNAL_WEIGHTS
    total_weight = sum(weights[k] for k in scores)
    raw = sum(scores[k] * weights[k] for k in scores) / total_weight
    return round(raw, 4)
