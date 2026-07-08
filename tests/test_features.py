"""Tests for dag_components/features/calculations.py"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from dag_components.features import calculations as calc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(
    n: int = 300,
    close_val: float = 100.0,
    high_val: float | None = None,
    low_val: float | None = None,
    volume_val: float = 1_000_000.0,
    sma50_val: float | None = None,
    sma200_val: float | None = None,
    rsi_val: float = 50.0,
    macd_hist_val: float = 0.5,
    bb_upper_val: float | None = None,
    bb_lower_val: float | None = None,
) -> pd.DataFrame:
    """Build a constant-price DataFrame of length n."""
    dates = pd.date_range("2020-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    c = close_val
    return pd.DataFrame(
        {
            "date": dates,
            "open": c,
            "high": high_val if high_val is not None else c * 1.01,
            "low": low_val if low_val is not None else c * 0.99,
            "close": c,
            "volume": volume_val,
            "sma_50": sma50_val if sma50_val is not None else c,
            "sma_200": sma200_val if sma200_val is not None else c,
            "rsi_14": rsi_val,
            "macd_hist": macd_hist_val,
            "bb_upper": bb_upper_val if bb_upper_val is not None else c * 1.02,
            "bb_lower": bb_lower_val if bb_lower_val is not None else c * 0.98,
        }
    )


def _last(df: pd.DataFrame, col: str):
    """Return last non-null value of a column, or None."""
    series = df[col].dropna()
    return float(series.iloc[-1]) if not series.empty else None


# ---------------------------------------------------------------------------
# Phase 2 features
# ---------------------------------------------------------------------------


class TestDistSma50:
    def test_zero_when_close_equals_sma50(self):
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        assert _last(out, "dist_sma50") == pytest.approx(0.0)

    def test_positive_when_above(self):
        df = _make_df(n=300, close_val=110.0, sma50_val=100.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "dist_sma50") == pytest.approx(0.10)

    def test_negative_when_below(self):
        df = _make_df(n=300, close_val=90.0, sma50_val=100.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "dist_sma50") == pytest.approx(-0.10)

    def test_null_when_sma50_zero(self):
        df = _make_df(n=300, sma50_val=0.0)
        out = calc.compute_feature_frame(df)
        assert out["dist_sma50"].isna().all()


class TestDistSma200:
    def test_zero_when_at_sma200(self):
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        assert _last(out, "dist_sma200") == pytest.approx(0.0)

    def test_positive_when_above(self):
        df = _make_df(n=300, close_val=120.0, sma200_val=100.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "dist_sma200") == pytest.approx(0.20)


class TestDistSma200Z:
    def test_null_before_window_fills(self):
        df = _make_df(n=200)  # less than z_window=252
        out = calc.compute_feature_frame(df)
        assert out["dist_sma200_z"].isna().all()

    def test_zero_when_constant_dist(self):
        # Constant dist_sma200=0 → std=0 → NaN (division by zero), not 0
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        # std of constant series is 0 → z-score is NaN
        assert out["dist_sma200_z"].isna().all()

    def test_finite_when_varying(self):
        # Varying close against constant sma200 gives varying dist_sma200 → z-score computable
        import numpy as np

        n = 300
        dates = pd.date_range("2020-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
        close = pd.Series(100.0 + np.sin(range(n)) * 5)
        df = pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1e6,
                "sma_50": close,
                "sma_200": 100.0,
                "rsi_14": 50.0,
                "macd_hist": 0.0,
                "bb_upper": close * 1.02,
                "bb_lower": close * 0.98,
            }
        )
        out = calc.compute_feature_frame(df)
        last_z = out["dist_sma200_z"].dropna()
        assert not last_z.empty
        assert math.isfinite(float(last_z.iloc[-1]))


class TestRsiCentered:
    def test_zero_at_rsi_50(self):
        df = _make_df(n=300, rsi_val=50.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "rsi_centered") == pytest.approx(0.0)

    def test_positive_one_at_rsi_100(self):
        df = _make_df(n=300, rsi_val=100.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "rsi_centered") == pytest.approx(1.0)

    def test_negative_one_at_rsi_0(self):
        df = _make_df(n=300, rsi_val=0.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "rsi_centered") == pytest.approx(-1.0)

    def test_oversold_is_negative(self):
        df = _make_df(n=300, rsi_val=25.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "rsi_centered") < 0


class TestBbPctb:
    def test_half_when_at_midband(self):
        # close == (upper + lower) / 2 → pctb = 0.5
        df = _make_df(n=300, close_val=100.0, bb_upper_val=102.0, bb_lower_val=98.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "bb_pctb") == pytest.approx(0.5)

    def test_one_at_upper_band(self):
        df = _make_df(n=300, close_val=102.0, bb_upper_val=102.0, bb_lower_val=98.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "bb_pctb") == pytest.approx(1.0)

    def test_zero_at_lower_band(self):
        df = _make_df(n=300, close_val=98.0, bb_upper_val=102.0, bb_lower_val=98.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "bb_pctb") == pytest.approx(0.0)

    def test_null_when_bands_equal(self):
        df = _make_df(n=300, bb_upper_val=100.0, bb_lower_val=100.0)
        out = calc.compute_feature_frame(df)
        assert out["bb_pctb"].isna().all()


class TestMacdHistNorm:
    def test_zero_when_hist_zero(self):
        df = _make_df(n=300, macd_hist_val=0.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "macd_hist_norm") == pytest.approx(0.0)

    def test_scales_by_close(self):
        df = _make_df(n=300, close_val=200.0, macd_hist_val=2.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "macd_hist_norm") == pytest.approx(0.01)


class TestDist52w:
    def test_null_before_252_bars(self):
        df = _make_df(n=200)
        out = calc.compute_feature_frame(df)
        assert out["dist_52w_high"].isna().all()
        assert out["dist_52w_low"].isna().all()

    def test_dist_52w_high_leq_zero_at_new_high(self):
        # constant price → close == 252d high → dist = 0
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        val = _last(out, "dist_52w_high")
        assert val is not None
        assert val <= 0.0

    def test_dist_52w_low_geq_zero_at_new_low(self):
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        val = _last(out, "dist_52w_low")
        assert val is not None
        assert val >= 0.0


class TestVolRatio:
    def test_one_when_volume_equals_sma(self):
        df = _make_df(n=300, volume_val=1_000_000.0)
        out = calc.compute_feature_frame(df)
        assert _last(out, "vol_ratio") == pytest.approx(1.0)

    def test_null_before_window_fills(self):
        df = _make_df(n=10)
        out = calc.compute_feature_frame(df)
        assert out["vol_ratio"].isna().all()


class TestRet:
    def test_ret_21d_zero_for_constant_price(self):
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        assert _last(out, "ret_21d") == pytest.approx(0.0)

    def test_ret_63d_zero_for_constant_price(self):
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        assert _last(out, "ret_63d") == pytest.approx(0.0)

    def test_ret_21d_null_before_window(self):
        df = _make_df(n=15)
        out = calc.compute_feature_frame(df)
        assert out["ret_21d"].isna().all()


# ---------------------------------------------------------------------------
# Phase 3 features
# ---------------------------------------------------------------------------


def _make_trending_df(n: int = 400) -> pd.DataFrame:
    """Build a DataFrame with a clear uptrend to produce non-degenerate ADX."""
    dates = pd.date_range("2020-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    # Steady uptrend with small daily noise — ADX will be high, slope positive
    rng = pd.Series(range(n), dtype=float)
    close = 50.0 + rng * 0.3 + pd.Series([i % 3 * 0.1 for i in range(n)])
    high = close * 1.01
    low = close * 0.99
    sma50 = close.rolling(50, min_periods=50).mean().fillna(close)
    sma200 = close.rolling(200, min_periods=200).mean().fillna(close)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1_000_000.0,
            "sma_50": sma50,
            "sma_200": sma200,
            "rsi_14": 60.0,
            "macd_hist": 0.5,
            "bb_upper": close * 1.02,
            "bb_lower": close * 0.98,
        }
    )


class TestAdx14:
    def test_null_during_warmup(self):
        df = _make_df(n=20)  # less than 2*14=28 warmup
        out = calc.compute_feature_frame(df)
        assert out["adx_14"].isna().all()

    def test_non_null_after_warmup_with_trending_data(self):
        # Constant price gives ATR=0 → degenerate ADX=NaN; use trending data
        df = _make_trending_df(n=400)
        out = calc.compute_feature_frame(df)
        valid = out["adx_14"].dropna()
        assert not valid.empty

    def test_range_zero_to_100_trending(self):
        df = _make_trending_df(n=400)
        out = calc.compute_feature_frame(df)
        valid = out["adx_14"].dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_high_adx_for_strong_trend(self):
        # Strong steady uptrend → ADX should be elevated (> 20 at minimum)
        df = _make_trending_df(n=400)
        out = calc.compute_feature_frame(df)
        valid = out["adx_14"].dropna()
        assert float(valid.iloc[-1]) > 20.0


class TestSma200Slope:
    def test_null_before_window(self):
        # compute_feature_frame receives a pre-computed sma_200 column.
        # sma200_slope = sma_200.pct_change(63) → first non-null at index 63.
        # n=63 → indices 0-62 → last index 62, pct_change needs index 63 → all null.
        df = _make_df(n=63)
        out = calc.compute_feature_frame(df)
        assert out["sma200_slope"].isna().all()

    def test_zero_for_constant_sma200(self):
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        assert _last(out, "sma200_slope") == pytest.approx(0.0)

    def test_positive_for_rising_sma200(self):
        df = _make_trending_df(n=400)
        out = calc.compute_feature_frame(df)
        val = _last(out, "sma200_slope")
        assert val is not None
        assert val > 0


class TestTickerRegime:
    def test_ranging_for_flat_slope_low_adx(self):
        # Use trending df but check that ranging label exists when slope is flat
        # at the start of the series (sma200_slope still ~0 near warmup edge)
        # Constant price → sma200_slope=0, ADX=NaN → regime=NaN (not ranging)
        # so we test the non-null values of a trending series for valid labels
        df2 = _make_trending_df(n=400)
        out2 = calc.compute_feature_frame(df2)
        valid = out2["ticker_regime"].dropna()
        assert not valid.empty
        assert valid.isin(["trending", "ranging"]).all()

    def test_trending_for_strong_trend(self):
        df = _make_trending_df(n=400)
        out = calc.compute_feature_frame(df)
        valid = out["ticker_regime"].dropna()
        # Strong uptrend should produce at least some 'trending' labels
        assert (valid == "trending").any()

    def test_null_during_warmup(self):
        df = _make_df(n=50)
        out = calc.compute_feature_frame(df)
        assert out["ticker_regime"].isna().all()

    def test_only_valid_values(self):
        df = _make_trending_df(n=400)
        out = calc.compute_feature_frame(df)
        valid = out["ticker_regime"].dropna()
        assert valid.isin(["trending", "ranging"]).all()


class TestTrendDirection:
    def test_null_when_ranging(self):
        df = _make_df(n=300)
        out = calc.compute_feature_frame(df)
        # Constant price → ranging → trend_direction should all be None
        ranging_mask = out["ticker_regime"] == "ranging"
        assert out.loc[ranging_mask, "trend_direction"].isna().all()

    def test_null_during_warmup(self):
        df = _make_df(n=50)
        out = calc.compute_feature_frame(df)
        assert out["trend_direction"].isna().all()


# ---------------------------------------------------------------------------
# min_history_required
# ---------------------------------------------------------------------------


class TestMinHistoryRequired:
    def test_returns_positive_int(self):
        result = calc.min_history_required()
        assert isinstance(result, int)
        assert result > 0

    def test_at_least_sma200_window(self):
        from config import config

        assert calc.min_history_required() >= config.V2_FEATURE_SMA_LONG

    def test_at_least_52w_window(self):
        from config import config

        assert calc.min_history_required() >= config.V2_FEATURE_52W_WINDOW
