"""Tests for dag_components/llm/calculations.py and prompt_builder.py"""

from __future__ import annotations

import pandas as pd

from dag_components.llm import calculations as calc
from dag_components.llm import prompt_builder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signal_row(**kwargs) -> dict:
    """Build a minimal stock_signals row dict."""
    defaults = {
        "date": "2024-06-01",
        "close": 100.0,
        "composite_vix_adj": 0.45,
        "rsi_14": 55.0,
        "macd_hist": 0.12,
        "vix_close": 18.5,
        "vvix_close": 95.0,
        "vix_regime": "normal",
        "vix_trend": "stable",
        "vol_environment": "clean_fear",
        "sma_50": 98.0,
        "sma_200": 90.0,
        "bb_upper": 108.0,
        "bb_lower": 92.0,
    }
    defaults.update(kwargs)
    return defaults


def _price_rows(n: int = 30, base_high: float = 105.0, base_low: float = 95.0) -> list[dict]:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return [
        {"date": str(dt.date()), "high": base_high + i * 0.1, "low": base_low - i * 0.1}
        for i, dt in enumerate(idx)
    ]


# ---------------------------------------------------------------------------
# build_key_level_candidates
# ---------------------------------------------------------------------------


class TestBuildKeyLevelCandidates:
    def test_signal_levels_included(self):
        signal = _signal_row()
        candidates = calc.build_key_level_candidates(signal, [])
        types = {c["type"] for c in candidates}
        assert {"sma_50", "sma_200", "bb_upper", "bb_lower"}.issubset(types)

    def test_none_signal_levels_omitted(self):
        signal = _signal_row(sma_50=None, sma_200=None)
        candidates = calc.build_key_level_candidates(signal, [])
        types = {c["type"] for c in candidates}
        assert "sma_50" not in types
        assert "sma_200" not in types

    def test_price_extremes_included_with_enough_history(self):
        signal = _signal_row()
        prices = _price_rows(n=30)
        candidates = calc.build_key_level_candidates(signal, prices)
        types = {c["type"] for c in candidates}
        assert "20d_high" in types
        assert "20d_low" in types

    def test_52w_extremes_require_252_bars(self):
        signal = _signal_row(sma_50=None, sma_200=None, bb_upper=None, bb_lower=None)
        # Only 10 bars — not enough for 52w but enough for 20d
        prices = _price_rows(n=10)
        candidates = calc.build_key_level_candidates(signal, prices)
        types = {c["type"] for c in candidates}
        # 20d uses last 20 bars — with 10 we still get it from what's available
        assert "52w_high" not in types
        assert "52w_low" not in types

    def test_52w_present_with_252_bars(self):
        signal = _signal_row(sma_50=None, sma_200=None, bb_upper=None, bb_lower=None)
        prices = _price_rows(n=260)
        candidates = calc.build_key_level_candidates(signal, prices)
        types = {c["type"] for c in candidates}
        assert "52w_high" in types
        assert "52w_low" in types

    def test_empty_price_rows_no_extremes(self):
        signal = _signal_row()
        candidates = calc.build_key_level_candidates(signal, [])
        types = {c["type"] for c in candidates}
        assert "52w_high" not in types
        assert "20d_high" not in types

    def test_candidate_prices_are_floats(self):
        signal = _signal_row()
        prices = _price_rows(n=30)
        candidates = calc.build_key_level_candidates(signal, prices)
        for c in candidates:
            assert isinstance(c["price"], float)

    def test_52w_high_is_max_of_high_column(self):
        signal = _signal_row(sma_50=None, sma_200=None, bb_upper=None, bb_lower=None)
        prices = [{"date": "2024-01-01", "high": 150.0, "low": 90.0}] * 260
        candidates = calc.build_key_level_candidates(signal, prices)
        high_cands = [c for c in candidates if c["type"] == "52w_high"]
        assert len(high_cands) == 1
        assert high_cands[0]["price"] == 150.0

    def test_52w_low_is_min_of_low_column(self):
        signal = _signal_row(sma_50=None, sma_200=None, bb_upper=None, bb_lower=None)
        prices = [{"date": "2024-01-01", "high": 150.0, "low": 50.0}] * 260
        candidates = calc.build_key_level_candidates(signal, prices)
        low_cands = [c for c in candidates if c["type"] == "52w_low"]
        assert len(low_cands) == 1
        assert low_cands[0]["price"] == 50.0


# ---------------------------------------------------------------------------
# signal_trend
# ---------------------------------------------------------------------------


class TestSignalTrend:
    def test_improving_trend(self):
        history = [
            {"composite_vix_adj": 0.1},
            {"composite_vix_adj": 0.3},
            {"composite_vix_adj": 0.5},
        ]
        assert calc.signal_trend(history) == "improving"

    def test_deteriorating_trend(self):
        history = [
            {"composite_vix_adj": 0.5},
            {"composite_vix_adj": 0.3},
            {"composite_vix_adj": 0.1},
        ]
        assert calc.signal_trend(history) == "deteriorating"

    def test_flat_trend(self):
        history = [
            {"composite_vix_adj": 0.3},
            {"composite_vix_adj": 0.31},
            {"composite_vix_adj": 0.32},
        ]
        assert calc.signal_trend(history) == "flat"

    def test_single_row_is_flat(self):
        assert calc.signal_trend([{"composite_vix_adj": 0.5}]) == "flat"

    def test_empty_history_is_flat(self):
        assert calc.signal_trend([]) == "flat"

    def test_none_values_skipped(self):
        history = [
            {"composite_vix_adj": None},
            {"composite_vix_adj": 0.1},
            {"composite_vix_adj": 0.5},
        ]
        assert calc.signal_trend(history) == "improving"


# ---------------------------------------------------------------------------
# classify_bias
# ---------------------------------------------------------------------------


class TestClassifyBias:
    def test_positive_score_is_bullish(self):
        assert calc.classify_bias(0.6) == "bullish"

    def test_negative_score_is_bearish(self):
        assert calc.classify_bias(-0.6) == "bearish"

    def test_zero_score_is_neutral(self):
        assert calc.classify_bias(0.0) == "neutral"

    def test_none_score_is_neutral(self):
        assert calc.classify_bias(None) == "neutral"

    def test_small_positive_is_bullish(self):
        assert calc.classify_bias(0.001) == "bullish"

    def test_small_negative_is_bearish(self):
        assert calc.classify_bias(-0.001) == "bearish"


# ---------------------------------------------------------------------------
# classify_confidence
# ---------------------------------------------------------------------------


class TestClassifyConfidence:
    def test_none_score_returns_zero(self):
        assert calc.classify_confidence(None, "flat") == 0.0

    def test_base_from_abs_score(self):
        result = calc.classify_confidence(0.7, "flat")
        assert result == 0.7

    def test_aligned_bullish_boosts_confidence(self):
        base = calc.classify_confidence(0.7, "flat")
        boosted = calc.classify_confidence(0.7, "improving")
        assert boosted > base

    def test_aligned_bearish_boosts_confidence(self):
        base = calc.classify_confidence(-0.7, "flat")
        boosted = calc.classify_confidence(-0.7, "deteriorating")
        assert boosted > base

    def test_opposed_trend_dampens_confidence(self):
        base = calc.classify_confidence(0.7, "flat")
        dampened = calc.classify_confidence(0.7, "deteriorating")
        assert dampened < base

    def test_clamped_at_one(self):
        result = calc.classify_confidence(1.0, "improving")
        assert result <= 1.0

    def test_clamped_at_zero(self):
        result = calc.classify_confidence(0.05, "deteriorating")
        assert result >= 0.0

    def test_score_above_one_clamped_before_boost(self):
        result = calc.classify_confidence(1.5, "improving")
        assert result == 1.0


# ---------------------------------------------------------------------------
# classify_key_levels
# ---------------------------------------------------------------------------


class TestClassifyKeyLevels:
    def test_level_below_price_is_support(self):
        candidates = [{"price": 90.0, "type": "sma_200"}]
        levels = calc.classify_key_levels(candidates, current_price=100.0)
        assert levels[0]["role"] == "support"

    def test_level_above_price_is_resistance(self):
        candidates = [{"price": 110.0, "type": "sma_200"}]
        levels = calc.classify_key_levels(candidates, current_price=100.0)
        assert levels[0]["role"] == "resistance"

    def test_significance_from_lookup(self):
        candidates = [{"price": 90.0, "type": "sma_200"}]
        levels = calc.classify_key_levels(candidates, current_price=100.0)
        assert levels[0]["significance"] == "high"

    def test_unknown_type_defaults_to_low_significance(self):
        candidates = [{"price": 90.0, "type": "custom_level"}]
        levels = calc.classify_key_levels(candidates, current_price=100.0)
        assert levels[0]["significance"] == "low"

    def test_note_is_string(self):
        candidates = [{"price": 90.0, "type": "sma_50"}]
        levels = calc.classify_key_levels(candidates, current_price=100.0)
        assert isinstance(levels[0]["note"], str)
        assert len(levels[0]["note"]) > 0

    def test_all_fields_present(self):
        candidates = [{"price": 105.0, "type": "bb_upper"}]
        levels = calc.classify_key_levels(candidates, current_price=100.0)
        level = levels[0]
        assert {"price", "type", "role", "significance", "note"}.issubset(level.keys())

    def test_empty_candidates_returns_empty(self):
        assert calc.classify_key_levels([], current_price=100.0) == []


# ---------------------------------------------------------------------------
# build_reasoning
# ---------------------------------------------------------------------------


class TestBuildReasoning:
    def test_none_score_returns_fallback(self):
        signal = _signal_row(composite_vix_adj=None)
        result = calc.build_reasoning(signal, "flat")
        assert "insufficient" in result.lower()

    def test_bullish_direction_in_output(self):
        signal = _signal_row(composite_vix_adj=0.6, rsi_14=55.0, vix_regime="normal")
        result = calc.build_reasoning(signal, "flat")
        assert "bullish" in result.lower()

    def test_bearish_direction_in_output(self):
        signal = _signal_row(composite_vix_adj=-0.6, rsi_14=55.0, vix_regime="normal")
        result = calc.build_reasoning(signal, "flat")
        assert "bearish" in result.lower()

    def test_rsi_oversold_mentioned(self):
        signal = _signal_row(composite_vix_adj=0.6, rsi_14=25.0, vix_regime="normal")
        result = calc.build_reasoning(signal, "flat")
        assert "oversold" in result.lower()

    def test_rsi_overbought_mentioned(self):
        signal = _signal_row(composite_vix_adj=-0.6, rsi_14=75.0, vix_regime="normal")
        result = calc.build_reasoning(signal, "flat")
        assert "overbought" in result.lower()

    def test_high_vix_mentioned(self):
        signal = _signal_row(composite_vix_adj=0.6, rsi_14=55.0, vix_regime="high")
        result = calc.build_reasoning(signal, "flat")
        assert "vix" in result.lower()

    def test_improving_trend_in_output(self):
        signal = _signal_row(composite_vix_adj=0.6, rsi_14=55.0, vix_regime="normal")
        result = calc.build_reasoning(signal, "improving")
        assert "strengthening" in result.lower()

    def test_result_is_string_ending_with_period(self):
        signal = _signal_row()
        result = calc.build_reasoning(signal, "flat")
        assert isinstance(result, str)
        assert result.endswith(".")


# ---------------------------------------------------------------------------
# build_brief_message
# ---------------------------------------------------------------------------


class TestBuildBriefMessage:
    def _make_rows(self):
        return [
            (
                "AAPL",
                "bullish",
                0.82,
                "Strengthening bullish signal (0.82).",
                0.82,
                "normal",
                "stable",
                "clean_fear",
            ),
            (
                "TSLA",
                "bearish",
                0.75,
                "Weakening bearish signal (0.75).",
                -0.75,
                "elevated",
                "expanding",
                "elevated",
            ),
        ]

    def test_contains_date(self):
        msg = prompt_builder.build_brief_message(self._make_rows(), 10, "2024-06-01")
        assert "2024-06-01" in msg

    def test_contains_total_count(self):
        msg = prompt_builder.build_brief_message(self._make_rows(), 10, "2024-06-01")
        assert "10" in msg

    def test_bullish_section_present(self):
        msg = prompt_builder.build_brief_message(self._make_rows(), 10, "2024-06-01")
        assert "BULLISH" in msg
        assert "AAPL" in msg

    def test_bearish_section_present(self):
        msg = prompt_builder.build_brief_message(self._make_rows(), 10, "2024-06-01")
        assert "BEARISH" in msg
        assert "TSLA" in msg

    def test_market_context_present(self):
        msg = prompt_builder.build_brief_message(self._make_rows(), 10, "2024-06-01")
        assert "MARKET CONTEXT" in msg

    def test_brief_system_is_non_empty_string(self):
        assert isinstance(prompt_builder.BRIEF_SYSTEM, str)
        assert len(prompt_builder.BRIEF_SYSTEM) > 50
