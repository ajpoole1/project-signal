# CONTEXT.md — session handoff log

Session entries are scaffolded by `/handoff` at every session end. Newest entry at the top.
This file is the running memory across authoring sessions; keep entries tight and specific.

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
