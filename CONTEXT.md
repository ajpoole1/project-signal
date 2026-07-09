# CONTEXT.md — session handoff log

Session entries are scaffolded by `/handoff` at every session end. Newest entry at the top.
This file is the running memory across authoring sessions; keep entries tight and specific.

---

## Session — 2026-07-08 (cont.) — §7 migration execution: PR 1 + PR 2

**Context:** Consolidated AWS migration plan reached v1.2 (plan of record, absorbed the
§7 implementation-plan reviews). This session executed the [SIGNAL] §7 pre-migration
repo work as staged PRs. Plan artifact (reconciled with v1.2):
https://claude.ai/code/artifact/c48f2c99-7da0-4ab8-ac88-fb1472f0b476

**Completed — PR #17 (merged → main `ecf97d0`), "PR 1":**
- **§7.4 connection rewiring** — new `scripts/_db.py` shared helper (`load_env` +
  `get_connection`, env-driven `POSTGRES_HOST` default `localhost`, never
  host.docker.internal). All 10 scripts rewired to it. `docker-compose.yml` DB host →
  `${POSTGRES_HOST:-host.docker.internal}` (verified via `docker compose config`: box→RDS,
  local→gateway). `POLYGON_API_KEY` dropped from compose.
- **§7.2 relatedness stop-safety** — replaced up-front `TRUNCATE relatedness_matrix` with
  per-window `DELETE ... WHERE window_days=%s` + INSERT committed per window (5am kill can't
  leave the table globally empty). `TestRelatednessStopSafety` (static-source) fences it.
- **§7.3 relatedness trim** — `CORRELATION_WINDOWS=[90,365]` (30d dropped), `RELATEDNESS_MIN_R`
  0.20→0.50. Migration `011` (MANUAL-marked) deletes orphaned rows. Validated on live DB:
  **20.1M → 558K rows (97% cut)**. Finding F (relatedness_matrix has no wired reader) +
  wire-or-retire backlog item + PR-3 wind-down mechanism recorded in roadmap decision log.

**Completed — PR #18 (merged → main `a60ee1b`), "PR 2":**
- **§7.6** — `scripts/deploy.sh` (deploy-on-boot: git reset → apply auto-safe migrations →
  tier-detect → smoke check). **Mechanical `-- MANUAL` destructive-migration guard** (greps
  file CONTENTS, not filename — 008 is named "bugfix" but DELETEs). Migrations 006/008/009
  marked retroactively. New **DagBag-import CI job** in `quality.yml` (airflow 2.9.0 +
  postgres provider + constraints) — the only pre-prod DAG-parse gate now Tier-2 Docker is gone.
- **§7.5 (partial)** — orchestrator cron → `5 2 * * 0-5` with tz-aware America/Toronto
  start_date (2:05am ET, DST-correct); `alert_on_failure` TaskFlow `@task(ONE_FAILED)`.
- **§7.7** — signal-tools mem cap 8g→4g (224 MiB peak, ~18x headroom); `jarvis` least-privilege
  Airflow role/account in airflow-init (read DAGs/Runs/Tasks/Logs + create DagRuns; password
  from `JARVIS_AIRFLOW_PASSWORD`, skipped locally).
- **§7.9** — deleted dormant polygon/yfinance clients + tests + `backfill_history.py`; removed
  polygon residue from `base_client.py` (kept `BaseMarketClient`); `source` default →`eodhd`.

**Worth remembering:**
- **The DagBag CI gate paid off on its first run** — it caught `ModuleNotFoundError:
  airflow.providers.postgres` (bare pip install doesn't bundle providers like the container
  image does). Fixed by adding `apache-airflow-providers-postgres`; verified by reproducing the
  CI install in a throwaway venv (all 7 DAGs parse). Lesson: the DagBag CI job's dep set must
  track every `airflow.providers.*` the DAGs import, separately from the container image.
- deploy.sh applies non-MANUAL migrations every boot relying on idempotency (no
  `schema_migrations` tracking table). Fine now; revisit if migration volume grows.

**In progress:** Nothing.

**Blocked:**
- **PR 3 (§7.5 remainder)** — wind-down task + run-summary writer. Wind-down MECHANISM is
  resolved and recorded (async-invoke `workbench-stop` Lambda, never raw rds:Stop; ownership
  Signal-self-check until ≥2 tenants). Blocked on: **Q11** run-summary schema v1 (Jarvis drafts
  → Signal reviews for producibility) and **S3/boto3** prefix from infra. Do NOT build until
  those land, or it's built stale.

**Next:**
- When Q11 schema + S3 land: build PR 3 — orchestrator run-summary writer (writes JSON to
  `signal/run-summaries/` on `all_done`, incl. `deploy` + `data_gates` blocks) + wind-down task.
- **§7.8 sizing report** — per-table sizes + wall-clocks, measured on the inauguration backfill
  day (§8), a step-7 deliverable. Ticker count (6,419) already delivered.
- Signal side of §7 otherwise complete; remaining work gates on [INFRA] provisioning (§9 step 4)
  and [JARVIS] schema draft.

---

## Session — 2026-07-08

**Context:** AWS-migration capacity review. Target box is a shared t4g.large (arm64,
8 GiB, ~3h nightly window, shared RDS Postgres). Question: can Signal run nightly there,
and if memory-bound, spread the DAG rather than pay for a bigger instance? Operator OK'd
widening the window to 4–5h if needed.

**Completed:**
- **Landed the indicator batch-streaming refactor** (PR #16, merged → `main` `2954eba`).
  - Root cause fixed: `dag_components/indicators/tasks.py` `fetch_price_history` returned a
    universe-sized `dict[str, list[dict]]` (~7k tickers × ~280d OHLCV) **across a task
    boundary → XCom** (built, pickled, written to metadata DB, read back — 2–3 copies
    resident). That was the box's memory ceiling and the most likely 3am OOM.
  - New `dag_components/indicators/pipeline.py` — pure, airflow-free helpers (`read_prices`,
    `build_vix_context`, `compute_ticker_row`, `upsert_batch`, `rss_mb`). One source of truth
    for DAG + gate; importable without Airflow so the gate runs from WSL.
  - `tasks.py` now thin: `fetch_price_history` → `int` count-check (no bulk XCom);
    `compute_and_upsert_indicators` streams the universe in `config.INDICATOR_BATCH_SIZE`
    (=500) blocks: read → compute → upsert → release. Two-task topology preserved.
  - `tests/test_stock_signals_insert.py` — insert-mapping fence for the moved 18-col
    `stock_signals` INSERT (tuple order == column list; verified to have teeth).
  - `scripts/verify_indicators_batch.py` — acceptance gate (parity + peak RSS + streaming).

**Measured result (feeds AWS migration §6.1 / open Q7):**
- Live 6,419-ticker universe (2026-07-07): **parity diff 0** (max |Δ| 0.00e+00), **peak RSS
  224 MiB**, RSS flat across all 13 batches. The heaviest indicator run now fits in ~0.2 GiB
  vs. the old whole-universe ceiling. **8 GiB is enough; no larger instance needed.** Relatedness
  (Sunday) was already chunked — not a concern. Trade: wall-clock grows, memory ceiling flat.

**Decisions worth remembering:**
- The general lever for the rest of the migration if any stage is memory-bound: stream in
  `config`-driven batches (read → compute → write → release), trading the widened window for a
  flat RSS ceiling. XCom carries counts/refs only, never universe-sized payloads.
- Process fix this session: `qa`/`ship`/`handoff` skills + `CHARTER.md` lived only on the
  unmerged `feature/dev-standards-rollout` branch, so `/qa` couldn't resolve. Merged that first
  (PR #15), rebased this branch onto it. Skills register at session start — needed the merge
  before the verbs worked.

**In progress:**
- Nothing.

**Blocked:**
- Nothing.

**Next:**
- **`docker-compose.yml` still hardcodes `host.docker.internal` + `extra_hosts`** for the DB
  (lines ~8/14/18/30). The migration spec says these must become the RDS endpoint (`extra_hosts`
  gets dropped on the box). Env-driven `POSTGRES_HOST` swap — required migration change, not yet done.
- Other nightly stages not yet measured against 8 GiB (ingest/llm are per-ticker loops, low risk;
  relatedness already chunked). Batch-stream any that prove memory-bound using the pattern above.

---

## Session — 2026-07-07

**Completed:**
- Rolled project-signal onto the shared dev-standards framework
  (`../project-jarvis/docs/dev-standards/ROLLOUT.md`).
  - `CHARTER.md` — filled from the hub template. Declares **5** QA lenses: four `@std/`
    (PY/SQL/AIRFLOW/API_CLIENT) + repo-local `docs/SIGNAL_GUIDE.md`.
  - `scripts/checks.sh` — canonical check script. **CI invokes it directly**
    (`.github/workflows/quality.yml`); the three inline ruff/pytest steps were replaced by
    one call, so local and CI cannot drift on what "green" means.
  - Vendored `/ship`, `/qa`, `/handoff` under `.claude/skills/` (provenance 2026-07-07).
  - `.claude/settings.json` — `additionalDirectories` grant for the standards root.
- **Brownfield triage** (the substantive work). `CLAUDE.md` was a 155-line hand-written
  contract competing with the lens set. Every block was assigned exactly one authority:
  - deleted what a declared lens already enforces verbatim (calc/tasks split, TaskFlow-only,
    single throttle point, `ON CONFLICT` posture, calendar-counting-from-rows);
  - repo values (branch flow, CI entry points) → `CHARTER.md`;
  - enforceable domain conventions → new `docs/SIGNAL_GUIDE.md`, a repo-local lens
    (prose teaches, `## Review Checklist` tail enforces, 20 items);
  - identity/verbs/safety/navigation stayed in the overlay.
  - Net: `CLAUDE.md` 155 → ~50 lines, and now carries a line telling the next session not to
    re-grow it. The STOP conditions, split-seam invariant, insert-mapping-test rule, and
    robust-statistics requirement are **enforced by `/qa` for the first time** — as prose they
    never were.
  - Two `QA_LENSES.md` §3 overrides declared in the guide's override table; both strengthen a
    shared-lens line, neither relaxes one.

**Decisions worth remembering:**
- Signal has **no dev-loop** (no spec queue, no `start-build.sh`/`open-pr.sh`). The check script
  lives at `scripts/checks.sh`, not `scripts/dev-loop/`. `/ship`'s vendored copy has its
  dev-loop-item branches stripped — recorded as a `LOCAL DEVIATION` comment under its provenance
  line. Operator intends to bring a dev-loop in later; re-vendor `/ship` from the hub at that point.

**In progress:**
- Nothing.

**Blocked:**
- Nothing.

**Next:**
- Re-run `/qa` (five blocking findings from the first pass are now fixed), then `/ship`.
- Push + PR are the operator's actions; nothing has been pushed.
- Stale local `feature/*` refs (7, all merged into `origin/main`, none on origin) can be pruned:
  `git branch -d feature/…`. Zombie branches per DEV_BASE §3.4.
- `.github/workflows/*.yml` still trigger on `pull_request: branches: [main, develop]`.
  `develop` was never created — out of scope for this PR, worth a cleanup.
