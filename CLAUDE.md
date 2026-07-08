# Project Signal — Author Overlay

**This is the at-desk authoring session.** The operator (AJ) driving via Claude Code.
This repo is stood up on the shared dev-standards framework (see
`../project-jarvis/docs/dev-standards/ROLLOUT.md`).

## Contract

Inherit `../project-jarvis/docs/dev-standards/DEV_BASE.md` (§5 — Author role) + `CHARTER.md`.

This file is the **overlay only**: identity, verbs, safety, navigation. It states no code
convention and no charter value. Conventions are enforced as lenses declared in
`CHARTER.md → ## QA Lenses`; repo values (stack, check command, QA params) live in `CHARTER.md`.
If you are about to add a rule here, it belongs in `docs/SIGNAL_GUIDE.md` or the charter instead.

**The verbs:**
- `/ship` — the only exit ramp: checks → `/qa` → commit+push → PR
- `/qa` — lens review before any PR (also runs inside `/ship`)
- `/handoff` — scaffold session entry in `CONTEXT.md`; run at every session end

## Safety essentials (non-negotiable, never skip)

- **Secrets never hardcoded.** All credentials via `.env` (local) or environment variables.
  `.env.example` is committed with placeholders.
- **Personal data / secrets never staged.** Before any commit: verify no `.env`, `data/`,
  `logs/`, `.db`, or personal watchlist files are staged.
- **Push is always a human action.** `/ship` opens the PR; merge is the operator's action.
- **STOP conditions are real stops.** Implausible data and ~100%-firing fallbacks halt the
  session for review — never documented-around. Defined in `docs/SIGNAL_GUIDE.md`.

## Doc map

Load these when the task needs them, not by default.

| What you need | Where to look |
|---|---|
| Dev contract (all roles) | `../project-jarvis/docs/dev-standards/DEV_BASE.md` |
| QA lens definitions | `../project-jarvis/docs/dev-standards/QA_LENSES.md` |
| Repo goal, stack, check command, QA params, declared lenses | `CHARTER.md` |
| **Signal conventions + environment gotchas** | `docs/SIGNAL_GUIDE.md` |
| Shared Python / SQL / Airflow / API-client conventions | `@std/…` lenses declared in `CHARTER.md` |
| Writing or modifying any code | `docs/design_patterns.md` |
| Pipeline structure, schema, data flow, why decisions were made | `docs/architecture.md` |
| Phase status, current focus, what's in flight | `docs/roadmap.md` |
| EODHD endpoints, model selection, universe stats | `docs/reference.md` |
| Signal v2 workstream spec and amendments | `docs/signal_v2_rebuild.md` |
| Empirical findings (what the data has shown) | `docs/signal_v2_phase0_findings.md`, `_phase3_`, `_event_study` |
| Session handoff log | `CONTEXT.md` |

**Precedence.** For *review criteria*, the declared lenses bind (`CHARTER.md → ## QA Lenses`);
where `docs/SIGNAL_GUIDE.md` conflicts with a shared lens, the guide wins and must declare the
override (QA_LENSES §3 — see the guide's override table). For *navigation and repo facts*, this
file and `CHARTER.md` win over `docs/`; flag any conflict you find.

## Finishing a task

`DEV_BASE.md` §2.3 (green before commit), §2.4 (never fail silent — report what was actually
verified, with real outputs), and §2.5 (doc hygiene — code and its docs land together; stale
docs are a bug) bind. Run the check command from `CHARTER.md`, then `/ship`.

End every session with `/handoff`.
