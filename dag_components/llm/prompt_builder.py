"""Prompt construction for the generate_brief Sonnet call in dag_llm_analysis.

No Airflow or DB imports.
"""

from __future__ import annotations

BRIEF_SYSTEM = (
    "You are a market analyst producing a concise morning brief for a systematic "
    "trading pipeline. You receive the top-conviction technical signals from a nightly "
    "algorithmic analysis run. Produce a brief with:\n"
    "1. Two or three bullet points for the strongest bullish setups — name the ticker, "
    "the key driver, and one actionable price level if relevant\n"
    "2. Two or three bullet points for the strongest bearish setups — same format\n"
    "3. One sentence on market context (VIX regime, vol environment)\n"
    "Total length: under 200 words. Be specific and actionable."
)


def build_brief_message(rows: list, count: int, target_dt: object) -> str:
    """Build the user message string for the Sonnet daily brief call.

    rows: list of (ticker, bias, confidence, reasoning, composite_vix_adj,
                   vix_regime, vix_trend, vol_environment)
    """
    bullish = [r for r in rows if r[1] == "bullish"]
    bearish = [r for r in rows if r[1] == "bearish"]

    def _row_line(r) -> str:
        ticker, bias, conf, reasoning, cvxa, vix_r, vix_tr, vol_e = r
        return f"  {ticker} | conf={float(conf):.2f} | score={float(cvxa):.3f} | {reasoning}"

    sections = [f"DATE: {target_dt}", f"TOTAL ANALYZED: {count} tickers"]
    if bullish:
        sections += ["", "BULLISH SIGNALS:"] + [_row_line(r) for r in bullish]
    if bearish:
        sections += ["", "BEARISH SIGNALS:"] + [_row_line(r) for r in bearish]

    vix_regime = rows[0][5] or "unknown"
    vol_env = rows[0][7] or "unknown"
    sections += ["", f"MARKET CONTEXT: VIX regime={vix_regime} | vol_environment={vol_env}"]

    return "\n".join(sections)
