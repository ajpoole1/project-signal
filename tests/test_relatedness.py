"""Tests for dag_components/relatedness/calculations.py"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from dag_components.relatedness import calculations as calc


def _price_history(
    tickers_values: dict[str, list[float]], start: str = "2024-01-01"
) -> dict[str, list[dict]]:
    """Build a minimal price_history dict from ticker → list of close prices."""
    history: dict[str, list[dict]] = {}
    for ticker, values in tickers_values.items():
        idx = pd.date_range(start=start, periods=len(values), freq="B")
        history[ticker] = [
            {"date": str(dt.date()), "close": float(v)} for dt, v in zip(idx, values, strict=False)
        ]
    return history


# ---------------------------------------------------------------------------
# build_returns_matrix
# ---------------------------------------------------------------------------


class TestBuildReturnsMatrix:
    def test_basic_shape(self):
        history = _price_history({"A": [100.0, 101.0, 102.0], "B": [50.0, 51.0, 52.0]})
        result = calc.build_returns_matrix(history, ["A", "B"])
        assert set(result.columns) == {"A", "B"}
        # pct_change introduces one NaN row at the top
        assert result.shape[0] == 3

    def test_empty_history_returns_empty_df(self):
        result = calc.build_returns_matrix({}, ["A", "B"])
        assert result.empty

    def test_missing_ticker_excluded(self):
        history = _price_history({"A": [100.0, 101.0, 102.0]})
        result = calc.build_returns_matrix(history, ["A", "B"])
        assert "A" in result.columns
        assert "B" not in result.columns

    def test_returns_are_pct_change(self):
        history = _price_history({"A": [100.0, 110.0, 121.0]})
        result = calc.build_returns_matrix(history, ["A"])
        assert math.isnan(result["A"].iloc[0])
        assert result["A"].iloc[1] == pytest.approx(0.10)
        assert result["A"].iloc[2] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# correlation_pairs
# ---------------------------------------------------------------------------


class TestCorrelationPairs:
    def test_identical_series_correlation_one(self):
        """Two tickers with the same returns should have r = 1.0."""
        prices = [float(i) for i in range(1, 52)]
        history = _price_history({"A": prices, "B": prices})
        returns = calc.build_returns_matrix(history, ["A", "B"])
        pairs = calc.correlation_pairs(returns, window_days=60, equity_tickers=["A", "B"])
        assert len(pairs) == 1
        ta, tb, w, r = pairs[0]
        assert r == pytest.approx(1.0)

    def test_anti_correlated_series(self):
        """Two series with exactly negated daily returns should have r = -1.0."""
        import numpy as np

        rng = np.random.default_rng(42)
        raw_returns = rng.normal(0, 0.02, 50)
        prices_a = [100.0]
        prices_b = [100.0]
        for r in raw_returns:
            prices_a.append(prices_a[-1] * (1 + r))
            prices_b.append(prices_b[-1] * (1 - r))  # pct_change of B = -pct_change of A
        history = _price_history({"A": prices_a, "B": prices_b})
        returns = calc.build_returns_matrix(history, ["A", "B"])
        pairs = calc.correlation_pairs(returns, window_days=100, equity_tickers=["A", "B"])
        assert len(pairs) == 1
        _, _, _, r = pairs[0]
        assert r == pytest.approx(-1.0, abs=1e-4)

    def test_canonical_alphabetical_ordering(self):
        """ticker_a should always be alphabetically less than ticker_b."""
        prices = [float(i) for i in range(1, 52)]
        history = _price_history({"Z": prices, "A": prices})
        returns = calc.build_returns_matrix(history, ["A", "Z"])
        pairs = calc.correlation_pairs(returns, window_days=60, equity_tickers=["A", "Z"])
        assert len(pairs) == 1
        ta, tb, _, _ = pairs[0]
        assert ta < tb

    def test_no_duplicates_for_three_tickers(self):
        """Three tickers should produce exactly 3 unique pairs."""
        prices = [float(i) for i in range(1, 52)]
        history = _price_history({"A": prices, "B": prices, "C": prices})
        returns = calc.build_returns_matrix(history, ["A", "B", "C"])
        pairs = calc.correlation_pairs(returns, window_days=60, equity_tickers=["A", "B", "C"])
        assert len(pairs) == 3
        pair_keys = {(ta, tb) for ta, tb, _, _ in pairs}
        assert len(pair_keys) == 3

    def test_window_limits_data(self):
        """Window shorter than total history should use fewer data points."""
        prices = [float(i) for i in range(1, 102)]
        history = _price_history({"A": prices, "B": prices})
        returns = calc.build_returns_matrix(history, ["A", "B"])
        # 10-day window on 100 business-day history
        pairs = calc.correlation_pairs(returns, window_days=14, equity_tickers=["A", "B"])
        assert len(pairs) == 1

    def test_empty_returns_returns_empty(self):
        pairs = calc.correlation_pairs(pd.DataFrame(), window_days=30, equity_tickers=["A"])
        assert pairs == []

    def test_single_ticker_returns_empty(self):
        prices = [float(i) for i in range(1, 52)]
        history = _price_history({"A": prices})
        returns = calc.build_returns_matrix(history, ["A"])
        pairs = calc.correlation_pairs(returns, window_days=60, equity_tickers=["A"])
        assert pairs == []


# ---------------------------------------------------------------------------
# beta_values
# ---------------------------------------------------------------------------


class TestBetaValues:
    def test_ticker_perfectly_tracks_etf_has_beta_one(self):
        """A ticker with identical returns to the ETF should have beta ≈ 1.0."""
        prices = [float(100 + i) for i in range(51)]
        history = _price_history({"TICK": prices, "SPY": prices})
        returns = calc.build_returns_matrix(history, ["TICK", "SPY"])
        betas = calc.beta_values(returns, ["TICK"], ["SPY"], window_days=60)
        assert len(betas) == 1
        _, _, _, beta = betas[0]
        assert beta == pytest.approx(1.0, rel=1e-4)

    def test_double_leverage_has_beta_two(self):
        """A ticker that doubles the ETF's returns should have beta ≈ 2.0."""
        base = [float(100 + i) for i in range(51)]
        # Build doubled-return series: start same, then double each move
        etf_returns = pd.Series([b / a - 1 for a, b in zip(base[:-1], base[1:], strict=False)])
        doubled_prices = [100.0]
        for r in etf_returns:
            doubled_prices.append(doubled_prices[-1] * (1 + 2 * r))
        history = _price_history({"TICK": doubled_prices, "SPY": base})
        returns = calc.build_returns_matrix(history, ["TICK", "SPY"])
        betas = calc.beta_values(returns, ["TICK"], ["SPY"], window_days=60)
        assert len(betas) == 1
        _, _, _, beta = betas[0]
        assert beta == pytest.approx(2.0, rel=1e-4)

    def test_empty_returns_returns_empty(self):
        betas = calc.beta_values(pd.DataFrame(), ["TICK"], ["SPY"], window_days=90)
        assert betas == []

    def test_missing_etf_returns_empty(self):
        prices = [float(i) for i in range(1, 52)]
        history = _price_history({"TICK": prices})
        returns = calc.build_returns_matrix(history, ["TICK"])
        betas = calc.beta_values(returns, ["TICK"], ["SPY"], window_days=60)
        assert betas == []

    def test_result_structure(self):
        """Each result tuple should be (ticker, etf, window_days, beta)."""
        prices = [float(100 + i) for i in range(51)]
        history = _price_history({"TICK": prices, "SPY": prices})
        returns = calc.build_returns_matrix(history, ["TICK", "SPY"])
        betas = calc.beta_values(returns, ["TICK"], ["SPY"], window_days=60)
        assert len(betas) == 1
        ticker, etf, window, beta = betas[0]
        assert ticker == "TICK"
        assert etf == "SPY"
        assert window == 60
        assert isinstance(beta, float)

    def test_insert_column_mapping_regression(self):
        """Regression test: verify beta_values tuple order matches INSERT column order.

        This test MUST FAIL if _INSERT_BETAS column order in tasks.py is ever
        misaligned with beta_values() return tuple order (ticker, etf, window_days, beta).
        The bug where columns were swapped caused betas to be truncated into
        the window_days INTEGER column and window_days to overflow into beta.
        """
        prices = [float(100 + i) for i in range(51)]
        history = _price_history({"ABC": prices, "SPY": prices})
        returns = calc.build_returns_matrix(history, ["ABC", "SPY"])
        betas = calc.beta_values(returns, ["ABC"], ["SPY"], window_days=90)
        assert len(betas) == 1

        # Tuple order from beta_values: (ticker, etf, window_days, beta)
        ticker, etf, window_days, beta = betas[0]

        # Expected order in _INSERT_BETAS (tasks.py line 28):
        # (ticker, etf_proxy, window_days, beta)
        # If this assertion fails, the INSERT column order has regressed.
        assert ticker == "ABC", "Position 0 must be ticker string"
        assert etf == "SPY", "Position 1 must be etf string"
        assert isinstance(window_days, int) and window_days == 90, (
            "Position 2 must be window_days int"
        )
        assert isinstance(beta, float) and 0.9 < beta < 1.1, (
            "Position 3 must be beta float (~1.0 for tracking)"
        )


# ---------------------------------------------------------------------------
# mask_implausible_returns
# ---------------------------------------------------------------------------


class TestMaskImplausibleReturns:
    def _make_returns(self, values: list[float], dates: list[str] | None = None) -> pd.DataFrame:
        if dates is None:
            dates = pd.date_range("2024-01-01", periods=len(values), freq="B").strftime("%Y-%m-%d")
        return pd.DataFrame({"TICK": values}, index=pd.DatetimeIndex(dates))

    def test_seam_day_masked_to_nan(self):
        # A 10x return (ratio=11) with max_daily_ratio=3.0 must be NaN
        returns = self._make_returns([0.01, 9.0, 0.01])  # middle day: +900%
        masked = calc.mask_implausible_returns(returns, max_daily_ratio=3.0)
        assert math.isnan(masked["TICK"].iloc[1])
        # neighbouring days untouched
        assert masked["TICK"].iloc[0] == pytest.approx(0.01)
        assert masked["TICK"].iloc[2] == pytest.approx(0.01)

    def test_large_but_plausible_return_kept(self):
        # +50% return: ratio = 1.5, within [1/3, 3]
        returns = self._make_returns([0.01, 0.50, 0.01])
        masked = calc.mask_implausible_returns(returns, max_daily_ratio=3.0)
        assert masked["TICK"].iloc[1] == pytest.approx(0.50)

    def test_large_negative_seam_masked(self):
        # -80% return: ratio = 0.20, below 1/3.0 = 0.333
        returns = self._make_returns([0.01, -0.80, 0.01])
        masked = calc.mask_implausible_returns(returns, max_daily_ratio=3.0)
        assert math.isnan(masked["TICK"].iloc[1])

    def test_existing_nan_preserved(self):
        returns = self._make_returns([0.01, float("nan"), 0.01])
        masked = calc.mask_implausible_returns(returns, max_daily_ratio=3.0)
        assert math.isnan(masked["TICK"].iloc[1])
        assert masked["TICK"].iloc[0] == pytest.approx(0.01)

    def test_empty_dataframe_returns_empty(self):
        masked = calc.mask_implausible_returns(pd.DataFrame(), max_daily_ratio=3.0)
        assert masked.empty

    def test_beta_with_seam_matches_clean_series(self):
        """Beta computed on a dirty series with a masked seam day must equal
        beta computed on the same series with that day set to NaN explicitly.

        The dirty series has a +500% seam at position 30. After masking, that
        row becomes NaN. The clean baseline is the same data with position 30
        already NaN — so both computations use identical observation sets.
        """
        rng = np.random.default_rng(42)
        n = 60
        etf_rets = rng.normal(0.001, 0.01, n)
        tick_rets = 1.5 * etf_rets + rng.normal(0, 0.005, n)

        dates = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=n, freq="B"))

        # Dirty: position 30 has a seam (+500% on TICK)
        dirty_tick = tick_rets.copy()
        dirty_tick[30] = 5.0
        dirty = pd.DataFrame({"TICK": dirty_tick, "SPY": etf_rets}, index=dates)

        # Clean baseline: same data but position 30 already NaN (simulates masked series)
        clean_tick = tick_rets.copy().astype(float)
        clean_tick[30] = float("nan")
        clean = pd.DataFrame({"TICK": clean_tick, "SPY": etf_rets}, index=dates)

        masked = calc.mask_implausible_returns(dirty, max_daily_ratio=3.0)

        betas_clean = calc.beta_values(clean, ["TICK"], ["SPY"], window_days=90)
        betas_masked = calc.beta_values(masked, ["TICK"], ["SPY"], window_days=90)

        assert len(betas_clean) == 1
        assert len(betas_masked) == 1
        _, _, _, beta_clean = betas_clean[0]
        _, _, _, beta_masked = betas_masked[0]

        assert beta_masked == pytest.approx(beta_clean, rel=1e-9)
