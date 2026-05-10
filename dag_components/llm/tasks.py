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
    log.info("Selected %d tickers for analysis on %s", len(tickers), target_dt)
    return tickers


@task()
def analyze_and_upsert(tickers: list[str]) -> int:
    """Compute per-ticker bias, confidence, key levels, and reasoning algorithmically."""
    if not tickers:
        log.info("No tickers to analyze.")
        return 0

    context = get_current_context()
    target_dt = context["data_interval_start"].date()
    history_start = target_dt - timedelta(days=config.LLM_SIGNAL_HISTORY_DAYS)
    price_start = target_dt - timedelta(days=400)

    hook = PostgresHook(postgres_conn_id="signal_postgres")

    # batch-load signal history (includes close via raw_prices join)
    sig_rows = hook.get_records(
        """
        SELECT ss.ticker, ss.date, rp.close, ss.composite_vix_adj, ss.rsi_14, ss.macd_hist,
               ss.vix_close, ss.vvix_close, ss.vix_regime, ss.vix_trend, ss.vol_environment,
               ss.sma_50, ss.sma_200, ss.bb_upper, ss.bb_lower
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
        (
            t,
            dt,
            close,
            cvxa,
            rsi,
            macd_h,
            vix_c,
            vvix_c,
            vix_r,
            vix_tr,
            vol_e,
            s50,
            s200,
            bbu,
            bbl,
        ) = row
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
                "sma_50": float(s50) if s50 is not None else None,
                "sma_200": float(s200) if s200 is not None else None,
                "bb_upper": float(bbu) if bbu is not None else None,
                "bb_lower": float(bbl) if bbl is not None else None,
            }
        )

    # batch-load raw prices (high/low for key level extremes)
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

    # compute algorithmically
    results: list[tuple] = []
    for ticker in tickers:
        signal_history = signals_by_ticker.get(ticker, [])
        if not signal_history:
            log.info("%s: no signal history, skipping", ticker)
            continue

        current_signal = signal_history[-1]
        close_val = current_signal.get("close")
        if close_val is None:
            log.info("%s: no close price, skipping", ticker)
            continue

        trend = calc.signal_trend(signal_history)
        candidates = calc.build_key_level_candidates(
            current_signal, prices_by_ticker.get(ticker, [])
        )
        key_levels = calc.classify_key_levels(candidates, float(close_val))
        bias = calc.classify_bias(current_signal.get("composite_vix_adj"))
        confidence = calc.classify_confidence(current_signal.get("composite_vix_adj"), trend)
        reasoning = calc.build_reasoning(current_signal, trend)

        results.append(
            (
                ticker,
                str(target_dt),
                bias,
                confidence,
                json.dumps(key_levels),
                reasoning,
                "algorithmic",
                0,
            )
        )

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
    log.info("Upserted %d algorithmic analysis rows for %s", len(results), target_dt)
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

    user_content = prompt_builder.build_brief_message(rows, count, target_dt)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL_ANALYSIS,
        max_tokens=500,
        system=prompt_builder.BRIEF_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    brief_text = response.content[0].text

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
