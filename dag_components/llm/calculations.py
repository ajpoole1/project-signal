"""Pure calculation functions for dag_llm_analysis.

No Airflow imports. No LLM calls. Operates on plain dicts.
"""

from __future__ import annotations


def build_key_level_candidates(
    signal_row: dict,
    price_rows: list[dict],
) -> list[dict]:
    """Return candidate price levels for LLM classification.

    Candidates come from two sources:
    - stock_signals: sma_50, sma_200, bb_upper, bb_lower
    - raw_prices: 52-week high/low (252 bars), 20-day high/low

    Returns list of {"price": float, "type": str} dicts.
    Levels with None values are omitted.
    """
    candidates: list[dict] = []

    for level_type in ("sma_50", "sma_200", "bb_upper", "bb_lower"):
        val = signal_row.get(level_type)
        if val is not None:
            candidates.append({"price": round(float(val), 4), "type": level_type})

    if price_rows:
        recent_20 = price_rows[-20:]
        highs_20 = [float(r["high"]) for r in recent_20 if r.get("high") is not None]
        lows_20 = [float(r["low"]) for r in recent_20 if r.get("low") is not None]
        if highs_20:
            candidates.append({"price": round(max(highs_20), 4), "type": "20d_high"})
        if lows_20:
            candidates.append({"price": round(min(lows_20), 4), "type": "20d_low"})

        # 52-week extremes require at least 252 bars to be meaningful
        if len(price_rows) >= 252:
            recent_252 = price_rows[-252:]
            highs_252 = [float(r["high"]) for r in recent_252 if r.get("high") is not None]
            lows_252 = [float(r["low"]) for r in recent_252 if r.get("low") is not None]
            if highs_252:
                candidates.append({"price": round(max(highs_252), 4), "type": "52w_high"})
            if lows_252:
                candidates.append({"price": round(min(lows_252), 4), "type": "52w_low"})

    return candidates


def signal_trend(signal_history: list[dict]) -> str:
    """Return 'improving', 'deteriorating', or 'flat' from composite_vix_adj trajectory."""
    scores = [
        float(r["composite_vix_adj"])
        for r in signal_history
        if r.get("composite_vix_adj") is not None
    ]
    if len(scores) < 2:
        return "flat"
    delta = scores[-1] - scores[0]
    if delta > 0.1:
        return "improving"
    if delta < -0.1:
        return "deteriorating"
    return "flat"
