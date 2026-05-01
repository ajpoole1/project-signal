"""Tests for dag_components/indicators/calculations.py"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from dag_components.indicators import calculations as calc


def _series(values: list[float], start: str = "2024-01-01") -> pd.Series:
    idx = pd.date_range(start=start, periods=len(values), freq="D")
    return pd.Series(values, index=idx.strftime("%Y-%m-%d"))


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------


class TestSMA:
    def test_correct_value(self):
        s = _series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = calc.sma(s, 3)
        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_min_periods_enforced(self):
        s = _series([1.0, 2.0, 3.0])
        assert calc.sma(s, 5).isna().all()


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------


class TestEMA:
    def test_first_value_equals_seed(self):
        s = _series([10.0] * 10)
        result = calc.ema(s, 5)
        assert result.iloc[-1] == pytest.approx(10.0)

    def test_ema_tracks_rising_series(self):
        s = _series([float(i) for i in range(1, 30)])
        result = calc.ema(s, 5)
        # EMA should lag behind the latest value in a rising series
        assert result.iloc[-1] < s.iloc[-1]


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------


class TestMACD:
    def test_returns_three_series_same_length(self):
        close = _series([float(i) for i in range(1, 50)])
        macd_line, signal, hist = calc.macd(close)
        assert len(macd_line) == len(close)
        assert len(signal) == len(close)
        assert len(hist) == len(close)

    def test_histogram_is_macd_minus_signal(self):
        close = _series([100.0 + i * 0.5 for i in range(60)])
        macd_line, signal, hist = calc.macd(close)
        pd.testing.assert_series_equal(hist, macd_line - signal)

    def test_values_finite_after_warmup(self):
        close = _series([100.0 + i * 0.5 for i in range(60)])
        macd_line, signal, hist = calc.macd(close)
        assert pd.notna(macd_line.iloc[-1])
        assert pd.notna(signal.iloc[-1])


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


class TestRSI:
    def test_overbought_on_rising_series(self):
        close = _series([float(i) for i in range(1, 50)])
        result = calc.rsi(close)
        assert result.iloc[-1] > 70

    def test_oversold_on_falling_series(self):
        close = _series([float(50 - i) for i in range(50)])
        result = calc.rsi(close)
        assert result.iloc[-1] < 30

    def test_values_bounded_0_to_100(self):
        close = _series([100.0 + ((-1) ** i) * i * 0.5 for i in range(40)])
        valid = calc.rsi(close).dropna()
        assert (valid >= 0).all() and (valid <= 100).all()


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------


class TestBollingerBands:
    def test_upper_always_above_lower(self):
        close = _series([100.0 + (i % 5) * 0.5 for i in range(30)])
        upper, lower = calc.bollinger_bands(close)
        assert (upper.dropna() > lower.dropna()).all()

    def test_nan_when_insufficient_history(self):
        # BB_WINDOW = 20; 10 rows → all NaN
        close = _series([100.0] * 10)
        upper, lower = calc.bollinger_bands(close)
        assert upper.isna().all()
        assert lower.isna().all()

    def test_flat_series_has_zero_width(self):
        close = _series([100.0] * 25)
        upper, lower = calc.bollinger_bands(close)
        # Std of a constant series is 0 → bands equal mid
        assert upper.iloc[-1] == pytest.approx(lower.iloc[-1])


# ---------------------------------------------------------------------------
# VIX regime
# ---------------------------------------------------------------------------


class TestVIXRegime:
    @pytest.mark.parametrize(
        "vix, expected_label, expected_mult",
        [
            (12.0, "low", 0.85),
            (18.0, "normal", 1.00),
            (25.0, "elevated", 1.10),
            (35.0, "high", 1.20),
            (50.0, "extreme", 0.70),
        ],
    )
    def test_regime_bands(self, vix, expected_label, expected_mult):
        label, mult = calc.classify_vix_regime(vix)
        assert label == expected_label
        assert mult == pytest.approx(expected_mult)


# ---------------------------------------------------------------------------
# VIX trend
# ---------------------------------------------------------------------------


class TestVIXTrend:
    def test_expanding(self):
        # ratio = 25/20 = 1.25 > VIX_TREND_UPPER (1.20)
        assert calc.classify_vix_trend(25.0, 20.0) == "expanding"

    def test_contracting(self):
        # ratio = 16/20 = 0.80 < VIX_TREND_LOWER (0.85)
        assert calc.classify_vix_trend(16.0, 20.0) == "contracting"

    def test_stable(self):
        # ratio = 20/20 = 1.00 — within bounds
        assert calc.classify_vix_trend(20.0, 20.0) == "stable"


# ---------------------------------------------------------------------------
# Vol environment (VVIX)
# ---------------------------------------------------------------------------


class TestVolEnvironment:
    @pytest.mark.parametrize(
        "vvix, expected",
        [
            (80.0, "complacent"),
            (92.0, "clean_fear"),
            (108.0, "elevated"),
            (117.0, "chaotic"),
            (125.0, "spike"),
        ],
    )
    def test_environment_bands(self, vvix, expected):
        assert calc.classify_vol_environment(vvix) == expected


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------


class TestCompositeScore:
    def test_all_bullish_neutral_rsi(self):
        # sma_200 +1, sma_50 +1, macd +1, rsi neutral (0)
        # weights: 0.30 + 0.25 + 0.25 + 0.20 = 1.0
        # score = (0.30 + 0.25 + 0.25 + 0.0) / 1.0 = 0.80
        score = calc.compute_composite(
            close=110.0,
            sma_50=100.0,
            sma_200=90.0,
            macd_val=0.5,
            macd_sig=0.1,
            rsi_val=55.0,
        )
        assert score == pytest.approx(0.80)

    def test_all_bearish(self):
        # sma_200 -1, sma_50 -1, macd -1, rsi overbought -1
        score = calc.compute_composite(
            close=80.0,
            sma_50=100.0,
            sma_200=90.0,
            macd_val=-0.5,
            macd_sig=0.1,
            rsi_val=75.0,
        )
        assert score == pytest.approx(-1.0)

    def test_partial_indicators_normalised(self):
        # Only sma_200 available; weight = 0.30, normalised → 1.0
        score = calc.compute_composite(
            close=110.0,
            sma_50=None,
            sma_200=100.0,
            macd_val=None,
            macd_sig=None,
            rsi_val=None,
        )
        assert score == pytest.approx(1.0)

    def test_no_indicators_returns_none(self):
        score = calc.compute_composite(
            close=100.0,
            sma_50=None,
            sma_200=None,
            macd_val=None,
            macd_sig=None,
            rsi_val=None,
        )
        assert score is None

    def test_rsi_oversold_scores_bullish(self):
        # RSI < 30 → score +1 for RSI component
        score = calc.compute_composite(
            close=100.0,
            sma_50=None,
            sma_200=None,
            macd_val=None,
            macd_sig=None,
            rsi_val=25.0,
        )
        assert score == pytest.approx(1.0)

    def test_rsi_recovering_scores_half(self):
        # RSI in [30, 40) → score +0.5 for RSI component
        score = calc.compute_composite(
            close=100.0,
            sma_50=None,
            sma_200=None,
            macd_val=None,
            macd_sig=None,
            rsi_val=35.0,
        )
        assert score == pytest.approx(0.5)
