# Project Signal — Agent Operating Guide

Nightly stock intelligence pipeline. Airflow + Postgres + EODHD + Anthropic API.
Portfolio-grade open source: logic is public; API keys and personal watchlists are local-only.

This file is the contract for how to work in this repo. Architecture, history, and
reference detail live in `docs/` — load them when the task needs them, not by default.

## Doc map — read before assuming

| Need | Read |
|---|---|
| Writing or modifying any code | `docs/design_patterns.md` (mandatory) |
| Pipeline structure, schema, data flow, why decisions were made | `docs/architecture.md` |
| Phase status, current focus, what's in flight | `docs/roadmap.md` |
| EODHD endpoints, model selection, universe stats | `docs/reference.md` |
| Signal v2 workstream spec and amendments | `docs/signal_v2_rebuild.md` |
| Empirical findings (what the data has shown) | `docs/signal_v2_phase0_findings.md`, `_phase3_`, `_event_study` |

If this file and a `docs/` file conflict, this file wins; flag the conflict.

## Non-negotiable rules

**Security**
- Never hardcode secrets. All credentials via `.env` (local) or environment variables.
- Never commit: `.env`, `data/`, `logs/`, any `.db` file, personal watchlist extensions.
- Always commit `.env.example` with placeholders. Verify no secrets staged before any PR.

**Database**
- All tables defined in `sql/schema.sql`; all changes via numbered `sql/migrations/` files.
  Never create or alter tables from Python.
- DAG-task writes are idempotent: `ON CONFLICT DO UPDATE` (or `DO NOTHING` where the
  spec says outcomes must never be overwritten). Never `DELETE`/`TRUNCATE` in DAG tasks —
  destructive data operations live only in migration files, with a comment explaining why.
- DAG tasks connect via the Airflow Postgres hook (`signal_postgres`). Standalone
  `scripts/` connect via psycopg2 with `dbname="signal"` and `.env` credentials.
- Time-series PKs are `(ticker, date)`; trading-day counting is by row offset from
  `raw_prices`, never calendar arithmetic.

**Data plausibility — STOP conditions**
Any value implausible on its face is a STOP condition, not a documentation item:
betas outside [−2, 5]; returns > 100x on names ≥ $1; counts that should be zero but
aren't; a median that contradicts its own distribution's direction statistics; a column
holding values that belong to a different column. When hit: stop, state the finding and
most likely root cause, wait for review. Do not classify surprises as acceptable, do not
absorb them with fallbacks, do not proceed past them. (Three historical
"document-and-proceed" calls on this project all traced to real production defects.)

**Fallbacks must be loud.** Any fallback path (default beta, skipped ticker, missing
benchmark) logs its usage count per run. A fallback that fires for >10% of rows is a
warning; one that fires for ~100% is a STOP condition — it means the primary path is broken.

**Workstream isolation.** v1 scoring logic is frozen during the Signal v2 build
(see `docs/signal_v2_rebuild.md` ground rules). Do not modify it except where the spec
explicitly allows.

## Design principles

- **Single data source.** All market data flows through EODHD via `plugins/eodhdclient.py`.
- **Adjusted prices are epoch-sensitive.** Stored adjusted prices are valid only as of
  fetch time; corporate actions retroactively change the basis. The corporate-actions
  refresh task re-bases affected tickers; never compute returns across an un-refreshed
  split seam (the quarantine guard enforces this — respect it).
- **Separation of concerns.** Independent DAGs sequenced by `dag_orchestrator`; each can
  fail and retry without corrupting the others. DAG files contain orchestration only.
- **Single throttle point.** All API rate limiting lives in `plugins/base_client.py`.
- **Config-driven everything.** All tunables in `config/config.py`. Nothing numeric or
  behavioral is hardcoded in DAG, plugin, or calculation files.
- **Pure-pandas math.** No indicator/stat libraries (no pandas-ta). sklearn is permitted
  only inside `scripts/` for offline fitting; runtime scoring is pandas/numpy.
- **Humans commit artifacts.** Optimizers and trainers write proposals/coefficients;
  a human reviews and commits them. Nothing self-applies to config.
- **Robust statistics.** All reported return metrics use median and winsorized (1%/99%)
  mean — never raw means. Sample counts distinguish raw rows from independent episodes.

## Code patterns (summary — full versions in docs/design_patterns.md)

- **calc/tasks split:** pure functions in `dag_components/<area>/calculations*.py`
  (no Airflow imports, no DB, no network); `@task` wrappers in `tasks.py`. TaskFlow API
  only — no classic operators for Python logic.
- **Insert mapping:** every `execute_values`/INSERT must have a test asserting tuple
  field order matches the SQL column list (mapping test, not just math test). This class
  of bug has occurred; the tests are the fence.
- **Every module has a test file.** Math tests AND seam tests. Edge cases from the spec
  are mandatory, not optional.
- **Backfill scripts:** standalone, per-ticker commits, kill-safe, re-runnable, `--start`
  flag, run inside Docker.
- **Versioning:** predictions tagged with `SIGNAL_VERSION` / `SIGNAL_VERSION_V2`;
  backfills append `-backfill`.

## Environment gotchas

- Postgres is native Windows; containers reach it via `host.docker.internal`.
- **Two execution contexts for scripts:**
  - DAG-related scripts (backfill_*.py, one-off data fixes): use the scheduler:
    `docker compose exec airflow-scheduler python /opt/airflow/scripts/<name>.py`
  - **Heavy analysis scripts** (analyze_*.py, train_*.py, anything loading the
    full universe into memory): use the dedicated tools container:
    `docker compose run --rm signal-tools python /opt/airflow/scripts/<name>.py`
    The `signal-tools` service (profile: `tools`) has `mem_limit: 8g, cpus: 4`
    and runs independently — an OOM in it cannot kill the scheduler.
    Start it with: `docker compose --profile tools up -d signal-tools`
- After editing `config/config.py`, restart the scheduler — WSL2 mounts have
  low-resolution mtimes and workers can run stale `.pyc`:
  `docker compose restart airflow-scheduler`
  **Only do this when no DAG run or backfill is active** — check
  `docker compose ps` and the Airflow UI first.
- DAG files must contain the literal strings "dag" and "airflow" for DagBag safe-mode;
  the `from airflow.models.dag import DAG  # noqa: F401` import in each DAG file exists
  for this reason — do not remove it.
- CI must be green before merging: ruff lint + format, pytest (3.11/3.12),
  TruffleHog/gitleaks, `docker compose config`.
  Branch flow: `feature/*` → `develop` → `main` (protected).

## When finishing any task

1. Run `ruff check .`, `ruff format --check .`, `pytest` — all green.
2. Run the task's verification gates and report actual outputs against expected values.
3. Update `docs/roadmap.md` status and any `docs/` file your change made stale.
   Doc drift misleads future agent sessions — treat stale docs as a bug.
