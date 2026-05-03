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
# build_user_message
# ---------------------------------------------------------------------------


class TestBuildUserMessage:
    def _make_message(self, **kwargs):
        signal = _signal_row(**kwargs)
        candidates = calc.build_key_level_candidates(signal, _price_rows(30))
        return prompt_builder.build_user_message(
            ticker="AAPL",
            target_date="2024-06-01",
            current_signal=signal,
            signal_history=[signal] * 5,
            key_candidates=candidates,
            peers=[{"peer": "MSFT", "pearson_r": 0.82}],
        )

    def test_contains_ticker(self):
        msg = self._make_message()
        assert "AAPL" in msg

    def test_contains_date(self):
        msg = self._make_message()
        assert "2024-06-01" in msg

    def test_contains_key_level_types(self):
        msg = self._make_message()
        assert "sma_50" in msg
        assert "sma_200" in msg

    def test_contains_peer(self):
        msg = self._make_message()
        assert "MSFT" in msg

    def test_contains_vix_regime(self):
        msg = self._make_message()
        assert "normal" in msg

    def test_no_candidates_shows_placeholder(self):
        signal = _signal_row(sma_50=None, sma_200=None, bb_upper=None, bb_lower=None)
        msg = prompt_builder.build_user_message(
            ticker="XYZ",
            target_date="2024-06-01",
            current_signal=signal,
            signal_history=[signal],
            key_candidates=[],
            peers=[],
        )
        assert "none available" in msg.lower()

    def test_no_peers_shows_placeholder(self):
        signal = _signal_row()
        msg = prompt_builder.build_user_message(
            ticker="XYZ",
            target_date="2024-06-01",
            current_signal=signal,
            signal_history=[signal],
            key_candidates=[],
            peers=[],
        )
        assert "no peer data" in msg.lower()


# ---------------------------------------------------------------------------
# prompt_builder constants
# ---------------------------------------------------------------------------


class TestPromptBuilderConstants:
    def test_cached_system_has_cache_control(self):
        assert len(prompt_builder.CACHED_SYSTEM) == 1
        block = prompt_builder.CACHED_SYSTEM[0]
        assert block["type"] == "text"
        assert block["cache_control"] == {"type": "ephemeral"}
        assert len(block["text"]) > 50

    def test_analysis_tool_has_required_fields(self):
        tool = prompt_builder.ANALYSIS_TOOL
        assert tool["name"] == "record_analysis"
        schema = tool["input_schema"]
        required = schema["required"]
        assert set(required) == {"bias", "confidence", "key_levels", "reasoning"}

    def test_analysis_tool_bias_enum(self):
        tool = prompt_builder.ANALYSIS_TOOL
        bias_prop = tool["input_schema"]["properties"]["bias"]
        assert set(bias_prop["enum"]) == {"bullish", "bearish", "neutral"}
