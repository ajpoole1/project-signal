"""Tests for dag_components/outcome_tracker/calculations.py"""

from __future__ import annotations

import pandas as pd
import pytest

from dag_components.outcome_tracker import calculations as calc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THRESHOLD = 0.01  # mirrors config.CORRECT_SIGNAL_THRESHOLD


def _price_rows(closes: list[float]) -> list[dict]:
    """Build price_rows dicts from a list of close prices (ascending by date)."""
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    return [{"date": str(d.date()), "close": c} for d, c in zip(dates, closes)]


def _prediction(**kwargs) -> dict:
    defaults = {
        "signal_version": "v1.0",
        "vix_regime": "normal",
        "vol_environment": "clean_fear",
        "bias": "bullish",
        "return_5d": 0.02,
        "return_10d": 0.03,
        "return_20d": 0.04,
        "correct_5d": True,
        "correct_10d": True,
        "correct_20d": True,
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# is_correct
# ---------------------------------------------------------------------------


class TestIsCorrect:
    def test_bullish_above_threshold_is_true(self):
        assert calc.is_correct("bullish", 0.02, THRESHOLD) is True

    def test_bullish_below_threshold_is_false(self):
        # Small positive move that doesn't clear the 1% bar
        assert calc.is_correct("bullish", 0.005, THRESHOLD) is False

    def test_bullish_negative_return_is_false(self):
        assert calc.is_correct("bullish", -0.03, THRESHOLD) is False

    def test_bearish_below_neg_threshold_is_true(self):
        assert calc.is_correct("bearish", -0.02, THRESHOLD) is True

    def test_bearish_above_neg_threshold_is_false(self):
        # Small negative move that doesn't clear the -1% bar
        assert calc.is_correct("bearish", -0.005, THRESHOLD) is False

    def test_bearish_positive_return_is_false(self):
        assert calc.is_correct("bearish", 0.03, THRESHOLD) is False

    def test_neutral_bias_returns_none(self):
        assert calc.is_correct("neutral", 0.05, THRESHOLD) is None

    def test_none_return_returns_none(self):
        assert calc.is_correct("bullish", None, THRESHOLD) is None

    def test_bullish_exactly_at_threshold_is_false(self):
        # Must be strictly greater than threshold
        assert calc.is_correct("bullish", THRESHOLD, THRESHOLD) is False

    def test_bearish_exactly_at_neg_threshold_is_false(self):
        assert calc.is_correct("bearish", -THRESHOLD, THRESHOLD) is False


# ---------------------------------------------------------------------------
# nth_trading_day_price
# ---------------------------------------------------------------------------


class TestNthTradingDayPrice:
    def test_first_trading_day(self):
        rows = _price_rows([100.0, 101.0, 102.0])
        assert calc.nth_trading_day_price(rows, 1) == 100.0

    def test_fifth_trading_day(self):
        closes = [100.0 + i for i in range(10)]
        rows = _price_rows(closes)
        assert calc.nth_trading_day_price(rows, 5) == 104.0

    def test_returns_none_when_not_enough_rows(self):
        rows = _price_rows([100.0, 101.0, 102.0])
        assert calc.nth_trading_day_price(rows, 5) is None

    def test_empty_rows_returns_none(self):
        assert calc.nth_trading_day_price([], 1) is None

    def test_exactly_n_rows_returns_last(self):
        rows = _price_rows([100.0, 101.0, 102.0])
        assert calc.nth_trading_day_price(rows, 3) == 102.0

    def test_weekend_boundary_not_counted(self):
        # Business-day date range skips weekends; rows 1-5 are Mon-Fri
        mon_fri_closes = [100.0, 101.0, 102.0, 103.0, 104.0]
        rows = _price_rows(mon_fri_closes)
        # 5th trading day (Friday) should be 104.0, not a weekend date
        assert calc.nth_trading_day_price(rows, 5) == 104.0
        assert len(rows) == 5  # no weekend rows inserted

    def test_close_value_is_float(self):
        rows = _price_rows([99.5])
        result = calc.nth_trading_day_price(rows, 1)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# compute_return
# ---------------------------------------------------------------------------


class TestComputeReturn:
    def test_positive_return(self):
        result = calc.compute_return(100.0, 105.0)
        assert abs(result - 0.05) < 1e-9

    def test_negative_return(self):
        result = calc.compute_return(100.0, 95.0)
        assert abs(result - (-0.05)) < 1e-9

    def test_zero_return(self):
        result = calc.compute_return(100.0, 100.0)
        assert result == 0.0

    def test_none_future_price_returns_none(self):
        assert calc.compute_return(100.0, None) is None

    def test_zero_price_at_signal_returns_none(self):
        assert calc.compute_return(0.0, 105.0) is None


# ---------------------------------------------------------------------------
# compute_accuracy_rollup
# ---------------------------------------------------------------------------


class TestComputeAccuracyRollup:
    def _make_predictions(self, n: int, correct: bool = True) -> list[dict]:
        ret = 0.02 if correct else -0.02
        return [
            _prediction(
                correct_5d=correct,
                correct_10d=correct,
                correct_20d=correct,
                return_5d=ret,
                return_10d=ret,
                return_20d=ret,
            )
            for _ in range(n)
        ]

    def test_returns_row_when_sample_size_met(self):
        preds = self._make_predictions(30)
        rows = calc.compute_accuracy_rollup(preds, [5, 10, 20], min_sample_size=30)
        horizons = {r["horizon_days"] for r in rows}
        assert 5 in horizons and 10 in horizons and 20 in horizons

    def test_filtered_when_below_min_sample_size(self):
        preds = self._make_predictions(5)
        rows = calc.compute_accuracy_rollup(preds, [5], min_sample_size=30)
        assert rows == []

    def test_accuracy_rate_all_correct(self):
        preds = self._make_predictions(30, correct=True)
        rows = calc.compute_accuracy_rollup(preds, [10], min_sample_size=30)
        row = next(r for r in rows if r["horizon_days"] == 10)
        assert row["accuracy_rate"] == 1.0
        assert row["correct_signals"] == 30
        assert row["total_signals"] == 30

    def test_accuracy_rate_none_correct(self):
        preds = self._make_predictions(30, correct=False)
        rows = calc.compute_accuracy_rollup(preds, [10], min_sample_size=30)
        row = next(r for r in rows if r["horizon_days"] == 10)
        assert row["accuracy_rate"] == 0.0
        assert row["correct_signals"] == 0

    def test_accuracy_rate_mixed(self):
        correct = self._make_predictions(15, correct=True)
        wrong = self._make_predictions(15, correct=False)
        rows = calc.compute_accuracy_rollup(correct + wrong, [10], min_sample_size=30)
        row = next(r for r in rows if r["horizon_days"] == 10)
        assert row["accuracy_rate"] == 0.5

    def test_groups_by_vix_regime(self):
        normal = [_prediction(vix_regime="normal") for _ in range(30)]
        elevated = [_prediction(vix_regime="elevated") for _ in range(30)]
        rows = calc.compute_accuracy_rollup(normal + elevated, [10], min_sample_size=30)
        regimes = {r["vix_regime"] for r in rows if r["horizon_days"] == 10}
        assert "normal" in regimes and "elevated" in regimes

    def test_missing_return_field_excluded(self):
        preds = [_prediction(return_10d=None, correct_10d=None) for _ in range(30)]
        rows = calc.compute_accuracy_rollup(preds, [10], min_sample_size=1)
        horizon_10_rows = [r for r in rows if r["horizon_days"] == 10]
        assert horizon_10_rows == []

    def test_avg_return_correct_none_when_no_correct(self):
        preds = self._make_predictions(30, correct=False)
        rows = calc.compute_accuracy_rollup(preds, [10], min_sample_size=30)
        row = next(r for r in rows if r["horizon_days"] == 10)
        assert row["avg_return_correct"] is None

    def test_avg_return_wrong_none_when_all_correct(self):
        preds = self._make_predictions(30, correct=True)
        rows = calc.compute_accuracy_rollup(preds, [10], min_sample_size=30)
        row = next(r for r in rows if r["horizon_days"] == 10)
        assert row["avg_return_wrong"] is None

    def test_output_row_has_required_keys(self):
        preds = self._make_predictions(30)
        rows = calc.compute_accuracy_rollup(preds, [10], min_sample_size=30)
        required = {
            "signal_version", "vix_regime", "vol_environment", "bias", "horizon_days",
            "total_signals", "correct_signals", "accuracy_rate",
            "avg_return", "avg_return_correct", "avg_return_wrong",
        }
        assert required.issubset(rows[0].keys())
