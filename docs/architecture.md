# Architecture — Project Signal

Nightly stock intelligence pipeline. Airflow orchestrates six independent DAGs that ingest market data, compute indicators and features, build a cross-asset relatedness matrix, run an LLM analysis layer, and track prediction accuracy. All market data flows through a single provider (EODHD). All schema changes go through numbered migration files.

---

## Pipeline overview

```
EODHD API (US equities + TSX/TSX-V + VIX/VVIX)
        │
        ▼
dag_stock_ingest          →  raw_prices, ticker_metadata
        │
        ▼
dag_stock_indicators      →  stock_signals, signal_features
        │
        ├── (Sundays only) ──►  dag_stock_relatedness  →  relatedness_matrix, sector_beta
        │
        ▼
dag_llm_analysis          →  llm_analysis, daily_brief
        │
        ▼
dag_outcome_tracker       →  signal_predictions, signal_accuracy, prediction_outcomes
        │
        └── (Sundays only) ──►  dag_parameter_review   →  (read-only; delivers brief to Jarvis stub)
```

`dag_orchestrator` runs on schedule `0 5 * * 0-5` (5 AM UTC, Sun–Fri). It triggers each sub-DAG in sequence and waits for completion before proceeding. All sub-DAGs have `schedule=None` — they are triggered exclusively from the orchestrator and never run independently unless kicked manually via the Airflow UI.

The Sunday branch adds `dag_stock_relatedness` (between indicators and llm) and `dag_parameter_review` (after outcome_tracker). Weekday runs skip both.

---

## DAG task flows

### dag_stock_ingest

```
validate_watchlist → fetch_ohlcv ─────────────→ validate_raw → upsert_raw_prices → refresh_adjusted_history
                   ↘ fetch_metadata (parallel)
```

- `validate_watchlist`: returns all equity tickers + VIX.INDX + VVIX.INDX from `config/watchlist.py`
- `fetch_ohlcv`: per-ticker EODHD `/eod/` requests; adjusted close used as `close`; O/H/L scaled by the same split/dividend factor
- `fetch_metadata`: per-ticker EODHD `/fundamentals/` General filter; skips tickers whose metadata is <7 days old
- `validate_raw`: asserts close > 0 and volume >= 0 per bar; raises if >20% of tickers are missing (circuit breaker)
- `upsert_raw_prices`: bulk `INSERT ... ON CONFLICT DO UPDATE` into `raw_prices`
- `refresh_adjusted_history`: polls EODHD `/splits/` and `/dividends/` for every ticker since yesterday; for any ticker with a new corporate action, re-fetches its full OHLCV history and upserts, resetting the adjustment epoch

### dag_stock_indicators

```
fetch_price_history → compute_indicators → upsert_stock_signals → compute_features
```

- `fetch_price_history`: reads `raw_prices` for all tickers + VIX/VVIX over `PRICE_HISTORY_DAYS + 30` days
- `compute_indicators`: computes SMA 50/200, MACD(12,26,9), RSI(14), Bollinger Bands(20, 2σ) per equity ticker; classifies VIX regime and VVIX environment globally for the day; computes `composite_score` and `composite_vix_adj`
- `upsert_stock_signals`: bulk upsert into `stock_signals`
- `compute_features`: computes Phase 2 + Phase 3 continuous features (11 numeric features + ADX + SMA200 slope + regime classification) for all tickers; bulk upsert into `signal_features`

### dag_stock_relatedness (Sundays only)

```
fetch_price_history → compute_and_upsert_correlations → compute_and_upsert_betas
```

- `fetch_price_history`: verifies `raw_prices` has data over the `RELATEDNESS_HISTORY_DAYS` (400d) window; returns count
- `compute_and_upsert_correlations`: reads full returns matrix from DB; for each window in `[30, 90, 365]`, TRUNCATEs `relatedness_matrix` then computes all equity-pair Pearson r values; writes pairs with |r| >= `RELATEDNESS_MIN_R` (0.20); chunked computation (`CORRELATION_CHUNK_SIZE = 500`) to avoid CPU spikes
- `compute_and_upsert_betas`: for each window in `[90, 365]`, computes beta for every equity × ETF pair; upserts into `sector_beta`; requires ≥10 overlapping observations per pair

Note: `compute_and_upsert_correlations` has `execution_timeout=timedelta(hours=2)`.

### dag_llm_analysis

```
select_tickers → analyze_and_upsert → generate_brief → push_to_jarvis
```

- `select_tickers`: selects tickers with `|composite_vix_adj| >= LLM_SIGNAL_THRESHOLD` (0.5), ordered by signal strength
- `analyze_and_upsert`: **algorithmic only — no LLM calls**; computes bias, confidence, key levels, and reasoning from `llm_analysis`, `stock_signals`, and `raw_prices`; writes to `llm_analysis` with `model = "algorithmic"`
- `generate_brief`: loads top `LLM_BRIEF_TOP_N` (12) non-neutral rows by confidence; calls Sonnet (`claude-sonnet-4-6`) with a 500-token limit; writes to `daily_brief`
- `push_to_jarvis`: stub; logs readiness; Phase 6 will deliver to Jarvis

### dag_outcome_tracker

```
populate_predictions → resolve_matured_predictions → compute_accuracy_rollup
                                     ↓
                              resolve_outcomes_v2
```

- `populate_predictions`: joins `llm_analysis + stock_signals + raw_prices` for the execution date; inserts non-neutral signals into `signal_predictions`; `ON CONFLICT DO NOTHING` (safe re-run)
- `resolve_matured_predictions`: for each unresolved prediction, checks if `raw_prices` has ≥ 20 rows after signal date; resolves `actual_price_5d/10d/20d`, `return_*`, `correct_*` fields; uses row-offset counting (not calendar arithmetic)
- `compute_accuracy_rollup`: **Sundays only** (weekday guard inside task); groups resolved predictions by `(signal_version, vix_regime, vol_environment, bias, horizon_days)`; writes groups with ≥ `ACCURACY_MIN_SAMPLE_SIZE` (30) to `signal_accuracy`
- `resolve_outcomes_v2`: seeds and resolves `prediction_outcomes` (v2 long-format); skips tickers with `price_at_signal < V2_MIN_PRICE` (1.00); quarantines horizons with a corporate-action seam in the forward window; looks up SPY beta from `sector_beta`, falls back to 1.0; runs `assign_episodes` per ticker to stamp episode metadata

### dag_parameter_review (Sundays only)

```
read_pending_proposals → generate_review_brief → push_review_to_jarvis
```

- `read_pending_proposals`: reads pending rows from `parameter_proposals`
- `generate_review_brief`: Sonnet-generated weekly parameter health report; includes accuracy trends and proposal recommendations; never auto-applies changes
- `push_review_to_jarvis`: stub

---

## Table inventory

All tables defined in `sql/schema.sql`. Writes are idempotent (`ON CONFLICT DO UPDATE` or `DO NOTHING`).

| Table | Primary Key | Purpose |
|---|---|---|
| `raw_prices` | `(ticker, date)` | Daily OHLCV bars; adjusted prices; source and currency tracking |
| `ticker_metadata` | `ticker` | Company name, sector, industry, market cap, exchange |
| `stock_signals` | `(ticker, date)` | All indicator values, VIX/VVIX context, composite scores |
| `relatedness_matrix` | `(ticker_a, ticker_b, window_days)` | Pearson r at 90/365-day windows; only pairs with \|r\| ≥ 0.50 (30d dropped, floor raised 0.20→0.50 in the 2026-07 AWS trim; no wired reader yet — see roadmap "wire or retire") |
| `sector_beta` | `(ticker, etf_proxy, window_days)` | Beta vs SPY/QQQ/XLK/XLF/XLE/XLV/XLY at 90/365d windows |
| `llm_analysis` | `(ticker, date)` | Algorithmic bias, confidence, key levels (JSONB), reasoning |
| `daily_brief` | `date` | Sonnet-generated morning brief; one row per trading day |
| `signal_predictions` | `(ticker, signal_date, signal_version)` | Signal snapshot + v1 forward-price outcomes (5/10/20d); `signal_version` allows v1/v2 rows to coexist |
| `signal_accuracy` | `(signal_version, vix_regime, vol_environment, bias, horizon_days)` | Weekly accuracy rollup by regime and environment |
| `parameter_proposals` | `id` (serial) | LLM-generated parameter change proposals pending human review |
| `prediction_outcomes` | `(ticker, signal_date, horizon_days)` | v2 long-format outcomes: vol-scaled, benchmark-adjusted, episodic |
| `signal_features` | `(ticker, date)` | v2 continuous features (Phase 2) and regime classification (Phase 3) |

### signal_predictions PK

The live PK is `(ticker, signal_date, signal_version)`, allowing v1.0, v2.0, and backfill variants to coexist on the same date. Migration `003` created the table with `(ticker, signal_date)`; migration `010_signal_predictions_version_pk.sql` widened it (for the v2 parallel run). `schema.sql` reflects the current live definition.

---

## v1 vs v2 dual-pipeline relationship

v1 is frozen at v1.0. The `compute_composite` function, `classify_confidence`, `classify_bias`, `SIGNAL_WEIGHTS` (flat global weights), and the v1 DAG task flow are not modified until Phase 6 cutover. v2 is built in parallel:

- v1 predictions write to `signal_predictions` with `signal_version = "v1.0"` (or `"v1.0-backfill"` for historical backfills)
- v2 outcomes write to `prediction_outcomes` (long-format, separate table)
- v2 features write to `signal_features` (separate table)
- v2 config lives under `# --- Signal v2 ---` in `config/config.py`
- v2 calculations live in `dag_components/features/calculations.py` and `dag_components/outcome_tracker/calculations_v2.py`

The two pipelines share `raw_prices`, `stock_signals`, and `sector_beta` as read-only inputs.

---

## Fresh-database rebuild chain (AWS inauguration sequence)

On a fresh database (no data), run the backfill scripts in this exact order — each
stage reads the output of the previous one. `backfill_predictions` reads `llm_analysis`
history, which is empty until `backfill_llm_analysis` runs, so the LLM stage is a
required dependency, not an optional utility.

```
backfill_eodhd         → raw_prices           (full OHLCV history, single adjustment epoch)
backfill_indicators    → stock_signals        (SMA/MACD/RSI/BB + composite)
backfill_features      → signal_features       (Phase 2+3 continuous features + regime)
backfill_llm_analysis  → llm_analysis          (algorithmic classification; no Anthropic calls)
backfill_predictions   → signal_predictions    (v1.0-backfill; reads llm_analysis)
backfill_outcomes_v2   → prediction_outcomes   (v2 scoreboard; reads raw_prices + SPY + beta)
dag_stock_relatedness  → sector_beta + relatedness_matrix  (trigger the DAG; masked returns)
```

Heavy stages (`backfill_features`, `backfill_outcomes_v2`, `analyze_*`) run in the
`signal-tools` container (`docker compose --profile tools run --rm signal-tools ...`),
which is memory-isolated from the scheduler.

## Environment hold list (pending AWS migration — not committed)

These local Windows/WSL operational workarounds are intentionally NOT committed; the
AWS migration rewires this layer and committing them now creates churn the migration
undoes. Recorded here so they are folded into the migration spec:

- `docker-compose.yml`: interim Airflow concurrency caps
  (`AIRFLOW__CORE__PARALLELISM=4`, `MAX_ACTIVE_TASKS_PER_DAG=4`,
  `MAX_ACTIVE_RUNS_PER_DAG=1`) and per-service `deploy.resources.limits` on
  `airflow-scheduler`/`airflow-webserver` — added to keep a heavy DAG run from
  exhausting the shared WSL VM and bouncing the Docker daemon. On AWS these become
  right-sized task/instance resources, not compose caps.
- `docs/RECOVERY.md`: WSL-crash + Docker bind-mount recovery runbook — specific to
  the local Windows/WSL2 host, superseded by the AWS deployment.

The `signal-tools` service itself IS committed — it is the service's design (a
bounded 8 GB analysis container), not a WSL workaround, and survives the migration.

---

## File map

```
project-signal/
├── dags/
│   ├── dag_orchestrator.py         # Master sequencer; schedule 0 5 * * 0-5
│   ├── dag_stock_ingest.py         # OHLCV + metadata + corp-actions refresh
│   ├── dag_stock_indicators.py     # Indicator computation + v2 features
│   ├── dag_stock_relatedness.py    # Correlation matrix + sector beta (Sundays)
│   ├── dag_llm_analysis.py         # Algorithmic analysis + Sonnet brief
│   ├── dag_outcome_tracker.py      # Prediction tracking + v2 outcomes
│   └── dag_parameter_review.py     # Sonnet parameter review (Sundays)
├── dag_components/
│   ├── dag_builder.py              # SignalDAG and DAGBuilder; @build decorator
│   ├── ingest/
│   │   ├── tasks.py                # validate_watchlist, fetch_ohlcv, fetch_metadata,
│   │   │                           #   validate_raw, upsert_raw_prices, refresh_adjusted_history
│   │   └── __init__.py
│   ├── indicators/
│   │   ├── calculations.py         # sma, ema, macd, rsi, bollinger_bands,
│   │   │                           #   classify_vix_regime, classify_vix_trend,
│   │   │                           #   classify_vol_environment, compute_composite
│   │   ├── tasks.py                # fetch_price_history, compute_indicators, upsert_stock_signals
│   │   └── __init__.py
│   ├── relatedness/
│   │   ├── calculations.py         # build_returns_matrix, correlation_pairs,
│   │   │                           #   _correlation_pairs_chunked, beta_values
│   │   ├── tasks.py                # fetch_price_history, compute_and_upsert_correlations,
│   │   │                           #   compute_and_upsert_betas
│   │   └── __init__.py
│   ├── llm/
│   │   ├── calculations.py         # build_key_level_candidates, classify_bias,
│   │   │                           #   classify_confidence, classify_key_levels,
│   │   │                           #   build_reasoning, signal_trend
│   │   ├── prompt_builder.py       # BRIEF_SYSTEM prompt; build_brief_message()
│   │   ├── tasks.py                # select_tickers, analyze_and_upsert,
│   │   │                           #   generate_brief, push_to_jarvis
│   │   └── __init__.py
│   ├── outcome_tracker/
│   │   ├── calculations.py         # is_correct, nth_trading_day_price,
│   │   │                           #   compute_return, compute_accuracy_rollup
│   │   ├── calculations_v2.py      # trailing_daily_vol, scaled_threshold,
│   │   │                           #   benchmark_window_return, excess_return,
│   │   │                           #   outcome_flags, assign_episodes, has_price_seam
│   │   ├── parameter_review_tasks.py  # read_pending_proposals, generate_review_brief,
│   │   │                              #   push_review_to_jarvis
│   │   ├── tasks.py                # populate_predictions, resolve_matured_predictions,
│   │   │                           #   compute_accuracy_rollup, resolve_outcomes_v2
│   │   └── __init__.py
│   ├── features/
│   │   ├── calculations.py         # compute_feature_frame, _adx, min_history_required
│   │   ├── tasks.py                # compute_features
│   │   └── __init__.py
│   └── __init__.py
├── plugins/
│   ├── base_client.py              # BaseMarketClient (backoff session); rate_limited_call decorator
│   ├── eodhdclient.py              # EODHDClient — fetch_ohlcv, fetch_splits, fetch_dividends,
│   │                               #   fetch_metadata; all market data flows through here
│   ├── routing.py                  # get_client_for_ticker, resolve_vix_tickers, is_index_ticker
│   ├── polygon_client.py           # RETAINED FOR REFERENCE — not active
│   └── yfinance_client.py          # RETAINED FOR REFERENCE — not active
├── config/
│   ├── config.py                   # All tunables: weights, thresholds, windows, model names,
│   │                               #   v2 config; parameter_overrides.json loaded at import
│   ├── watchlist.py                # Loads ticker_universe.json; sector ETFs hardcoded
│   ├── ticker_universe.json        # 6,983 tickers; 6,949 with has_data=True
│   ├── ticker_universe.json.bak    # Backup
│   └── parameter_overrides.json    # Approved proposals; starts as {}; grows via Phase 6
├── sql/
│   ├── schema.sql                  # All CREATE TABLE statements + indexes (fresh installs)
│   ├── init/01_create_signal_db.sh # Database initialization
│   └── migrations/                 # Numbered migration files (see migration log below)
├── scripts/
│   ├── backfill_eodhd.py           # Full OHLCV history backfill; --start flag; per-ticker commits
│   ├── backfill_indicators.py      # Full indicator history backfill over raw_prices
│   ├── backfill_predictions.py     # Retroactive signal_predictions from llm_analysis; v1.0-backfill
│   ├── backfill_features.py        # Full Phase 2+3 feature history backfill
│   ├── backfill_outcomes_v2.py     # Retroactive prediction_outcomes; --start flag; per-ticker commits
│   ├── backfill_llm_analysis.py    # Historical llm_analysis backfill
│   ├── optimize_parameters.py      # Grid search → parameter_proposals; never auto-applies
│   ├── analyze_regimes.py          # One-off Phase 3 regime validation (print-only)
│   └── analyze_events.py           # Event-study analysis (Phase 4 prerequisite)
├── tests/
│   ├── test_indicators.py          # SMA, EMA, MACD, RSI, BB, VIX regime/trend, vol env, composite
│   ├── test_relatedness.py         # build_returns_matrix, correlation_pairs, beta_values
│   ├── test_features.py            # Phase 2+3 feature calculations
│   ├── test_calculations_v2.py     # v2 outcome calculations: vol, threshold, excess, episodes, seams
│   ├── test_eodhd_client.py        # EODHDClient behavior
│   ├── test_polygon_client.py      # PolygonClient behavior (reference only)
│   ├── test_yfinance_client.py     # YFinanceClient behavior (reference only)
│   ├── test_llm_analysis.py        # Algorithmic analysis functions
│   └── test_outcome_tracker.py     # Prediction population, resolution, accuracy rollup
└── docs/
    ├── CLAUDE.md                   # (at repo root) Agent operating guide
    ├── design_patterns.md          # This codebase's coding patterns (mandatory reading before edits)
    ├── architecture.md             # This file
    ├── roadmap.md                  # Phase status and decision log
    ├── reference.md                # EODHD endpoints, model table, universe stats
    ├── signal_v2_rebuild.md        # Signal v2 full spec and phase definitions
    ├── signal_v2_phase0_findings.md
    ├── signal_v2_phase3_findings.md
    └── phase3_5_regime_conditional_tuning.md
```

---

## Key design decisions

### Single data source

All market data (US equities, TSX/TSX-V, VIX/VVIX) flows through `plugins/eodhdclient.py` and one API key. `PolygonClient` and `YFinanceClient` are retained in `plugins/` for reference only — they are not used by any active DAG or script. `plugins/routing.py:get_client_for_ticker()` always returns an `EODHDClient`.

### Adjusted-price epoch problem and the corporate-actions solution

EODHD serves split/dividend-adjusted prices. A price fetched on day D is adjusted relative to all corporate actions known as of day D. When a corporate action occurs after day D, EODHD retroactively changes the adjustment factor for all historical bars — but rows already stored in `raw_prices` carry the old adjustment basis. Any return computed across this seam uses incompatible price bases and is garbage.

**Phase 0 confirmed this was a real production defect:** tickers like PPCB, TBLT, MRIN, and IVP showed 10,000× returns in 10 days — impossible values that contaminated the accuracy rollup and the Phase 0 analysis.

**The solution** (implemented as part of Phase 1, live in `dag_stock_ingest`):
- `refresh_adjusted_history` polls EODHD splits/dividends endpoints nightly
- If any corporate action is found for a ticker since yesterday, the task re-fetches the ticker's full OHLCV history back to 2019-01-01 and upserts every row, resetting the entire stored history to a single adjustment epoch
- The quarantine guard in `resolve_outcomes_v2` (`has_price_seam`) detects remaining seams (1-day close ratio > `V2_MAX_DAILY_RATIO = 3.0`) and prevents those prediction windows from resolving until after the corporate-actions re-fetch

**Rule:** Never compute returns across a stored price series without verifying that the series has a single adjustment epoch. The quarantine guard enforces this at the v2 outcome level. v1 predictions are protected by the nightly corporate-actions refresh.

### Separation of concerns

Each DAG is independent: it can fail, be retried, or be skipped without corrupting the others. The orchestrator waits for each sub-DAG to complete before triggering the next. This means indicators always use the same night's prices; analysis always uses the same night's indicators; outcome tracking always runs after analysis has logged the day's predictions.

### Single throttle point

All API calls go through `plugins/base_client.py`. `BaseMarketClient._get_session()` configures exponential backoff (Retry: total=5, backoff_factor=2, status_forcelist=[429, 500–504]). No `time.sleep()` calls exist in any DAG, task, or calculations file.

### Config-driven everything

All tunables — indicator windows, thresholds, LLM model names, v2 parameters — live in `config/config.py`. Approved parameter proposals go to `config/parameter_overrides.json` and are loaded at config import time. Nothing numeric or behavioral is hardcoded in DAG, task, plugin, or calculations files.

### Pure-pandas math

All indicator and feature math uses pandas directly. No `pandas-ta` or other indicator libraries. `scikit-learn` is permitted only inside `scripts/` for offline model fitting; runtime scoring must use pandas/numpy dot products only. No sklearn imports in `dag_components/` or `plugins/`.

### Humans commit artifacts

`optimize_parameters.py` generates proposals and writes them to `parameter_proposals`. A human reviews them and commits approved changes to `parameter_overrides.json`. The same principle will apply to `train_signal_v2.py` (Phase 4) and `calibrate_confidence_v2.py` (Phase 5): scripts write JSON artifacts; humans commit them. Nothing self-applies to config.
