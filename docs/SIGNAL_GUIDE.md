# SIGNAL_GUIDE.md — Project Signal conventions

status: canonical (repo-local). Convention lens — see
`../project-jarvis/docs/dev-standards/QA_LENSES.md` §3 for how this file is consumed.
scope: **Signal-specific.** Rules here are domain conventions that no shared lens covers,
or that Signal enforces more strictly than a shared lens. Anything already covered by
`@std/PY_STANDARDS.md`, `@std/SQL_STANDARDS.md`, `@std/AIRFLOW_STANDARDS.md`, or
`@std/API_CLIENT_STANDARDS.md` is **not** restated here — those lenses bind directly.

The prose below teaches. The `## Review Checklist` tail is the enforced surface.

---

## Lens overrides (QA_LENSES §3 precedence)

Two rules below overlap a shared-lens checklist line. Per §3, each names the superseded
line and the reason. Both are **strengthenings** — neither relaxes the shared bar.

| This guide's rule | Supersedes | Reason |
|---|---|---|
| Fallbacks must be loud (per-run usage count; >10% warn; ~100% STOP) | `PY_STANDARDS.md` → *"Degraded/partial-success paths log or surface the degradation — they do not return silently."* | Signal quantifies the bar. "Logged somewhere" is insufficient for a nightly batch pipeline where a silently-100%-firing fallback looks identical to success. |
| Config-driven everything: all numeric/behavioral tunables in `config/config.py` | `API_CLIENT_STANDARDS.md` → *"Base URLs, rate/tier parameters, timeouts, and model names live in config, not call sites."* | Signal broadens the scope from client-layer config to **every** DAG, plugin, and calculation file. |

Pure-pandas math (below) *extends* `PY_STANDARDS.md`'s stdlib-first bias into a domain-specific
dependency ban. It supersedes no checklist line and is not an override.

---

## Data plausibility — STOP conditions

Any value implausible on its face is a STOP condition, not a documentation item:

- betas outside [−2, 5]
- returns > 100x on names ≥ $1
- counts that should be zero but aren't
- a median that contradicts its own distribution's direction statistics
- a column holding values that belong to a different column

When hit: stop, state the finding and most likely root cause, wait for review. Do not
classify surprises as acceptable, do not absorb them with fallbacks, do not proceed past
them. Three historical "document-and-proceed" calls on this project all traced to real
production defects — this rule is empirically earned, not defensive.

## Fallbacks must be loud

Any fallback path (default beta, skipped ticker, missing benchmark) logs its usage count
per run. A fallback that fires for >10% of rows is a warning; one that fires for ~100% is
a STOP condition — it means the primary path is broken and the run is silently producing
garbage that looks like output.

## Robust statistics

All reported return metrics use median and winsorized (1%/99%) mean — never raw means.
A single 100x outlier moves a raw mean enough to invert a conclusion. Sample counts
distinguish raw rows from independent episodes; overlapping windows are not independent
observations and must not be counted as such.

## Adjusted prices are epoch-sensitive

Stored adjusted prices are valid only as of fetch time; corporate actions retroactively
change the basis. The corporate-actions refresh task re-bases affected tickers. Never
compute returns across an un-refreshed split seam — the quarantine guard enforces this,
and it exists because the failure is silent: the numbers are plausible, just wrong.

## Pure-pandas math

No indicator or statistics libraries in runtime code (no pandas-ta). sklearn is permitted
only inside `scripts/` for offline fitting; runtime scoring is pandas/numpy. The math is
the product — it is auditable only if it is legible in the repo.

## Humans commit artifacts

Optimizers and trainers write proposals and coefficient files; a human reviews and commits
them. Nothing self-applies to `config/config.py`. No code path writes to config.

## Workstream isolation

v1 scoring logic is frozen during the Signal v2 build (see `signal_v2_rebuild.md` ground
rules). Do not modify it except where the spec explicitly allows. *This rule expires when
the v2 workstream closes; remove it from the checklist tail at that point.*

## Database conventions

Beyond `@std/SQL_STANDARDS.md`:

- Tables are **never** created or altered from Python. `sql/schema.sql` is the definition;
  numbered `sql/migrations/` files are the only mechanism of change.
- `DELETE` / `TRUNCATE` never appear in a DAG task. Destructive data operations live only
  in migration files, each with a comment explaining why.
- DAG tasks connect via the Airflow Postgres hook (`signal_postgres`). Standalone `scripts/`
  connect via psycopg2 with `dbname="signal"` and `.env` credentials.
- Time-series primary keys are `(ticker, date)`.

## Insert mapping tests

Every `execute_values` / INSERT must have a test asserting the tuple field order matches
the SQL column list — a mapping test, not just a math test. This class of bug has occurred
in production here: the math was right and the columns were transposed. The tests are the
fence.

## Test coverage posture

Beyond `@std/PY_STANDARDS.md`: every module has a test file, carrying **math tests and seam
tests**. Edge cases named in a spec are mandatory, not optional.

## Backfill scripts

Standalone, per-ticker commits, kill-safe, re-runnable, `--start` flag, run inside Docker.
A backfill that cannot be killed and resumed will be killed and not resumed.

## Versioning

Predictions are tagged with `SIGNAL_VERSION` / `SIGNAL_VERSION_V2`. Backfills append
`-backfill` to the version tag.

## Single-module boundaries

- All market data flows through EODHD via `plugins/eodhdclient.py`.
- All API rate limiting lives in `plugins/base_client.py` — the single throttle point.
- All tunables live in `config/config.py`. Nothing numeric or behavioral is hardcoded in
  DAG, plugin, or calculation files.

---

## Environment

Operational knowledge. Not diff-reviewable (except the DagBag import rule, which is in the
checklist tail) — this section teaches, it does not gate.

- Postgres is native Windows; containers reach it via `host.docker.internal`.

- **Two execution contexts for scripts:**
  - DAG-related scripts (`backfill_*.py`, one-off data fixes) — use the scheduler:
    `docker compose exec airflow-scheduler python /opt/airflow/scripts/<name>.py`
  - **Heavy analysis scripts** (`analyze_*.py`, `train_*.py`, anything loading the full
    universe into memory) — use the dedicated tools container:
    `docker compose run --rm signal-tools python /opt/airflow/scripts/<name>.py`
    The `signal-tools` service (profile: `tools`) has `mem_limit: 8g, cpus: 4` and runs
    independently — an OOM in it cannot kill the scheduler.
    Start it with: `docker compose --profile tools up -d signal-tools`

- After editing `config/config.py`, restart the scheduler — WSL2 mounts have
  low-resolution mtimes and workers can run stale `.pyc`:
  `docker compose restart airflow-scheduler`
  **Only do this when no DAG run or backfill is active** — check `docker compose ps` and
  the Airflow UI first.

- DAG files must contain the literal strings "dag" and "airflow" for DagBag safe-mode. The
  `from airflow.models.dag import DAG  # noqa: F401` import in each DAG file exists for this
  reason — removing it makes the DAG invisible to the scheduler, silently.

---

## Review Checklist

- [ ] No implausible value accepted or documented-around: betas outside [−2, 5]; returns > 100x on names ≥ $1; a should-be-zero count that isn't; a median contradicting its distribution's direction stats; a column holding another column's values.
- [ ] Every fallback path (default beta, skipped ticker, missing benchmark) logs a per-run usage count.
- [ ] No fallback is introduced whose expected firing rate exceeds 10% of rows without an explicit justification at the call site.
- [ ] Reported return metrics use median and winsorized (1%/99%) mean; no raw `.mean()` on a return series.
- [ ] Sample counts distinguish raw rows from independent episodes; overlapping windows not counted as independent.
- [ ] No return computed across an un-refreshed split seam; the quarantine guard is not bypassed or weakened.
- [ ] No indicator/statistics library in runtime code (no pandas-ta); sklearn confined to `scripts/`.
- [ ] No code path writes to `config/config.py`; optimizer/trainer output is a proposal artifact for human commit.
- [ ] v1 scoring logic unmodified, unless the v2 spec explicitly authorizes the change. *(Remove when the v2 workstream closes.)*
- [ ] No `CREATE TABLE` / `ALTER TABLE` issued from Python; schema changes are `sql/schema.sql` + a numbered migration.
- [ ] No `DELETE` or `TRUNCATE` in a DAG task; destructive operations appear only in migration files, each with a why-comment.
- [ ] DAG tasks use the `signal_postgres` Airflow hook; standalone `scripts/` use psycopg2 with `dbname="signal"`.
- [ ] New time-series tables use a `(ticker, date)` primary key.
- [ ] Every new/changed `execute_values`/INSERT has a test asserting tuple field order matches the SQL column list.
- [ ] Every new module has a test file containing both math tests and seam tests; spec-named edge cases are tested.
- [ ] New/changed backfill scripts are kill-safe, re-runnable, commit per ticker, and accept `--start`.
- [ ] Predictions are tagged with `SIGNAL_VERSION` / `SIGNAL_VERSION_V2`; backfill runs append `-backfill`.
- [ ] Market data is fetched only via `plugins/eodhdclient.py`; rate limiting only via `plugins/base_client.py`.
- [ ] No numeric or behavioral tunable hardcoded in a DAG, plugin, or calculation file — it belongs in `config/config.py`.
- [ ] Each DAG file retains `from airflow.models.dag import DAG  # noqa: F401` (DagBag safe-mode requires the literal strings).
