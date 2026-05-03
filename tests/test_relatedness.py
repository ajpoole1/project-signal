"""Tests for dag_components/relatedness/calculations.py"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from dag_components.relatedness import calculations as calc


def _price_history(tickers_values: dict[str, list[float]], start: str = "2024-01-01") -> dict[str, list[dict]]:
    """Build a minimal price_history dict from ticker → list of close prices."""
    history: dict[str, list[dict]] = {}
    for ticker, values in tickers_values.items():
        idx = pd.date_range(start=start, periods=len(values), freq="B")
        history[ticker] = [
            {"date": str(dt.date()), "close": float(v)}
            for dt, v in zip(idx, values, strict=False)
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
