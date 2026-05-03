"""Task implementations for dag_llm_analysis."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import anthropic
from airflow.decorators import task
from airflow.operators.python import get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook
from psycopg2.extras import execute_values

from config import config
from dag_components.llm import calculations as calc
from dag_components.llm import prompt_builder

log = logging.getLogger(__name__)


@task()
def select_tickers() -> list[str]:
    """Return tickers whose abs(composite_vix_adj) meets the analysis threshold."""
    context = get_current_context()
    target_dt = context["data_interval_start"].date()

    hook = PostgresHook(postgres_conn_id="signal_postgres")
    rows = hook.get_records(
        """
        SELECT ticker
        FROM stock_signals
        WHERE date = %s
          AND composite_vix_adj IS NOT NULL
          AND ABS(composite_vix_adj) >= %s
        ORDER BY ABS(composite_vix_adj) DESC
        """,
        parameters=(target_dt, config.LLM_SIGNAL_THRESHOLD),
    )
    tickers = [r[0] for r in rows]
    log.info("Selected %d tickers for LLM analysis on %s", len(tickers), target_dt)
    return tickers


@task()
def analyze_and_upsert(tickers: list[str]) -> int:
    """Call Haiku for each selected ticker, upsert results to llm_analysis."""
    if not tickers:
        log.info("No tickers to analyze.")
        return 0

    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    history_start = target_dt - timedelta(days=config.LLM_SIGNAL_HISTORY_DAYS)
    price_start = target_dt - timedelta(days=400)

    hook = PostgresHook(postgres_conn_id="signal_postgres")

    # --- batch-load signal history -------------------------------------------
    sig_rows = hook.get_records(
        """
        SELECT ss.ticker, ss.date, rp.close, ss.composite_vix_adj, ss.rsi_14, ss.macd_hist,
               ss.vix_close, ss.vvix_close, ss.vix_regime, ss.vix_trend, ss.vol_environment
        FROM stock_signals ss
        JOIN raw_prices rp ON rp.ticker = ss.ticker AND rp.date = ss.date
        WHERE ss.ticker = ANY(%s)
          AND ss.date BETWEEN %s AND %s
        ORDER BY ss.ticker, ss.date
        """,
        parameters=(tickers, history_start, target_dt),
    )
    signals_by_ticker: dict[str, list[dict]] = {}
    for row in sig_rows:
        t, dt, close, cvxa, rsi, macd_h, vix_c, vvix_c, vix_r, vix_tr, vol_e = row
        signals_by_ticker.setdefault(t, []).append(
            {
                "date": str(dt),
                "close": float(close) if close is not None else None,
                "composite_vix_adj": float(cvxa) if cvxa is not None else None,
                "rsi_14": float(rsi) if rsi is not None else None,
                "macd_hist": float(macd_h) if macd_h is not None else None,
                "vix_close": float(vix_c) if vix_c is not None else None,
                "vvix_close": float(vvix_c) if vvix_c is not None else None,
                "vix_regime": vix_r,
                "vix_trend": vix_tr,
                "vol_environment": vol_e,
            }
        )

    # --- batch-load raw prices (high/low for key level extremes) -------------
    price_rows = hook.get_records(
        """
        SELECT ticker, date, high, low
        FROM raw_prices
        WHERE ticker = ANY(%s)
          AND date BETWEEN %s AND %s
        ORDER BY ticker, date
        """,
        parameters=(tickers, price_start, target_dt),
    )
    prices_by_ticker: dict[str, list[dict]] = {}
    for row in price_rows:
        t, dt, high, low = row
        prices_by_ticker.setdefault(t, []).append(
            {
                "date": str(dt),
                "high": float(high) if high is not None else None,
                "low": float(low) if low is not None else None,
            }
        )

    # --- batch-load peer correlations ----------------------------------------
    peer_rows = hook.get_records(
        """
        SELECT ticker_a, ticker_b, pearson_r
        FROM relatedness_matrix
        WHERE window_days = 90
          AND pearson_r >= %s
          AND (ticker_a = ANY(%s) OR ticker_b = ANY(%s))
        ORDER BY pearson_r DESC
        """,
        parameters=(config.LLM_PEER_CORRELATION_MIN_R, tickers, tickers),
    )
    ticker_set = set(tickers)
    peers_by_ticker: dict[str, list[dict]] = {}
    for ta, tb, r in peer_rows:
        r_val = float(r)
        if ta in ticker_set:
            peers_by_ticker.setdefault(ta, []).append({"peer": tb, "pearson_r": r_val})
        if tb in ticker_set:
            peers_by_ticker.setdefault(tb, []).append({"peer": ta, "pearson_r": r_val})

    # Trim to top-N peers per ticker
    for t in peers_by_ticker:
        peers_by_ticker[t] = sorted(
            peers_by_ticker[t], key=lambda x: x["pearson_r"], reverse=True
        )[: config.LLM_PEER_COUNT]

    # --- call LLM per ticker -------------------------------------------------
    client = anthropic.Anthropic()
    results: list[tuple] = []

    for ticker in tickers:
        signal_history = signals_by_ticker.get(ticker, [])
        if not signal_history:
            log.info("%s: no signal history, skipping", ticker)
            continue

        current_signal = signal_history[-1]
        candidates = calc.build_key_level_candidates(
            current_signal, prices_by_ticker.get(ticker, [])
        )
        user_msg = prompt_builder.build_user_message(
            ticker=ticker,
            target_date=str(target_dt),
            current_signal=current_signal,
            signal_history=signal_history,
            key_candidates=candidates,
            peers=peers_by_ticker.get(ticker, []),
        )

        try:
            response = client.messages.create(
                model=config.ANTHROPIC_MODEL_CLASSIFICATION,
                max_tokens=config.LLM_MAX_TOKENS,
                system=prompt_builder.CACHED_SYSTEM,
                tools=[prompt_builder.ANALYSIS_TOOL],
                tool_choice={"type": "tool", "name": "record_analysis"},
                messages=[{"role": "user", "content": user_msg}],
            )
        except anthropic.APIError as exc:
            log.warning("%s: API error — %s", ticker, exc)
            continue

        analysis = None
        for block in response.content:
            if block.type == "tool_use":
                analysis = block.input
                break

        if analysis is None:
            log.warning("%s: no tool_use block in response", ticker)
            continue

        results.append(
            (
                ticker,
                str(target_dt),
                analysis.get("bias"),
                analysis.get("confidence"),
                json.dumps(analysis.get("key_levels", [])),
                analysis.get("reasoning"),
                config.ANTHROPIC_MODEL_CLASSIFICATION,
                response.usage.input_tokens,
            )
        )
        log.debug("%s: bias=%s confidence=%.2f", ticker, analysis.get("bias"), analysis.get("confidence", 0))

    if not results:
        log.info("No analysis results to upsert.")
        return 0

    conn = hook.get_conn()
    cur = conn.cursor()
    execute_values(
        cur,
        """
        INSERT INTO llm_analysis
            (ticker, date, bias, confidence, key_levels, reasoning, model, prompt_tokens)
        VALUES %s
        ON CONFLICT (ticker, date) DO UPDATE SET
            bias          = EXCLUDED.bias,
            confidence    = EXCLUDED.confidence,
            key_levels    = EXCLUDED.key_levels,
            reasoning     = EXCLUDED.reasoning,
            model         = EXCLUDED.model,
            prompt_tokens = EXCLUDED.prompt_tokens,
            created_at    = NOW()
        """,
        results,
        page_size=500,
    )
    conn.commit()
    cur.close()
    log.info("Upserted %d LLM analysis rows for %s", len(results), target_dt)
    return len(results)


@task()
def generate_brief(count: int) -> str | None:
    """Read top llm_analysis results, call Sonnet for a concise daily brief."""
    if not count:
        log.info("generate_brief: no analysis rows — skipping.")
        return None

    context = get_current_context()
    target_dt = context["data_interval_start"].date()

    hook = PostgresHook(postgres_conn_id="signal_postgres")

    # Top N non-neutral tickers by confidence, joined with composite score
    rows = hook.get_records(
        """
        SELECT la.ticker, la.bias, la.confidence, la.reasoning,
               ss.composite_vix_adj, ss.vix_regime, ss.vix_trend, ss.vol_environment
        FROM llm_analysis la
        JOIN stock_signals ss ON ss.ticker = la.ticker AND ss.date = la.date
        WHERE la.date = %s
          AND la.bias != 'neutral'
        ORDER BY la.confidence DESC
        LIMIT %s
        """,
        parameters=(target_dt, config.LLM_BRIEF_TOP_N),
    )

    if not rows:
        log.info("generate_brief: no non-neutral results for %s", target_dt)
        return None

    # Build the briefing prompt
    bullish = [r for r in rows if r[1] == "bullish"]
    bearish = [r for r in rows if r[1] == "bearish"]

    def _row_line(r) -> str:
        ticker, bias, conf, reasoning, cvxa, vix_r, vix_tr, vol_e = r
        return (
            f"  {ticker} | conf={conf:.2f} | composite_vix_adj={float(cvxa):.3f} | "
            f"vix={vix_r}/{vix_tr} | {reasoning}"
        )

    sections = [f"DATE: {target_dt}", f"TOTAL ANALYZED: {count} tickers"]
    if bullish:
        sections += ["", "BULLISH SIGNALS:"] + [_row_line(r) for r in bullish]
    if bearish:
        sections += ["", "BEARISH SIGNALS:"] + [_row_line(r) for r in bearish]

    # First row's VIX context represents the day (same for all tickers)
    vix_regime = rows[0][5] or "unknown"
    vol_env = rows[0][7] or "unknown"
    sections += [
        "",
        f"MARKET CONTEXT: VIX regime={vix_regime} | vol_environment={vol_env}",
    ]

    user_content = "\n".join(sections)

    system = (
        "You are a market analyst producing a concise morning brief for a systematic "
        "trading pipeline. You receive the top-conviction signals from a nightly "
        "technical analysis run. Produce a brief with:\n"
        "1. Two or three bullet points for the strongest bullish setups\n"
        "2. Two or three bullet points for the strongest bearish setups\n"
        "3. One sentence on market context (VIX regime, vol environment)\n"
        "Be specific: name the ticker, the key driver, and one actionable price level "
        "if relevant. Total length: under 200 words."
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL_ANALYSIS,
        max_tokens=500,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    brief_text = response.content[0].text

    # Persist to daily_brief
    conn = hook.get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO daily_brief (date, brief_text, ticker_count, model)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (date) DO UPDATE SET
            brief_text   = EXCLUDED.brief_text,
            ticker_count = EXCLUDED.ticker_count,
            model        = EXCLUDED.model,
            created_at   = NOW()
        """,
        (target_dt, brief_text, count, config.ANTHROPIC_MODEL_ANALYSIS),
    )
    conn.commit()
    cur.close()

    log.info("Daily brief for %s:\n%s", target_dt, brief_text)
    return brief_text


@task()
def push_to_jarvis(brief: str | None) -> None:
    """Stub — Phase 6 will push the daily brief to Jarvis."""
    if brief:
        log.info("push_to_jarvis: brief ready (%d chars) — Phase 6 will deliver this", len(brief))
    else:
        log.info("push_to_jarvis: no brief generated today")
