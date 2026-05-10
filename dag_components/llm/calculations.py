"""Pure calculation functions for dag_llm_analysis.

No Airflow imports. No LLM calls. Operates on plain dicts.
"""

from __future__ import annotations

_LEVEL_SIGNIFICANCE: dict[str, str] = {
    "sma_200": "high",
    "sma_50": "medium",
    "bb_upper": "medium",
    "bb_lower": "medium",
    "52w_high": "high",
    "52w_low": "high",
    "20d_high": "low",
    "20d_low": "low",
}


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


def classify_bias(score: float | None) -> str:
    """Derive directional bias from composite_vix_adj."""
    if score is None:
        return "neutral"
    if score > 0:
        return "bullish"
    if score < 0:
        return "bearish"
    return "neutral"


def classify_confidence(score: float | None, trend: str) -> float:
    """Score 0.0–1.0 from signal strength, boosted/dampened by trend alignment."""
    if score is None:
        return 0.0
    base = min(abs(score), 1.0)
    aligned = (trend == "improving" and score > 0) or (trend == "deteriorating" and score < 0)
    opposed = (trend == "improving" and score < 0) or (trend == "deteriorating" and score > 0)
    if aligned:
        base = min(base + 0.10, 1.0)
    elif opposed:
        base = max(base - 0.10, 0.0)
    return round(base, 2)


def classify_key_levels(candidates: list[dict], current_price: float) -> list[dict]:
    """Enrich each candidate with role, significance, and a descriptive note."""
    result = []
    for c in candidates:
        level_type = c["type"]
        price = c["price"]
        role = "support" if current_price >= price else "resistance"
        result.append(
            {
                "price": price,
                "type": level_type,
                "role": role,
                "significance": _LEVEL_SIGNIFICANCE.get(level_type, "low"),
                "note": _key_level_note(level_type, role, price),
            }
        )
    return result


def _key_level_note(level_type: str, role: str, price: float) -> str:
    notes: dict[tuple[str, str], str] = {
        ("sma_200", "support"): f"Long-term uptrend intact; SMA-200 at {price:.2f} is floor",
        ("sma_200", "resistance"): f"Below SMA-200 at {price:.2f}; long-term trend bearish",
        ("sma_50", "support"): f"Short-term momentum supported above SMA-50 at {price:.2f}",
        ("sma_50", "resistance"): f"SMA-50 at {price:.2f} capping near-term recovery",
        ("bb_upper", "resistance"): f"Approaching upper band at {price:.2f}; overbought risk",
        ("bb_upper", "support"): f"Above upper band at {price:.2f}; momentum breakout",
        ("bb_lower", "support"): f"Near lower band at {price:.2f}; oversold support zone",
        ("bb_lower", "resistance"): f"Below lower band at {price:.2f}; breakdown confirmed",
        ("52w_high", "resistance"): f"52-week high at {price:.2f} is key overhead resistance",
        ("52w_high", "support"): f"Holding above 52-week high at {price:.2f}; breakout confirmed",
        ("52w_low", "support"): f"52-week low at {price:.2f}; long-term support",
        ("52w_low", "resistance"): f"Below 52-week low at {price:.2f}; multi-year breakdown",
        ("20d_high", "resistance"): f"Recent swing high at {price:.2f}",
        ("20d_high", "support"): f"Holding above recent swing high at {price:.2f}",
        ("20d_low", "support"): f"Recent swing low at {price:.2f}",
        ("20d_low", "resistance"): f"Below recent swing low at {price:.2f}; near-term weak",
    }
    return notes.get((level_type, role), f"{level_type.replace('_', ' ')} at {price:.2f}")


def build_reasoning(current_signal: dict, trend: str) -> str:
    """Generate a concise reasoning string from signal data."""
    score = current_signal.get("composite_vix_adj")
    rsi = current_signal.get("rsi_14")
    vix_regime = current_signal.get("vix_regime") or "normal"

    if score is None:
        return "Insufficient signal data."

    direction = "bullish" if score > 0 else "bearish"
    trend_word = {"improving": "strengthening", "deteriorating": "weakening", "flat": "stable"}.get(
        trend, "stable"
    )
    parts = [f"{trend_word.capitalize()} {direction} signal ({abs(score):.2f})"]

    if rsi is not None:
        if rsi < 30:
            parts.append("RSI oversold")
        elif rsi > 70:
            parts.append("RSI overbought")
        elif rsi < 40:
            parts.append("RSI recovering")

    if vix_regime in ("high", "extreme"):
        parts.append(f"VIX {vix_regime} — macro risk elevated")
    elif vix_regime == "low":
        parts.append("VIX low — complacent conditions")

    return "; ".join(parts) + "."


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
