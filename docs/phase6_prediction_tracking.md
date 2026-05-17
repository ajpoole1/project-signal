# Project Signal — Phase 6 Roadmap
## Self-Improving Signal Layer: Prediction Tracking, Outcome Evaluation & Parameter Optimization
**Standalone spec for Claude Code — read alongside CLAUDE.md**

---

## Overview

Phase 6 adds a feedback loop to the signal pipeline. Every signal the system generates is recorded as a prediction, outcomes are evaluated against actual price movement, accuracy is aggregated by regime, and a parameter optimization layer proposes config changes based on empirical performance.

This phase is purely additive — no changes to existing DAGs or tables. All new components are independent.

### Design Principles (Phase 6 specific)

- **Never auto-apply parameter changes.** All proposals go to `parameter_proposals` table. Human approval required before any config changes.
- **Signal version tracking on every prediction.** When parameters change, historical accuracy data must remain attributable to the config that generated it.
- **Backfill before real-time.** 5 years of `raw_prices` + `stock_signals` means ~750K historical signal records are computable before a single live prediction matures. Optimize against history first.
- **Interpretable optimization only.** Grid search over named parameters, not black-box ML. Every proposal must have a human-readable rationale.
- **Separation from live pipeline.** `dag_outcome_tracker` and `dag_parameter_review` are independent DAGs — failure in either does not affect ingest, indicators, relatedness, or LLM analysis.

---

## New Tables

### `signal_predictions`

Records every signal at generation time with forward-looking outcome fields populated later by `dag_outcome_tracker`.

```sql
CREATE TABLE IF NOT EXISTS signal_predictions (
    ticker              VARCHAR(16)     NOT NULL,
    signal_date         DATE            NOT NULL,

    -- Signal state snapshot at prediction time
    bias                VARCHAR(16),
    composite_vix_adj   NUMERIC,
    confidence          NUMERIC,
    vix_regime          VARCHAR(16),
    vix_trend           VARCHAR(16),
    vol_environment     VARCHAR(24),
    volume_ratio        NUMERIC,
    earnings_flag       VARCHAR(16),
    signal_trend        VARCHAR(16),

    -- Price at signal time
    price_at_signal     NUMERIC         NOT NULL,

    -- Config version that generated this signal
    signal_version      VARCHAR(32)     NOT NULL,

    -- Outcome fields — NULL until resolved by dag_outcome_tracker
    actual_price_5d     NUMERIC,
    actual_price_10d    NUMERIC,
    actual_price_20d    NUMERIC,
    return_5d           NUMERIC,
    return_10d          NUMERIC,
    return_20d          NUMERIC,
    correct_5d          BOOLEAN,
    correct_10d         BOOLEAN,
    correct_20d         BOOLEAN,

    -- Lifecycle
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,

    PRIMARY KEY (ticker, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_signal_predictions_date
    ON signal_predictions (signal_date);
CREATE INDEX IF NOT EXISTS idx_signal_predictions_unresolved
    ON signal_predictions (signal_date)
    WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_signal_predictions_version
    ON signal_predictions (signal_version);
```

### `signal_accuracy`

Weekly accuracy rollup. Queryable by regime, environment, bias, horizon, and config version.

```sql
CREATE TABLE IF NOT EXISTS signal_accuracy (
    signal_version      VARCHAR(32)     NOT NULL,
    vix_regime          VARCHAR(16)     NOT NULL,
    vol_environment     VARCHAR(24)     NOT NULL,
    bias                VARCHAR(16)     NOT NULL,
    horizon_days        INTEGER         NOT NULL,

    total_signals       INTEGER         NOT NULL,
    correct_signals     INTEGER         NOT NULL,
    accuracy_rate       NUMERIC         NOT NULL,
    avg_return          NUMERIC,
    avg_return_correct  NUMERIC,
    avg_return_wrong    NUMERIC,

    computed_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    PRIMARY KEY (signal_version, vix_regime, vol_environment, bias, horizon_days)
);
```

### `parameter_proposals`

LLM-generated parameter change proposals. Status workflow: `pending` → `approved` or `rejected`.

```sql
CREATE TABLE IF NOT EXISTS parameter_proposals (
    id                  SERIAL          PRIMARY KEY,
    proposed_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    param_name          VARCHAR(64)     NOT NULL,
    current_value       NUMERIC         NOT NULL,
    proposed_value      NUMERIC         NOT NULL,
    rationale           TEXT            NOT NULL,
    accuracy_delta      NUMERIC,
    sample_size         INTEGER,
    vix_regime_scope    VARCHAR(16),    -- NULL = all regimes
    vol_env_scope       VARCHAR(24),    -- NULL = all environments
    status              VARCHAR(16)     NOT NULL DEFAULT 'pending',
    reviewed_at         TIMESTAMPTZ,
    review_note         TEXT
);
```

---

## New Config Values

Add to `config/config.py`:

```python
# --- Signal version ---
# Increment this string whenever signal weights or VIX/VVIX thresholds change.
# Format: "vMAJOR.MINOR" — major for weight changes, minor for threshold changes.
# Every signal_predictions row is tagged with the version active at generation time.
SIGNAL_VERSION = "v1.0"

# --- Outcome evaluation ---
PREDICTION_HORIZONS = [5, 10, 20]          # trading days
CORRECT_SIGNAL_THRESHOLD = 0.01            # min return magnitude to count as confirmed
                                           # e.g. 0.01 = 1% move required

# --- Accuracy aggregation ---
ACCURACY_MIN_SAMPLE_SIZE = 30              # min signals required to report accuracy

# --- Optimization ---
OPTIMIZATION_TARGET_HORIZON = 10          # primary horizon for parameter scoring
OPTIMIZATION_TARGET_BIAS = "bullish"      # primary bias to optimize for
OPTIMIZATION_MIN_ACCURACY_DELTA = 0.03    # min projected improvement to generate proposal
```

---

## New File: `config/parameter_overrides.json`

Config values in this file take precedence over `config.py` defaults. Populated only via approved proposals — never edited manually. Committed to git so every approval is in the change history.

```json
{}
```

`config.py` reads this at import time:

```python
import json
from pathlib import Path

_OVERRIDES_PATH = Path(__file__).parent / "parameter_overrides.json"
_OVERRIDES: dict = {}
if _OVERRIDES_PATH.exists():
    with open(_OVERRIDES_PATH) as _f:
        _OVERRIDES = json.load(_f)

def _get(key: str, default):
    return _OVERRIDES.get(key, default)

# Example usage in config.py — replace hardcoded defaults:
SIGNAL_WEIGHTS = {
    "sma_200": _get("sma_200_weight", 0.30),
    "sma_50":  _get("sma_50_weight",  0.25),
    "macd":    _get("macd_weight",    0.25),
    "rsi":     _get("rsi_weight",     0.20),
}
```

---

## New DAG: `dag_outcome_tracker`

**Schedule:** `0 6 * * 1-5` (6 AM UTC, weekdays — runs after ingest at 5 AM UTC)
**Catchup:** False
**Retries:** 2

### Task flow

```
populate_predictions → resolve_matured_predictions → compute_accuracy_rollup
```

### Task: `populate_predictions`

Reads today's `llm_analysis` + `stock_signals` rows and inserts into `signal_predictions`. Uses `ON CONFLICT DO NOTHING` — safe to re-run, never overwrites existing predictions.

Only inserts for `bias != 'neutral'` — neutral signals are not evaluated.

### Task: `resolve_matured_predictions`

For each unresolved prediction old enough that all horizons have passed (signal_date <= today - 20 trading days), look up actual prices and compute returns and correctness flags.

**Correctness logic:**

```python
def is_correct(bias: str, return_n: float | None, threshold: float) -> bool | None:
    if return_n is None:
        return None
    if bias == "bullish":
        return return_n > threshold
    if bias == "bearish":
        return return_n < -threshold
    return None
```

**Critical:** use trading-day counts from `raw_prices` (nth row after signal date), not calendar day offsets. This correctly handles weekends, holidays, and market closures.

### Task: `compute_accuracy_rollup`

Aggregates resolved predictions into `signal_accuracy`. Runs only on Sundays (guarded by day-of-week check inside the task, not a separate schedule — keeps the DAG unified).

Groups by `(signal_version, vix_regime, vol_environment, bias, horizon_days)`. Only includes groups with >= `ACCURACY_MIN_SAMPLE_SIZE` signals.

---

## New DAG: `dag_parameter_review`

**Schedule:** `0 11 * * 0` (11 AM UTC Sundays — after `dag_stock_relatedness` at 10 AM)
**Catchup:** False
**Retries:** 1

### Task flow

```
read_pending_proposals → generate_review_brief → push_review_to_jarvis
```

### Task: `generate_review_brief`

Reads pending proposals from `parameter_proposals` and resolved accuracy from `signal_accuracy`. Calls Sonnet to generate a weekly parameter health report (under 250 words): overall accuracy trend, strongest/weakest regime, approve/reject recommendation for each pending proposal.

### Task: `push_review_to_jarvis`

Writes to `daily_brief` with `brief_type = 'parameter_review'` — stubbed until Phase 7 Jarvis integration is live.

---

## New Script: `scripts/backfill_predictions.py`

Retroactively populates `signal_predictions` from historical `llm_analysis` + `stock_signals` + `raw_prices`. Uses `signal_version = "v1.0-backfill"` to distinguish from live predictions. Immediately resolves outcomes since all historical prices are available.

**This is the most important script in Phase 6.** It transforms 5 years of existing data into an accuracy baseline before any live predictions have matured.

```
Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_predictions.py
    docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_predictions.py --start 2021-01-01
```

Commits per-ticker for safety — safe to kill and re-run. Prints running accuracy stats as it goes.

---

## New Script: `scripts/optimize_parameters.py`

Grid search over parameter space using historical `signal_predictions` accuracy as the objective. Writes proposals to `parameter_proposals`. Never modifies any config files — human approval required.

### Parameter grid

```python
PARAM_GRID = {
    "sma_200_weight":     [0.20, 0.25, 0.30, 0.35, 0.40],
    "sma_50_weight":      [0.15, 0.20, 0.25, 0.30],
    "macd_weight":        [0.15, 0.20, 0.25, 0.30],
    "rsi_weight":         [0.10, 0.15, 0.20, 0.25],
    "vix_high_mult":      [1.10, 1.15, 1.20, 1.25, 1.30],
    "vix_elevated_mult":  [1.05, 1.08, 1.10, 1.12, 1.15],
    "vix_low_mult":       [0.75, 0.80, 0.85, 0.90],
    "signal_threshold":   [0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
}
# Constraint: weights must sum to 1.0 (enforced before evaluation)
# Objective: maximize accuracy_rate on (bullish, 10d, elevated+high VIX)
# Secondary objective: maximize avg_return_correct across all regimes
```

Calls Haiku to generate the rationale string for each proposal. Inserts into `parameter_proposals`, never touches config files.

---

## Build Order Within Phase 6

| Step | Deliverable | Notes |
|---|---|---|
| 1 | Migration 003 + `parameter_overrides.json` + `SIGNAL_VERSION` in config | No DAG changes — pure schema + config prep |
| 2 | `dag_components/outcome_tracker/calculations.py` + tests | Pure functions only — no Airflow imports. Test `is_correct()`, `nth_trading_day_price()`, accuracy rollup math |
| 3 | `dag_outcome_tracker` — `populate_predictions` task only | Run manually for one day, verify rows inserted correctly |
| 4 | `dag_outcome_tracker` — `resolve_matured_predictions` task | Requires step 3 data; verify trading-day counting logic |
| 5 | `dag_outcome_tracker` — `compute_accuracy_rollup` task | Requires resolved data from step 4 |
| 6 | `scripts/backfill_predictions.py` | Run against full history. This generates the accuracy baseline. |
| 7 | `scripts/optimize_parameters.py` | Run after backfill. Review proposals before touching any config. |
| 8 | `dag_parameter_review` | Wire up after step 7 produces real proposals |

**Exit criteria for Phase 6:**
- `signal_predictions` has backfilled rows for all historical `llm_analysis` dates
- `signal_accuracy` shows accuracy rates by VIX regime with sample sizes > 30
- At least one parameter proposal generated and visible in `parameter_proposals`
- No changes applied to `parameter_overrides.json` until proposals have been manually reviewed

---

## Key Implementation Notes

- **Trading day counting:** always use row count from `raw_prices` ordered by date, not calendar day arithmetic. `SELECT close FROM raw_prices WHERE ticker = %s AND date > %s ORDER BY date LIMIT 20` — the 5th, 10th, and 20th rows are your horizons.
- **`ON CONFLICT DO NOTHING` on `populate_predictions`** — this task must be re-runnable without corrupting outcome data that has already been resolved.
- **`ON CONFLICT DO UPDATE` on `resolve_matured_predictions`** — safe to re-resolve; outcome data is deterministic.
- **`signal_version` in `signal_predictions`** reads from `config.SIGNAL_VERSION` at task execution time, not at DAG parse time. Use `from config import config` inside the task function.
- **`parameter_overrides.json` is committed to git.** Every approval is a git commit. The file starts empty (`{}`). Never pre-populate it.
- **`backfill_predictions.py` uses `signal_version = "v1.0-backfill"`** — distinct from live predictions so accuracy stats can be filtered by source if needed.
- **`optimize_parameters.py` is a script, not a DAG task.** Run manually, produces proposals, exits. Never modifies config files under any circumstances.
- **Tests for `outcome_tracker/calculations.py` must cover:** correct signal with small move below threshold (should return False not True), neutral bias (should return None), missing price at horizon (should return None gracefully), trading day counting across a weekend boundary.
