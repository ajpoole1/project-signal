"""Prompt construction for dag_llm_analysis.

Keeps all text and tool schemas out of tasks.py. No Airflow or DB imports.
"""

from __future__ import annotations

from dag_components.llm import calculations as calc

SYSTEM_PROMPT = """You are a technical analysis engine in an automated stock signal pipeline.

For each ticker you receive a structured snapshot: recent composite signal scores, VIX regime,
pre-computed price levels, and top correlated peers. Your job is to call the `record_analysis`
tool with a structured assessment.

Rules:
- bias: base this on the composite_vix_adj trajectory and RSI context, not a single day
- confidence: 0.0–1.0; above 0.8 requires clear alignment across multiple indicators
- key_levels: classify every candidate provided — do not invent prices
  - role: "support" | "resistance" | "neutral"
  - significance: "high" | "medium" | "low" (relative to current price action)
  - note: one sentence on why this level matters right now
- reasoning: 1–2 sentences on the primary driver of your bias
- peers are context only; do not override a clear individual signal with peer noise"""

CACHED_SYSTEM = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]

ANALYSIS_TOOL = {
    "name": "record_analysis",
    "description": "Record the structured technical analysis for a ticker.",
    "input_schema": {
        "type": "object",
        "properties": {
            "bias": {
                "type": "string",
                "enum": ["bullish", "bearish", "neutral"],
                "description": "Directional bias given the signal context.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence in the bias, 0.0 to 1.0.",
            },
            "key_levels": {
                "type": "array",
                "description": "Classification of each provided price level candidate.",
                "items": {
                    "type": "object",
                    "properties": {
                        "price": {"type": "number"},
                        "type": {"type": "string"},
                        "role": {
                            "type": "string",
                            "enum": ["support", "resistance", "neutral"],
                        },
                        "significance": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["price", "type", "role", "significance", "note"],
                },
            },
            "reasoning": {
                "type": "string",
                "description": "1–2 sentences on the primary signal driver.",
            },
        },
        "required": ["bias", "confidence", "key_levels", "reasoning"],
    },
}


def build_user_message(
    ticker: str,
    target_date: str,
    current_signal: dict,
    signal_history: list[dict],
    key_candidates: list[dict],
    peers: list[dict],
) -> str:
    """Build the per-ticker user message string."""
    close = current_signal.get("close")
    close_str = f"{float(close):.2f}" if close is not None else "n/a"

    composite = current_signal.get("composite_vix_adj")
    composite_str = f"{float(composite):.3f}" if composite is not None else "n/a"

    rsi = current_signal.get("rsi_14")
    rsi_str = f"{float(rsi):.1f}" if rsi is not None else "n/a"

    trend = calc.signal_trend(signal_history)

    lines = [
        f"Ticker: {ticker} | Date: {target_date} | Close: {close_str}",
        "",
        f"COMPOSITE SIGNAL: current={composite_str} | RSI={rsi_str} | trend={trend}",
        f"VIX: close={_fmt(current_signal.get('vix_close'))} | "
        f"regime={current_signal.get('vix_regime', 'n/a')} | "
        f"trend={current_signal.get('vix_trend', 'n/a')}",
        f"VVIX: close={_fmt(current_signal.get('vvix_close'))} | "
        f"vol_env={current_signal.get('vol_environment', 'n/a')}",
    ]

    if len(signal_history) > 1:
        lines += ["", f"SIGNAL HISTORY (last {len(signal_history)} trading days):"]
        for row in signal_history:
            score = row.get("composite_vix_adj")
            macd_h = row.get("macd_hist")
            lines.append(
                f"  {row.get('date', '?')}: "
                f"composite_vix_adj={_fmt(score)} "
                f"macd_hist={_fmt(macd_h, 5)}"
            )

    lines += ["", "KEY LEVEL CANDIDATES (prices to classify):"]
    if key_candidates:
        for kl in key_candidates:
            lines.append(f"  {kl['type']}: {kl['price']:.4f}")
    else:
        lines.append("  (none available)")

    lines += ["", "TOP PEERS (90-day Pearson r ≥ threshold):"]
    if peers:
        for p in peers:
            lines.append(f"  {p['peer']}: r={p['pearson_r']:.3f}")
    else:
        lines.append("  (no peer data)")

    return "\n".join(lines)


def _fmt(val, precision: int = 2) -> str:
    if val is None:
        return "n/a"
    try:
        return f"{float(val):.{precision}f}"
    except (TypeError, ValueError):
        return "n/a"
