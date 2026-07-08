"""Tests for dag_components/outcome_tracker/calculations_v2.py"""

from __future__ import annotations

import math

import pytest

from dag_components.outcome_tracker import calculations_v2 as calc  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _price_rows(closes: list[float]) -> list[dict]:
    return [{"close": c} for c in closes]


# ---------------------------------------------------------------------------
# trailing_daily_vol
# ---------------------------------------------------------------------------


class TestTrailingDailyVol:
    def test_constant_prices_returns_zero(self):
        closes = [100.0] * 25
        result = calc.trailing_daily_vol(closes, lookback=20)
        assert result == 0.0

    def test_known_two_price_series(self):
        # single return of +10%: stdev of [0.1] with n=1 → needs >=2 returns
        result = calc.trailing_daily_vol([100.0, 110.0], lookback=20)
        assert result is None  # only 1 return from 2 prices

    def test_two_returns_computable(self):
        closes = [100.0, 110.0, 121.0]  # two +10% returns
        result = calc.trailing_daily_vol(closes, lookback=20)
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_uses_trailing_window(self):
        # Last 3 prices are volatile; first 20 are flat
        flat = [100.0] * 20
        volatile = [100.0, 200.0, 100.0]
        all_closes = flat + volatile
        result_full = calc.trailing_daily_vol(all_closes, lookback=3)
        result_flat = calc.trailing_daily_vol(flat + [100.0], lookback=3)
        assert result_full > result_flat

    def test_returns_none_for_single_price(self):
        assert calc.trailing_daily_vol([100.0], lookback=20) is None

    def test_returns_none_for_empty(self):
        assert calc.trailing_daily_vol([], lookback=20) is None

    def test_zero_price_in_window_handled(self):
        # 100→0 is a valid return (-1.0); 0→50 is skipped (prev=0); 50→60 is valid (0.2).
        # Two valid returns → stdev is computable (not None).
        closes = [100.0, 0.0, 50.0, 60.0]
        result = calc.trailing_daily_vol(closes, lookback=20)
        assert result is not None
        assert result > 0

    def test_zero_consecutive_prices_returns_none(self):
        # Only one valid return when the zero is preceded by another zero
        closes = [0.0, 0.0, 50.0, 60.0]
        result = calc.trailing_daily_vol(closes, lookback=20)
        # 0→0 skip, 0→50 skip, 50→60 valid: only 1 return → None
        assert result is None

    def test_returns_positive_float(self):
        closes = [100.0, 102.0, 98.0, 103.0, 99.0, 101.0]
        result = calc.trailing_daily_vol(closes, lookback=20)
        assert isinstance(result, float)
        assert result > 0


# ---------------------------------------------------------------------------
# scaled_threshold
# ---------------------------------------------------------------------------


class TestScaledThreshold:
    def test_formula(self):
        vol = 0.01
        horizon = 25
        k = 0.5
        expected = 0.5 * 0.01 * math.sqrt(25)
        assert calc.scaled_threshold(vol, horizon, k) == pytest.approx(expected)

    def test_zero_vol_returns_zero(self):
        assert calc.scaled_threshold(0.0, 10, 0.5) == 0.0

    def test_horizon_1(self):
        assert calc.scaled_threshold(0.02, 1, 0.5) == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# benchmark_window_return
# ---------------------------------------------------------------------------


class TestBenchmarkWindowReturn:
    def _bench_rows(self, closes: list[float]) -> list[dict]:
        return [{"close": c} for c in closes]

    def test_positive_return(self):
        rows = self._bench_rows([110.0] * 5)
        result = calc.benchmark_window_return(rows, horizon=5, bench_price_at_signal=100.0)
        assert result == pytest.approx(0.10)

    def test_negative_return(self):
        rows = self._bench_rows([90.0] * 5)
        result = calc.benchmark_window_return(rows, horizon=5, bench_price_at_signal=100.0)
        assert result == pytest.approx(-0.10)

    def test_returns_none_if_too_few_rows(self):
        rows = self._bench_rows([110.0, 112.0, 108.0])
        result = calc.benchmark_window_return(rows, horizon=5, bench_price_at_signal=100.0)
        assert result is None

    def test_returns_none_if_bench_price_zero(self):
        rows = self._bench_rows([110.0] * 5)
        assert calc.benchmark_window_return(rows, horizon=5, bench_price_at_signal=0.0) is None

    def test_uses_correct_horizon_row(self):
        # horizon=3 should use index 2 (3rd row)
        rows = self._bench_rows([101.0, 102.0, 115.0, 200.0])
        result = calc.benchmark_window_return(rows, horizon=3, bench_price_at_signal=100.0)
        assert result == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# excess_return
# ---------------------------------------------------------------------------


class TestExcessReturn:
    def test_zero_beta(self):
        assert calc.excess_return(0.05, 0.0, 0.10) == pytest.approx(0.05)

    def test_beta_one(self):
        assert calc.excess_return(0.05, 1.0, 0.03) == pytest.approx(0.02)

    def test_negative_excess(self):
        assert calc.excess_return(0.01, 1.2, 0.05) == pytest.approx(0.01 - 1.2 * 0.05)

    def test_benchmark_negative(self):
        # stock fell less than market
        result = calc.excess_return(-0.02, 1.0, -0.05)
        assert result == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# outcome_flags
# ---------------------------------------------------------------------------


class TestOutcomeFlags:
    def test_bullish_positive_above_threshold(self):
        direction, threshold = calc.outcome_flags("bullish", 0.05, 0.02)
        assert direction is True
        assert threshold is True

    def test_bullish_positive_below_threshold(self):
        direction, threshold = calc.outcome_flags("bullish", 0.01, 0.02)
        assert direction is True
        assert threshold is False

    def test_bullish_negative_excess(self):
        direction, threshold = calc.outcome_flags("bullish", -0.03, 0.02)
        assert direction is False
        assert threshold is False

    def test_bearish_negative_above_threshold(self):
        direction, threshold = calc.outcome_flags("bearish", -0.05, 0.02)
        assert direction is True
        assert threshold is True

    def test_bearish_negative_below_threshold(self):
        direction, threshold = calc.outcome_flags("bearish", -0.01, 0.02)
        assert direction is True
        assert threshold is False

    def test_bearish_positive_excess(self):
        direction, threshold = calc.outcome_flags("bearish", 0.03, 0.02)
        assert direction is False
        assert threshold is False

    def test_neutral_bias_returns_none_none(self):
        assert calc.outcome_flags("neutral", 0.05, 0.02) == (None, None)

    def test_none_excess_returns_none_none(self):
        assert calc.outcome_flags("bullish", None, 0.02) == (None, None)

    def test_none_threshold_returns_none_none(self):
        assert calc.outcome_flags("bullish", 0.05, None) == (None, None)

    def test_zero_excess_bullish_is_not_correct(self):
        direction, threshold = calc.outcome_flags("bullish", 0.0, 0.0)
        assert direction is False

    def test_exactly_at_threshold_is_correct(self):
        direction, threshold = calc.outcome_flags("bullish", 0.02, 0.02)
        assert direction is True
        assert threshold is True


# ---------------------------------------------------------------------------
# assign_episodes
# ---------------------------------------------------------------------------


def _make_rows(specs: list[tuple]) -> list[dict]:
    """specs: list of (ticker, signal_date_str, bias)"""
    return [{"ticker": t, "signal_date": d, "bias": b} for t, d, b in specs]


class TestAssignEpisodes:
    def test_first_signal_is_episode_head(self):
        rows = _make_rows([("AAPL", "2024-01-02", "bullish")])
        result = calc.assign_episodes(rows, gap_days=5)
        assert result[0]["is_episode_head"] is True
        assert result[0]["episode_id"] == "AAPL:2024-01-02"

    def test_consecutive_signals_same_episode(self):
        rows = _make_rows(
            [
                ("AAPL", "2024-01-02", "bullish"),
                ("AAPL", "2024-01-03", "bullish"),
                ("AAPL", "2024-01-04", "bullish"),
            ]
        )
        result = calc.assign_episodes(rows, gap_days=5)
        assert result[0]["is_episode_head"] is True
        assert result[1]["is_episode_head"] is False
        assert result[2]["is_episode_head"] is False
        assert all(r["episode_id"] == "AAPL:2024-01-02" for r in result)

    def test_gap_beyond_threshold_starts_new_episode(self):
        # 6 rows between signals in sequence → gap=6 > gap_days=5 → new episode
        rows = _make_rows(
            [
                ("AAPL", "2024-01-02", "bullish"),
                # simulate 6 rows of gap by putting a second signal as seq_pos=6
            ]
        )
        # Build a sequence with 5 consecutive then one far away
        rows = _make_rows(
            [
                ("AAPL", "2024-01-02", "bullish"),
                ("AAPL", "2024-01-03", "bullish"),
                ("AAPL", "2024-01-04", "bullish"),
                ("AAPL", "2024-01-05", "bullish"),
                ("AAPL", "2024-01-06", "bullish"),
                ("AAPL", "2024-01-07", "bullish"),  # seq_pos=5, gap=5 → NOT new
                ("AAPL", "2024-01-08", "bullish"),  # seq_pos=6, gap=6 → new episode
            ]
        )
        result = calc.assign_episodes(rows, gap_days=5)
        assert result[0]["is_episode_head"] is True
        assert result[6]["is_episode_head"] is True
        assert result[6]["episode_id"] == "AAPL:2024-01-08"

    def test_different_tickers_independent(self):
        rows = _make_rows(
            [
                ("AAPL", "2024-01-02", "bullish"),
                ("MSFT", "2024-01-02", "bullish"),
            ]
        )
        result = calc.assign_episodes(rows, gap_days=5)
        assert result[0]["episode_id"] == "AAPL:2024-01-02"
        assert result[1]["episode_id"] == "MSFT:2024-01-02"
        assert result[0]["is_episode_head"] is True
        assert result[1]["is_episode_head"] is True

    def test_different_biases_independent(self):
        rows = _make_rows(
            [
                ("AAPL", "2024-01-02", "bullish"),
                ("AAPL", "2024-01-03", "bearish"),
            ]
        )
        result = calc.assign_episodes(rows, gap_days=5)
        assert result[0]["is_episode_head"] is True
        assert result[1]["is_episode_head"] is True
        assert result[0]["episode_id"] != result[1]["episode_id"]

    def test_neutral_bias_no_episode(self):
        rows = _make_rows([("AAPL", "2024-01-02", "neutral")])
        result = calc.assign_episodes(rows, gap_days=5)
        assert result[0]["episode_id"] is None
        assert result[0]["is_episode_head"] is False

    def test_original_rows_not_mutated(self):
        rows = _make_rows([("AAPL", "2024-01-02", "bullish")])
        original = rows[0].copy()
        calc.assign_episodes(rows, gap_days=5)
        assert rows[0] == original

    def test_empty_input(self):
        assert calc.assign_episodes([], gap_days=5) == []


# ---------------------------------------------------------------------------
# has_price_seam
# ---------------------------------------------------------------------------


class TestHasPriceSeam:
    def test_clean_prices_no_seam(self):
        rows = _price_rows([100.0, 101.0, 99.0, 102.0])
        assert calc.has_price_seam(rows, max_daily_ratio=3.0) is False

    def test_large_upward_jump_is_seam(self):
        rows = _price_rows([0.001, 10.0])
        assert calc.has_price_seam(rows, max_daily_ratio=3.0) is True

    def test_large_downward_jump_is_seam(self):
        rows = _price_rows([10.0, 0.001])
        assert calc.has_price_seam(rows, max_daily_ratio=3.0) is True

    def test_exactly_at_ratio_is_not_seam(self):
        rows = _price_rows([100.0, 300.0])
        assert calc.has_price_seam(rows, max_daily_ratio=3.0) is False

    def test_zero_previous_price_is_seam(self):
        rows = _price_rows([0.0, 10.0])
        assert calc.has_price_seam(rows, max_daily_ratio=3.0) is True

    def test_single_row_no_seam(self):
        rows = _price_rows([100.0])
        assert calc.has_price_seam(rows, max_daily_ratio=3.0) is False

    def test_empty_no_seam(self):
        assert calc.has_price_seam([], max_daily_ratio=3.0) is False
