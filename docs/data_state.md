# Data State — Project Signal

_Snapshot: 2026-07-08. Describes the state of the local dev database at the point
the pipeline went dormant, and the remedy for standing it back up on AWS._

---

## Current state

- **Last data:** `raw_prices` ends **2026-06-23**. The nightly pipeline has been
  dormant since; no DAG runs after that date.
- **prediction_outcomes:** ~5.76M rows. ~482k rows are **unresolved**
  (`resolved_at IS NULL`) — a mix of (a) predictions whose forward horizon has
  not yet matured and (b) seam-quarantined rows. Quarantine is **not a stored
  flag**: the v2 resolver simply leaves a row unresolved when its forward price
  window contains a 1-day ratio beyond `V2_MAX_DAILY_RATIO` (a corporate-action
  seam). The backfill run that produced this table reported ~23k rows quarantined
  in-run; those remain undrained and will only resolve after the affected tickers
  are re-based by a full re-backfill.
- **signal_features:** populated full-history (~451k rows, Phase 2+3).
- **sector_beta / relatedness_matrix:** recomputed post-bug-fix on masked returns.

## Known integrity risk at the dormancy boundary

Stored adjusted prices are valid only as of fetch time. Any ticker that had a
**corporate action (split/dividend) after ~June 2026** now has an **adjustment
seam** at the gap boundary: rows before the action are in the old adjustment
epoch, rows after (whenever the pipeline resumes) will be in the new one. Returns
computed across that seam are invalid. The nightly `refresh_adjusted_history`
task (the seam guard) only catches actions detected while the pipeline is
*running* — it cannot retroactively cover the dormant window.

## Remedy — full re-backfill as the AWS inaugural act

Do **not** resume incremental nightly ingest on top of the stale local data.
The inaugural act on AWS is a full re-backfill from a fresh database, which
re-bases every ticker to a single current adjustment epoch and eliminates all
seams (including the dormancy-boundary ones).

### Canonical rebuild chain (run in this exact order)

Each stage reads the output of the previous one; the order is a hard dependency,
not a preference.

```
backfill_eodhd         → raw_prices           (full OHLCV history, single adjustment epoch)
backfill_indicators    → stock_signals        (SMA/MACD/RSI/BB + composite)
backfill_features      → signal_features       (Phase 2+3 continuous features + regime)
backfill_llm_analysis  → llm_analysis          (algorithmic classification; no Anthropic calls)
backfill_predictions   → signal_predictions    (v1.0-backfill; reads llm_analysis history)
backfill_outcomes_v2   → prediction_outcomes   (v2 scoreboard; reads raw_prices + SPY + beta)
dag_stock_relatedness  → sector_beta + relatedness_matrix  (trigger the DAG; masked returns)
```

Heavy stages (`backfill_features`, `backfill_outcomes_v2`, `analyze_*`) run in the
`signal-tools` container, memory-isolated from the scheduler.

## Survivorship caveat

A fresh EODHD backfill pulls **current listings only**. Tickers that were listed
during the historical window but have since been **delisted, merged, or renamed**
will be absent from the rebuilt `raw_prices`. The rebuilt history therefore has a
**survivorship bias**: it over-represents names that survived to the rebuild date.
This matters for any backtest or accuracy metric computed off the rebuilt data —
delisting is a real outcome (often the worst one) and its absence inflates
apparent returns. The local dormant database retains the pre-dormancy universe
snapshot; if survivorship-corrected history is ever needed, it must be preserved
before the fresh backfill overwrites it, or reconstructed from a point-in-time
listings source.
