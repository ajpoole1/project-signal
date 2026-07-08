# CONTEXT.md — session handoff log

Session entries are scaffolded by `/handoff` at every session end. Newest entry at the top.
This file is the running memory across authoring sessions; keep entries tight and specific.

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
