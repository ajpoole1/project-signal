# Signal v2 Rebuild — Measurement, Features, Regime-Gated Composite

**Status:** Planning → Phase 0
**Owner:** AJ
**Spec location:** `docs/signal_v2_rebuild.md`
**Supersedes:** the v1 composite scoring path (`compute_composite`, `classify_confidence`) — but only after Phase 6 cutover. v1 keeps running untouched until then.

---

## Why this rebuild exists

The v1 composite is underperforming and the evaluation layer can't tell us why. Confirmed problems, in order of severity:

1. **Measurement distortion.** The fixed ±1% threshold creates a dead zone: any move in (−1%, +1%) counts as a miss for *both* biases. At 5d horizons a large fraction of stocks land there, mechanically capping accuracy below 50% regardless of signal quality. Accuracy improving monotonically with horizon (40% → 47%) is the fingerprint of this artifact.
2. **No benchmark adjustment.** Raw returns conflate stock selection with market drift. Bearish signals look uniformly broken partly because the market rises.
3. **Philosophical contradiction in the composite.** Three of four sub-signals (SMA50, SMA200, MACD) are trend-following; RSI is contrarian. The system's thesis is mean reversion (buy systematic value lows, sell out-of-norm highs), but the ideal value entry — beaten-down price below both SMAs, negative MACD, oversold RSI — scores **−0.60 strong bearish**. The composite inverts the thesis.
4. **Binary sub-signals discard magnitude.** 0.2% above SMA200 scores identically to 25% above. For a reversion system, displacement magnitude IS the signal.
5. **Confidence is non-discriminating.** Empirically flat across buckets (high-conf bullish 45.0% at 20d vs low-conf 46.9% — slightly *inverted*, consistent with stretched scores marking extended names due to pull back). Confidence = |score| ± trend bonus is an invented number, not an earned one.
6. **Horizon mismatch.** 5/10/20d horizons evaluate a longer-term value thesis on swing-trade timescales.
7. **Inflated sample counts.** Consecutive same-bias signals on one ticker share overlapping forward windows; 224k rows ≠ 224k independent observations.

**Strategy:** fix the scoreboard first (Phases 0–1), then build the new signal (Phases 2–5), then cut over with v1/v2 running in parallel (Phase 6). Never optimize a new composite against the distorted v1 metric.

---

## Ground rules for implementation (Claude Code: read before every phase)

These extend `CLAUDE.md` — all of its rules still apply. Additional rules for this workstream:

- **Do not modify v1 scoring.** `compute_composite`, `classify_confidence`, `classify_bias`, `SIGNAL_WEIGHTS`, and the v1 DAG task flow stay byte-identical until Phase 6. v2 is built alongside, never on top.
- **All new tunables go in `config/config.py`** under a clearly marked `# --- Signal v2 ---` section. Nothing hardcoded in calculations, tasks, or scripts.
- **Pure functions in `calculations.py` modules, no Airflow imports.** Same separation as v1: `dag_components/<area>/calculations.py` holds testable math; `tasks.py` holds `@task` wrappers.
- **Every new module gets a test file.** Tests for pure functions use synthetic fixtures — no DB, no network. Run `ruff check .`, `ruff format --check .`, and `pytest` before declaring any phase complete.
- **All DB writes idempotent** (`ON CONFLICT DO UPDATE` / `DO NOTHING` as specified per table). No `DELETE`/`TRUNCATE` in DAG tasks (the existing relatedness TRUNCATE exception stands; do not add new ones).
- **Schema changes are migration files** in `sql/migrations/`, numbered sequentially (next: `005_…`). Also reflected in `sql/schema.sql` for fresh installs.
- **Model training is offline, runtime scoring is pandas/numpy only.** scikit-learn may be used in `scripts/` for fitting (it's already a project-adjacent dependency), but fitted coefficients are exported to JSON and runtime scoring is a dot product. No sklearn imports in `dag_components/` or `plugins/`.
- **No auto-applied parameters.** Same philosophy as `parameter_overrides.json`: training scripts write artifacts; a human commits them.
- **Versioning:** all v2 predictions are tagged `signal_version = "v2.0"` (backfills: `"v2.0-backfill"`). Bump minor on threshold/calibration changes, major on feature/model changes. Update `SIGNAL_VERSION_V2` in config.
- **Update the phase-status table at the bottom of this doc** as phases complete, same convention as `CLAUDE.md`.

---

## Phase 0 — Diagnostics (SQL only, zero code changes)

**Goal:** quantify how much of the sub-45% number is measurement artifact vs genuine anti-signal, and measure each indicator's standalone predictive power. Findings determine Phase 2 feature selection and Phase 4 channel design.

**Deliverable:** `docs/signal_v2_phase0_findings.md` — a short findings doc with the query outputs as tables and one-paragraph interpretations. No production code changes.

**Run these against the live `signal` DB** (read-only; safe anytime):

### 0.1 Three-way outcome breakdown (the dead-zone measurement)

```sql
-- For each bias × horizon: fraction up >1%, down >1%, and dead-zone (|move| <= 1%)
SELECT
    bias,
    h.horizon,
    COUNT(*)                                                            AS n,
    ROUND(AVG((ret >  0.01)::int)::numeric, 4)                          AS frac_up_gt_1pct,
    ROUND(AVG((ret < -0.01)::int)::numeric, 4)                          AS frac_down_gt_1pct,
    ROUND(AVG((ABS(ret) <= 0.01)::int)::numeric, 4)                     AS frac_dead_zone,
    ROUND(AVG((SIGN(ret) = CASE bias WHEN 'bullish' THEN 1 ELSE -1 END)::int)::numeric, 4)
                                                                        AS direction_only_accuracy
FROM signal_predictions,
LATERAL (VALUES (5, return_5d), (10, return_10d), (20, return_20d)) AS h(horizon, ret)
WHERE resolved_at IS NOT NULL AND ret IS NOT NULL
GROUP BY bias, h.horizon
ORDER BY bias, h.horizon;
```

Interpretation guide: if `direction_only_accuracy` is at/above ~50% while threshold accuracy is ~42%, the threshold design is the dominant problem. If direction-only is also well below 50%, the composite genuinely points the wrong way and inversion/channel-split becomes the priority.

### 0.2 Unconditional base rates (the "dumb baseline")

```sql
-- What would "always bullish on everything" score? Uses ALL resolved predictions
-- as a proxy population for tickers that fired any signal.
SELECT
    h.horizon,
    COUNT(*) AS n,
    ROUND(AVG((ret >  0.01)::int)::numeric, 4) AS base_up_gt_1pct,
    ROUND(AVG((ret < -0.01)::int)::numeric, 4) AS base_down_gt_1pct,
    ROUND(AVG((ret > 0)::int)::numeric, 4)     AS base_up_any,
    ROUND(AVG(ret)::numeric, 4)                AS avg_return
FROM signal_predictions,
LATERAL (VALUES (5, return_5d), (10, return_10d), (20, return_20d)) AS h(horizon, ret)
WHERE resolved_at IS NOT NULL AND ret IS NOT NULL
GROUP BY h.horizon ORDER BY h.horizon;
```

Every signal's edge is measured *relative to this*, not relative to 50%.

### 0.3 Inversion check

```sql
-- If we flipped every bias, what would accuracy be? (threshold metric)
SELECT
    bias AS original_bias,
    h.horizon,
    ROUND(AVG((CASE bias WHEN 'bullish' THEN ret < -0.01 ELSE ret > 0.01 END)::int)::numeric, 4)
        AS inverted_accuracy
FROM signal_predictions,
LATERAL (VALUES (5, return_5d), (10, return_10d), (20, return_20d)) AS h(horizon, ret)
WHERE resolved_at IS NOT NULL AND ret IS NOT NULL
GROUP BY bias, h.horizon ORDER BY bias, h.horizon;
```

### 0.4 Per-indicator standalone hit rates

Joins predictions back to `stock_signals` to score each indicator condition alone:

```sql
WITH joined AS (
    SELECT sp.bias, sp.return_10d AS ret,
           ss.rsi_14, ss.macd, ss.macd_signal,
           rp.close, ss.sma_50, ss.sma_200, ss.bb_upper, ss.bb_lower
    FROM signal_predictions sp
    JOIN stock_signals ss ON ss.ticker = sp.ticker AND ss.date = sp.signal_date
    JOIN raw_prices   rp ON rp.ticker = sp.ticker AND rp.date = sp.signal_date
    WHERE sp.resolved_at IS NOT NULL AND sp.return_10d IS NOT NULL
)
SELECT cond.name,
       COUNT(*) AS n,
       ROUND(AVG((ret > 0)::int)::numeric, 4)  AS frac_up_10d,
       ROUND(AVG(ret)::numeric, 4)             AS avg_ret_10d
FROM joined,
LATERAL (VALUES
    ('rsi_oversold(<30)',        rsi_14 < 30),
    ('rsi_overbought(>70)',      rsi_14 > 70),
    ('below_bb_lower',           close < bb_lower),
    ('above_bb_upper',           close > bb_upper),
    ('above_sma200',             close > sma_200),
    ('below_sma200',             close < sma_200),
    ('macd_bullish_cross_state', macd > macd_signal),
    ('deep_below_sma200(>15%)',  close < sma_200 * 0.85),
    ('far_above_sma200(>15%)',   close > sma_200 * 1.15)
) AS cond(name, hit)
WHERE cond.hit
GROUP BY cond.name ORDER BY avg_ret_10d DESC;
```

This is the feature-selection evidence for Phase 2: which conditions actually predict forward returns, and in which direction.

### 0.5 Confidence calibration curve (finer buckets)

```sql
SELECT bias,
       WIDTH_BUCKET(confidence, 0, 1, 10) AS conf_decile,
       COUNT(*) AS n,
       ROUND(AVG((ret > 0)::int)::numeric, 4) AS frac_up_10d,
       ROUND(AVG(ret)::numeric, 4)            AS avg_ret_10d
FROM signal_predictions, LATERAL (VALUES (return_10d)) v(ret)
WHERE resolved_at IS NOT NULL AND ret IS NOT NULL
GROUP BY bias, conf_decile ORDER BY bias, conf_decile;
```

Already known to be ~flat from the coarse table; this gives the full curve for the findings doc and a baseline to beat in Phase 5.

### 0.6 Episode inflation estimate

```sql
-- How many independent "episodes" vs raw rows? New episode = bias change or
-- gap > 5 trading days between consecutive same-bias signals on a ticker.
WITH ordered AS (
    SELECT ticker, signal_date, bias,
           LAG(signal_date) OVER (PARTITION BY ticker, bias ORDER BY signal_date) AS prev_date
    FROM signal_predictions WHERE resolved_at IS NOT NULL
)
SELECT bias,
       COUNT(*) AS raw_rows,
       SUM(CASE WHEN prev_date IS NULL OR signal_date - prev_date > 7 THEN 1 ELSE 0 END) AS episodes
FROM ordered GROUP BY bias;
```

(7 calendar days ≈ 5 trading days; good enough for an estimate.)

**Acceptance:** findings doc written; a go/no-go interpretation paragraph for each of: threshold redesign, inversion/channel-split, indicator keep/drop list.

---

## Phase 1 — Measurement rework (the new scoreboard)

**Goal:** replace the fixed ±1% raw-return metric with volatility-scaled, benchmark-adjusted, multi-horizon outcomes in a long-format table. Everything downstream (training, calibration, rollups) reads from this.

### 1.0 Data hygiene (prerequisite — run before any v2 computation)

Phase 0 found three data defects that corrupt return calculations. These must be remediated before backfill_outcomes_v2.py is run.

**Step 1 — Universe scrub (B + partial C):**
- Flag known Nasdaq test symbols (`ZWZZT`, `ZVZZT`, `ZXZZT` and family) `has_data=False` in `config/ticker_universe.json`.
- Flag bare-`W`-suffix warrants (e.g. `WLDSW`) `has_data=False` in `ticker_universe.json`. Heuristic: a ticker ending in `W` whose root (strip trailing `W`) also exists in the universe is a warrant.
- `DELETE FROM raw_prices WHERE ticker IN (<test_symbols>, <warrants>)` after flagging.

**Step 2 — One-time full EODHD re-backfill (A):** re-runs `backfill_eodhd.py` for all active tickers, resetting every row to a single adjustment epoch and eliminating cross-seam return errors. Then re-run `backfill_indicators.py`.

**Step 3 — yfinance purge (C):** `DELETE FROM raw_prices WHERE source = 'yfinance'`. Anything EODHD cannot serve is not usable.

**Step 4 — Re-run v1 prediction backfill:** `backfill_predictions.py` — all v1 return/correct fields computed against the now-clean prices.

**Permanent guard (implemented in `resolve_outcomes_v2`):** quarantine (do not resolve) any prediction row whose forward price window contains a 1-day close ratio > `V2_MAX_DAILY_RATIO`. These are pending corporate-action re-fetches, not genuine moves.

### 1.1 New config (`config/config.py`, `# --- Signal v2 ---` section)

```python
V2_PREDICTION_HORIZONS = [5, 10, 20, 21, 42, 63]   # trading days; 5/10/20 kept for v1 comparability
V2_PRIMARY_HORIZON = 42                             # primary calibration/optimization target
V2_VOL_LOOKBACK_DAYS = 20                           # trailing window for daily vol estimate
V2_THRESHOLD_VOL_MULT = 0.5                         # move must exceed k * sigma_daily * sqrt(h)
V2_BENCHMARK_TICKER = "SPY"
V2_BETA_WINDOW = 90                                 # which sector_beta window to use
V2_EPISODE_GAP_DAYS = 5                             # trading-day gap that starts a new episode
V2_MAX_DAILY_RATIO = 3.0                            # quarantine guard: 1-day close ratio above this flags a seam
V2_MIN_PRICE = 1.00                                 # USD/CAD nominal; exclude sub-$1 tickers from v2 eligibility
SIGNAL_VERSION_V2 = "v2.0"
```

### 1.2 Migration `005_v2_prediction_outcomes.sql`

```sql
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    ticker            VARCHAR(16) NOT NULL,
    signal_date       DATE        NOT NULL,
    horizon_days      INTEGER     NOT NULL,

    price_at_signal   NUMERIC     NOT NULL,
    future_price      NUMERIC,            -- close at trading day h (row-offset counting)
    raw_return        NUMERIC,
    benchmark_return  NUMERIC,            -- SPY return over the same trading-day window
    beta_used         NUMERIC,            -- from sector_beta (SPY proxy) or 1.0 fallback
    excess_return     NUMERIC,            -- raw_return - beta_used * benchmark_return
    vol_at_signal     NUMERIC,            -- trailing daily sigma at signal date
    threshold_used    NUMERIC,            -- k * vol_at_signal * sqrt(h)

    direction_correct BOOLEAN,            -- sign(excess_return) matches bias
    threshold_correct BOOLEAN,            -- excess_return clears +/- threshold_used per bias

    episode_id        VARCHAR(64),        -- '<ticker>:<first_signal_date_of_episode>'
    is_episode_head   BOOLEAN NOT NULL DEFAULT FALSE,

    resolved_at       TIMESTAMPTZ,
    PRIMARY KEY (ticker, signal_date, horizon_days)
);
CREATE INDEX IF NOT EXISTS idx_pred_outcomes_unresolved
    ON prediction_outcomes (signal_date) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pred_outcomes_horizon ON prediction_outcomes (horizon_days);
CREATE INDEX IF NOT EXISTS idx_pred_outcomes_episode ON prediction_outcomes (episode_id);
```

`signal_predictions` is untouched and continues to serve v1. `prediction_outcomes` rows are seeded from `signal_predictions` (it remains the registry of what was predicted; outcomes move to long format).

### 1.3 Pure functions — `dag_components/outcome_tracker/calculations_v2.py`

All no-Airflow, fully unit-tested:

- `trailing_daily_vol(closes: list[float], lookback: int) -> float | None` — stdev of daily log or pct returns over the lookback (pick pct, document choice).
- `scaled_threshold(vol_daily: float, horizon: int, k: float) -> float` — `k * vol_daily * sqrt(horizon)`.
- `benchmark_window_return(bench_closes_after: list[dict], h: int, bench_close_at_signal: float) -> float | None` — same row-offset counting as ticker prices (`nth_trading_day_price` reused).
- `excess_return(raw: float, beta: float, bench: float) -> float`.
- `outcome_flags(bias: str, excess: float | None, threshold: float | None) -> tuple[bool | None, bool | None]` — returns `(direction_correct, threshold_correct)`; neutral bias → `(None, None)`.
- `assign_episodes(rows: list[dict], gap_days: int) -> list[dict]` — rows sorted by ticker/date with bias; stamps `episode_id` and `is_episode_head`. Trading-day gaps measured by row offset within the ticker's signal sequence, consistent with the no-calendar-arithmetic rule.

**Edge cases to test:** zero/None vol, short history (< lookback), beta missing → 1.0 fallback, benchmark data missing → `excess_return` NULL but `raw_return` still recorded, bias flips mid-streak starting a new episode.

### 1.4 Task + backfill changes

- New task `resolve_outcomes_v2` in `dag_components/outcome_tracker/tasks.py`, appended to `dag_outcome_tracker` *after* the existing v1 tasks (v1 chain unchanged). It: seeds missing `prediction_outcomes` rows from `signal_predictions`, resolves any row where the ticker has ≥ h future bars, computes all columns above. Horizons resolve independently — a 5d row can resolve while the 63d row waits (this is why long format exists).
- Beta lookup: `sector_beta WHERE etf_proxy = 'SPY' AND window_days = V2_BETA_WINDOW`; fallback `beta_used = 1.0`.
- New script `scripts/backfill_outcomes_v2.py` — same conventions as `backfill_predictions.py` (per-ticker commits, kill-safe, `--start` flag). Loads each ticker's full price series once, plus SPY series once globally; resolves all horizons for all historical predictions; runs `assign_episodes` per ticker.
- Accuracy rollup v2: extend `signal_accuracy` usage via a v2 path that groups by `(signal_version, vix_regime, vol_environment, bias, horizon_days)` but computes from `prediction_outcomes` joined to `signal_predictions`, with **episode-head-only** and **all-rows** variants both reported. If column needs differ, add migration columns rather than overloading semantics.

**Acceptance:** backfill completes over full history; spot-check 10 random rows by hand-computing excess returns; episode counts roughly match Phase 0.6 estimate; pytest green; re-run of backfill is a no-op (idempotent).

---

## Phase 2 — Continuous feature layer

**Goal:** replace binary sub-signals with continuous, per-ticker-normalized features. Pure pandas. Stored per (ticker, date) so training and runtime read identical values.

### 2.1 Feature set (initial — prune using Phase 0.4 evidence)

| Feature | Definition | Captures |
|---|---|---|
| `dist_sma50` | (close − SMA50) / SMA50 | short-term displacement |
| `dist_sma200` | (close − SMA200) / SMA200 | long-term displacement |
| `dist_sma200_z` | z-score of `dist_sma200` vs its own trailing 252d distribution | stretch vs ticker's own norm |
| `rsi_centered` | (RSI − 50) / 50 → [−1, 1] | momentum exhaustion |
| `bb_pctb` | (close − bb_lower) / (bb_upper − bb_lower) | band position |
| `macd_hist_norm` | macd_hist / close | momentum slope, price-scale-free |
| `dist_52w_high` | close / 252d max(high) − 1 | distance from high (≤ 0) |
| `dist_52w_low` | close / 252d min(low) − 1 | distance from low (≥ 0) |
| `vol_ratio` | volume / SMA20(volume) | participation/confirmation |
| `ret_21d` | 21d trailing return | medium momentum |
| `ret_63d` | 63d trailing return | longer momentum |

All windows in config (`V2_FEATURE_*` constants). Z-scores: trailing window, never centered on future data (no lookahead — the window ends at the current row).

### 2.2 Implementation

- Migration `006_signal_features.sql`: table `signal_features (ticker, date, <one NUMERIC per feature>) PRIMARY KEY (ticker, date)` + date index. Keep `stock_signals` untouched.
- `dag_components/features/calculations.py` — one function `compute_feature_frame(df: pd.DataFrame) -> pd.DataFrame` taking a ticker's OHLCV history, returning the feature columns aligned to dates. Vectorized; no per-row loops.
- `dag_components/features/tasks.py` — `compute_features` task inserted into `dag_stock_indicators` flow after `upsert_stock_signals` (it reads `raw_prices` + `stock_signals`, writes `signal_features`). Idempotent upsert.
- `scripts/backfill_features.py` — full-history backfill, same conventions as `backfill_indicators.py`.
- `tests/test_features.py` — synthetic series with known answers (e.g., constant price → all distances 0, %B = 0.5 degenerate handling, NaN warmups).

**Edge cases:** division-by-zero when bb_upper == bb_lower or SMA == 0; <252 bars (52w features NULL, z-score NULL until window fills); volume = 0 days.

**Acceptance:** backfill done; feature NULL rates reported per feature (warmup-only NULLs expected); pytest green.

---

## Phase 3 — Regime classification

**Goal:** classify each ticker-date as `trending` / `ranging` so Phase 4 can apply the right logic, resolving the trend-vs-reversion contradiction instead of averaging over it. Market-level VIX regime becomes a conditioning variable (kept from v1), never a score multiplier.

### 3.1 Definition

- **ADX(14)** implemented in pure pandas (Wilder smoothing, consistent with the existing RSI implementation style) in `dag_components/features/calculations.py`. Config: `V2_ADX_WINDOW = 14`, `V2_ADX_TREND_MIN = 25`.
- **SMA200 slope:** 63d pct change of the SMA200 series. Config: `V2_SLOPE_WINDOW = 63`, `V2_SLOPE_TREND_MIN = 0.02` (|slope| ≥ 2% over the window).
- `ticker_regime = 'trending'` if `ADX ≥ 25 OR |sma200_slope| ≥ threshold`, else `'ranging'`. Plus `trend_direction ∈ {'up','down', NULL}` from slope sign when trending.

Add columns `adx_14`, `sma200_slope`, `ticker_regime`, `trend_direction` to `signal_features` (fold into migration 006 if Phase 2 and 3 land together — preferred).

### 3.2 Validation

One-off analysis (notebook-style script `scripts/analyze_regimes.py`, prints only): regime distribution over time, average regime persistence in days, and forward-return behavior of stretched names *split by regime* — the hypothesis to confirm is that deep-below-SMA200 names mean-revert in `ranging` and keep falling in `trending-down`. This is the empirical go/no-go for the two-channel design.

**Acceptance:** regimes populated full-history; persistence is sane (regimes last weeks, not days — if flipping daily, raise thresholds); hypothesis check documented in the findings doc.

**Status:** ✅ Complete — folded into Phase 2 (`migration 007`, `backfill_features.py`). `scripts/analyze_regimes.py` validation script is pending; run before Phase 4 to confirm the falling-knife hypothesis and set channel thresholds.

---

## Phase 4 — Cross-sectional rank model (AMENDED 2026-06-11)

> **Changelog:** The two-channel (ranging/trending) architecture from the original spec is retired.
> Phase 3 disproved mean-reversion in ranging regimes. The event study (2026-06-11) showed all
> TA event edges dissolve when compared against slope-conditioned base rates — the edge lives in
> continuous states, not first-cross conditions. Architecture replaced with a single cross-sectional
> rank model on state features from `signal_features`. No event features.

**Goal:** a daily cross-sectional rank over the eligible universe (price ≥ `V2_MIN_PRICE`,
non-quarantined) from a single logistic model per horizon. Runtime stays pure numpy.

### 4.1 Architecture

Single `LogisticRegression` per horizon (primary: 42d), target = `sign(excess_return_42d) > 0`.
Training frame: all `signal_features` rows × resolved excess returns — never v1-selected rows
(would inherit v1's selection bias). sklearn quarantined to `scripts/`; runtime scoring is a
pure numpy dot product + sigmoid.

**Feature set** (all from `signal_features`):

| Feature | Note |
|---|---|
| `dist_52w_high` | Lead feature; event study shows continuous proximity beats the binary crossing |
| `dist_52w_low` | |
| `dist_sma50` | |
| `dist_sma200` | |
| `dist_sma200_z` | z-score of displacement vs ticker's own trailing distribution |
| `sma200_slope` | Continuous; slope>0 vs <=0 is the dominant conditioning variable (2.33pp spread) |
| `above_sma200` | Binary indicator derived from dist_sma200 sign |
| `adx_14` | Trend strength; regime conditioning |
| `trend_direction` | One-hot: up / down / NULL (ranging) |
| `rsi_centered` | |
| `bb_pctb` | |
| `macd_hist_norm` | |
| `mom_12_1` | 12m return minus 1m return (medium-term momentum minus short-term reversal); computed in train script from raw_prices |
| `vol_63d` | 63d realised volatility; computed in train script |
| `vol_ratio` | Expected negative coefficient — verify sign in fit report |
| `ret_21d`, `ret_63d` | |
| `vix_regime` | One-hot |
| `vol_environment` | One-hot |
| `dist_sma200_z × trend_direction` | Interaction: how stretched is a trending-down name vs its own history |

No event features (E1–E11 retired).

### 4.2 Product legs (long-biased, no bearish predictions)

- **(a) Negative screen:** deep_below_sma200 AND trend_direction='down' excluded from buy candidates (config flag `V2_FALLING_KNIFE_SCREEN`). Empirical basis: Phase 3 confirmed this is the worst subgroup (−2.79% median excess @42d).
- **(b) Core:** top-N names by daily rank score, filtered by negative screen.
- **(c) Extended flag:** bottom-decile names from held positions only — "reduce / avoid" flag, not a short signal.

### 4.3 Training — `scripts/train_signal_v2.py` (offline, manual)

- **Target:** `sign(excess_return_42d) > 0` for every (ticker, date) with non-null features and resolved 42d excess return. Computable directly from `signal_features` × `raw_prices` × SPY × `sector_beta` (reuse `calculations_v2` functions).
- **Model:** `LogisticRegression`, L2, class-balanced. One model per horizon. sklearn only in this script.
- **Walk-forward (non-negotiable):** expanding window ending at year Y, evaluate on year Y+1; roll 2021→2025. Report per-fold: top-decile minus bottom-decile spread of winsorized excess @42d, and top-decile avg excess. Embargo: exclude 63 trading days before each test window from training (overlapping-label leakage from long return horizons).
- **Sample weighting:** episode heads weight 1.0, continuation rows 0.25 — config constant `V2_EPISODE_HEAD_WEIGHT`.
- **Export:** `config/model_coefficients_v2.json` — `{horizon: {features, coef, intercept, fitted_at, train_window, oos_metrics: {fold: {top_decile_excess, spread, n}}}}`. Human reviews and commits.

### 4.4 Runtime scoring

- `dag_components/scoring/calculations_v2.py`: `score_v2(features_row, coefficients) -> float` — pure numpy dot + sigmoid. Loaded coefficients cached at module import.
- New task in `dag_llm_analysis` writes v2 predictions to `signal_predictions` with `signal_version = 'v2.0'`. PK widening to `(ticker, signal_date, signal_version)` was already applied in migration 004b.

**Acceptance:** top-decile minus bottom-decile spread > 0 across all walk-forward folds; top-decile avg winsorized excess > 0 at 42d OOS; sign of `vol_ratio` coefficient is negative (verify); pytest green; ruff clean.

---

## Phase 5 — Calibrated confidence (AMENDED 2026-06-11)

> **Changelog:** Calibration buckets updated to match the Phase 4 rank-model architecture.
> Channel dimension replaced by trend_direction (the dominant conditioning variable from
> the event study and Phase 3).

**Goal:** confidence becomes an empirical probability. "0.62" must mean "setups in this bucket resolved correctly 62% of the time out-of-sample."

- **Buckets:** `(rank_decile, trend_direction, vix_regime)` — start coarse, refine only where n (episode-heads) ≥ `ACCURACY_MIN_SAMPLE_SIZE`.
- `scripts/calibrate_confidence_v2.py` computes bucket hit rates from walk-forward OOS predictions only (never training folds), exports `config/confidence_calibration_v2.json`.
- Runtime: `confidence_v2(rank_decile, trend_direction, vix_regime, calibration) -> float` — bucket lookup with monotonic smoothing (isotonic-style: enforce non-decreasing across rank deciles per trend_direction × vix_regime cell; implement in pandas, sklearn out of runtime).
- **Acceptance criterion:** buckets must be **monotonic** — accuracy increases with rank decile OOS, within each (trend_direction, vix_regime) cell. That monotonicity is the success criterion of the entire rebuild.

---

## Phase 6 — Parallel run & cutover

**Goal:** v1 and v2 run side by side on live nightly data; cut over on evidence.

- Both versions populate `signal_predictions` nightly (distinguished by `signal_version`); `prediction_outcomes` resolves both.
- New comparison view `v_signal_version_compare` (migration 008): per version × horizon × bias — episode-head direction accuracy, threshold accuracy, avg excess return, n.
- `dag_parameter_review`'s Sonnet prompt gains a v1-vs-v2 section (extend `_build_review_prompt`).
- **Cutover criteria (minimum 60 trading days of parallel live data):** v2 episode-head direction accuracy ≥ v1 + 3pts at 42d, AND v2 calibration monotonicity holds on live data, AND no operational regressions (runtime, failures).
- On cutover: daily brief and Jarvis stub consume v2; v1 scoring tasks retired from the DAG (code retained, reference-only, like `polygon_client.py`); `CLAUDE.md` updated.

---

## Build order & dependency summary

```
Phase 0 (SQL, findings)            ← no dependencies; run today
Phase 1 (scoreboard)               ← needs Phase 0 only to confirm threshold params
Phase 2 (features)                 ← independent of Phase 1; parallelizable
Phase 3 (regimes)                  ← extends Phase 2's module/migration
Phase 4 (model)                    ← needs 1 + 2 + 3
Phase 5 (calibration)              ← needs 4's walk-forward outputs
Phase 6 (parallel run, cutover)    ← needs 4 + 5
```

## Phase status

| Phase | Status | Deliverable |
|---|---|---|
| Phase 0 — Diagnostics | ✅ Complete | `docs/signal_v2_phase0_findings.md` |
| Phase 1 — Measurement rework | ✅ Complete | `prediction_outcomes` populated full-history (5.3M rows, 6 horizons), v2 resolver live |
| Phase 2 — Feature layer | ✅ Complete | `signal_features` populated full-history (451k rows, 6,932 tickers) |
| Phase 3 — Regime classification | ✅ Complete | regimes populated (folded into Phase 2 migration + backfill) |
| Phase 4 — Composite v2 | 🔲 Not started | `model_coefficients_v2.json`, walk-forward report, runtime scoring live |
| Phase 5 — Calibrated confidence | 🔲 Not started | `confidence_calibration_v2.json`, monotonic OOS calibration table |
| Phase 6 — Parallel run & cutover | 🔲 Not started | comparison view, cutover decision |
