# Design Patterns — Project Signal

All code in this repo follows these patterns. Read this before writing or modifying any code.

---

## 1. Calc / Tasks split

Pure math functions live in `dag_components/<area>/calculations*.py`. Task wrappers live in `tasks.py`. The two kinds of files have non-overlapping imports: calculations files import nothing from Airflow, no DB hooks, no network clients; tasks files import the calculations module and Airflow decorators.

**Why:** Calculations are testable without Docker or a live DB. A test can import `calculations.py` directly, pass a synthetic DataFrame, and assert the output — no mocking required.

**Example from this repo (`dag_components/indicators/calculations.py:18`):**

```python
def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()
```

No Airflow import anywhere in that file. The corresponding task (`dag_components/indicators/tasks.py:75`) imports `calculations as calc` and calls `calc.sma(...)` inside a `@task()` function that holds the DB hook.

**Rule:** If a function needs an Airflow import, a `PostgresHook`, or `requests`, it belongs in `tasks.py`. If it is pure math on plain Python types or pandas, it belongs in `calculations.py`.

**Test requirement:** Every calculations module has a test file (`tests/test_indicators.py`, `tests/test_relatedness.py`, `tests/test_features.py`, `tests/test_calculations_v2.py`). Tests use synthetic fixtures — no DB, no network, no `unittest.mock` of Airflow.

---

## 2. Config-driven tunables

All numeric constants, window sizes, thresholds, and LLM model names live in `config/config.py`. Nothing is hardcoded in DAG, task, plugin, or calculations files.

**Example (`config/config.py:130-138`):**

```python
SMA_SHORT_WINDOW = 50
SMA_LONG_WINDOW = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_WINDOW = 14
BB_WINDOW = 20
BB_STD = 2
VIX_SMA_WINDOW = 20
```

All calculations modules import `from config import config` and reference these constants directly. DAG files do not import config — only task files do.

### Parameter overrides loading

Approved parameter proposals are committed to `config/parameter_overrides.json` as a flat JSON dict (never edited manually). Config loads the file at import time and merges it with base values:

```python
_OVERRIDES_PATH = Path(__file__).parent / "parameter_overrides.json"
_OVERRIDES: dict = {}
if _OVERRIDES_PATH.exists():
    with open(_OVERRIDES_PATH) as _f:
        _OVERRIDES = json.load(_f)

def _get(key: str, default):
    return _OVERRIDES.get(key, default)
```

For regime-scoped signal weights, override keys use dotted notation (`"elevated.macd_weight": 0.30`). The lookup order (`config/config.py:43-57`):
1. Regime-scoped override: `"elevated.macd_weight"`
2. For `"default"` only: unprefixed legacy override `"macd_weight"`
3. Base default value

**Rule:** New tunables go in `config/config.py` under the appropriate section (`# --- Signal v2 ---` for v2 tunables). Never hardcode a number in a calculations or tasks file.

**After editing config:** restart the Airflow scheduler (`docker compose restart airflow-scheduler`) — WSL2 mounts have low-resolution mtimes, workers can run stale `.pyc`.

---

## 3. Idempotent insert pattern and the insert-mapping rule

All DAG-task database writes use `INSERT ... ON CONFLICT DO UPDATE` (or `DO NOTHING` where outcomes must never be overwritten, e.g. `signal_predictions`). Tasks never `DELETE` or `TRUNCATE` — destructive operations belong only in migration files.

**Example (`dag_components/indicators/tasks.py:220`):**

```python
execute_values(
    cur,
    """
    INSERT INTO stock_signals (
        ticker, date,
        sma_50, sma_200, macd, macd_signal, macd_hist, rsi_14,
        bb_upper, bb_lower,
        vix_close, vvix_close, vix_source,
        vix_regime, vix_trend, vol_environment,
        composite_score, composite_vix_adj
    ) VALUES %s
    ON CONFLICT (ticker, date) DO UPDATE SET
        sma_50 = EXCLUDED.sma_50,
        ...
    """,
    records,
)
```

### The insert-mapping rule

Every `execute_values` or `cur.execute` INSERT must have a test asserting that tuple field order matches the SQL column list. This is the **mapping test** — it is separate from the math test.

**Motivating incident — the sector_beta column-swap bug (`sql/migrations/008_fix_sector_beta_bug.sql`):**

`_INSERT_BETAS` in `dag_components/relatedness/tasks.py` had column order `(ticker, etf_proxy, beta, window_days)` but `beta_values()` returns tuples as `(ticker, etf_proxy, window_days, beta)` — the last two were swapped. Beta floats were truncated into the `INTEGER window_days` column; integer window values were stored in the `NUMERIC beta` column. `ON CONFLICT` collapsed the table to 2 rows. All `prediction_outcomes` computed from that point used beta=1.0 fallback. The bug was silent until a Phase 3 data audit. Migration 008 truncated `sector_beta`; the DAG was re-triggered to repopulate; `backfill_outcomes_v2.py` was re-run.

**Rule:** Write a test that constructs the tuple in the same expression used by the insert and asserts field-by-field against the SQL column list. If you can read the tuple order off the SQL column list without checking the construction code, the test does not exist yet.

---

## 4. Migration workflow

Schema changes follow a strict workflow:

1. Create a numbered migration file: `sql/migrations/NNN_description.sql`
2. Apply it to the live database
3. Sync the same change to `sql/schema.sql` (used for fresh installs)

Migration files are the only place that runs `ALTER TABLE`, `DROP TABLE`, `TRUNCATE`, or `DELETE` on production data. Python code never does these.

**Naming:** sequential three-digit prefix (`001_`, `002_`, …). If two migrations land in the same deployment, pick the next available number and document the dependency in a comment.

**Destructive operations in migrations:** always include a comment explaining why the operation is safe — what invariant holds that makes truncation or deletion correct here. See `sql/migrations/008_fix_sector_beta_bug.sql` for the canonical form: state the bug, state why truncation is the correct fix, note what must be re-run afterward.

**After a migration:** update `sql/schema.sql` to reflect the final table state. Both files must stay in sync so a fresh install via `schema.sql` produces the same schema as an incremental install running all migrations.

---

## 5. Backfill script template

All `scripts/backfill_*.py` files follow this template:

- **Env loading:** call `_load_env(root / ".env")` before attempting any DB connection; reads `.env` without requiring the shell to have exported the vars
- **psycopg2 connection:** `host=host.docker.internal`, `dbname="signal"`, credentials from env
- **Per-ticker commits:** commit after processing each ticker; a kill signal leaves work done, not partially written
- **Kill-safe:** the outer loop is safe to interrupt at any point; re-running resumes from the last uncommitted ticker
- **`--start` flag:** `argparse` argument `--start YYYY-MM-DD` skips records before that date; omitting it processes full history
- **Run inside Docker:**
  - Backfill scripts (DB writes, bounded memory): `docker compose exec airflow-scheduler python /opt/airflow/scripts/<name>.py`
  - **Analysis scripts** (full-universe reads, parquet intermediates, ML training): `docker compose run --rm signal-tools python /opt/airflow/scripts/<name>.py`
    The `signal-tools` service has `mem_limit: 8g` and is isolated from the scheduler. An OOM in it cannot take the scheduler down.
- **Idempotent:** `ON CONFLICT DO NOTHING` or equivalent; re-running produces the same end state

### Analysis script memory pattern (analyze_*.py, train_*.py)

Scripts that load the full universe must use bounded-memory batching:

- Process tickers in batches of `config.TOOLS_TICKER_BATCH` (500).
- Load SPY series and beta map once globally; reuse per batch.
- Accumulate results to a **parquet file** per stage (never hold the full universe in RAM simultaneously).
- Use `float32` for all return/feature columns; `category` dtype for ticker column.
- Print per-batch RSS (via `psutil` if available, else `/proc/self/status VmRSS`).
- Final aggregation reads from parquet — never from in-memory intermediates.
- **dtypes at load time**, not after: cast immediately in the SQL-to-DataFrame step.

**Example (`scripts/backfill_predictions.py:34-52`):**

```python
def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

def _get_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "host.docker.internal"),
        dbname="signal",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
```

---

## 6. Loud-fallback pattern

Any fallback path (default beta, skipped ticker, missing benchmark) must log its usage count per run. A fallback firing for >10% of rows is a warning; one firing for ~100% is a STOP condition — it means the primary path is broken.

**Example — beta fallback in `resolve_outcomes_v2` (`dag_components/outcome_tracker/tasks.py:367`):**

```python
beta_row = hook.get_first(
    "SELECT beta FROM sector_beta WHERE ticker = %s AND etf_proxy = 'SPY' AND window_days = %s",
    parameters=(ticker, beta_window),
)
beta = float(beta_row[0]) if beta_row else 1.0
```

When `sector_beta` was corrupted (see §3), `beta_row` returned `None` for every ticker and `beta` silently fell back to `1.0` for 100% of rows — a STOP condition that was only caught by a data audit. The current version logs `seeded` and `resolved` counts; a production-hardened version would also log `beta_fallback_count` and check it against the total.

**Rule:** Every fallback path needs: (a) a counter incremented on each use, (b) a log statement at the end of the function reporting the count, and (c) a check that halts or warns if the count exceeds the threshold.

---

## 7. Versioning conventions

- `SIGNAL_VERSION` (`config/config.py:147`): governs v1 scoring. Format `"vMAJOR.MINOR"`. Increment major on any change to `SIGNAL_WEIGHTS` structure or VIX multipliers; increment minor on threshold changes. Current: `"v1.1"`.
- `SIGNAL_VERSION_V2` (`config/config.py:172`): governs v2 scoring. Current: `"v2.0"`. Same increment semantics — major for feature/model changes, minor for threshold/calibration changes.
- Backfill versions: append `-backfill` (e.g., `"v1.0-backfill"`, `"v2.0-backfill"`). The `signal_predictions` PK is `(ticker, signal_date, signal_version)` so backfill and live rows coexist.

Increment `SIGNAL_VERSION` whenever the scoring logic changes in a way that makes old and new predictions non-comparable. Increment `SIGNAL_VERSION_V2` identically for v2 changes.

---

## 8. Episode and row-offset conventions

Forward price lookups use row offsets into `raw_prices`, never calendar arithmetic.

**Rule:** "5 trading days after the signal" means row index 4 (0-based) in the price sequence returned by `SELECT close FROM raw_prices WHERE ticker = %s AND date > %s ORDER BY date LIMIT %s`. Calendar arithmetic — subtracting/adding integers to a `date` — is never used for trading-day counting.

**Example (`dag_components/outcome_tracker/calculations.py` — `nth_trading_day_price`):**

```python
def nth_trading_day_price(price_rows: list[dict], n: int) -> float | None:
    if len(price_rows) < n:
        return None
    return float(price_rows[n - 1]["close"])
```

`price_rows` is always `SELECT ... WHERE date > signal_date ORDER BY date`; row 0 is trading day 1 after signal.

**Episodes** (`calculations_v2.assign_episodes`): a new episode starts when the gap since the previous same-bias signal on a ticker exceeds `V2_EPISODE_GAP_DAYS` **trading days**, measured by row offset in the ticker's per-bias signal sequence — again, not calendar days. This is consistent with the row-offset convention throughout the codebase.

---

## 9. Robust-statistics reporting rule

All reported return metrics use **median** and **winsorized (1%/99%) mean** — never raw means. Sample counts distinguish raw rows from independent episodes.

**Why:** Return distributions contain legitimate extreme values (10× on a $0.50 stock in two weeks) from the adjusted-price seam bug (see §3) and from genuine small-cap volatility. Raw means are destroyed by these; medians and winsorized means are robust. The Phase 0 findings doc excluded `avg_return` columns from several queries because the raw-price data was not yet clean — those are the canonical example of what happens when this rule is violated.

**Rule:** Any query or function that aggregates returns must report: `median_return`, `winsorized_mean_return` (1%/99%), `n_rows`, and `n_episodes` (episode-head count). A function that reports only `avg_return` is non-compliant.
