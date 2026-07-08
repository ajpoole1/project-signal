# Project Charter — project-signal

<!-- Filled charter for project-signal. The blank template lives at
     ../project-jarvis/docs/dev-standards/CHARTER_TEMPLATE.md — edit that file, not this
     comment, to update the template. Invariant sections (build rigor, QA contract) are
     inherited from ../project-jarvis/docs/dev-standards/DEV_BASE.md and referenced here —
     not restated. Stood up per docs/dev-standards/ROLLOUT.md in the hub. -->

---

## Goal

**project-signal** is a nightly stock-intelligence pipeline: it ingests market data, computes
indicators and relatedness, scores tickers, and tracks prediction outcomes — portfolio-grade
open source where the logic is public and API keys / personal watchlists stay local.

- **Nightly pipeline:** independent Airflow DAGs (ingest → indicators → relatedness →
  scoring → outcome tracking) sequenced by `dag_orchestrator`, each able to fail and retry
  without corrupting the others.
- **Signal scoring:** pure-pandas indicator/statistics math over an EODHD-sourced universe,
  with a v2 rebuild workstream (event studies, regime-conditional tuning) in flight.

## Stack

| Layer | What |
|---|---|
| Orchestration | Apache Airflow (TaskFlow API) — one DAG per pipeline stage, `dag_orchestrator` sequences them |
| Compute | Python 3.11/3.12 — pure pandas/numpy runtime math; sklearn only in offline `scripts/` |
| Data store | PostgreSQL (native Windows) — schema in `sql/schema.sql`, changes via `sql/migrations/` |
| Market data | EODHD via `plugins/eodhdclient.py` (single source); Polygon/yfinance clients present |
| LLM | Anthropic API — analysis/scoring augmentation |
| Runtime | Docker Compose — scheduler + `signal-tools` (heavy-analysis) container |

## Build Rigor

Inherited from `../project-jarvis/docs/dev-standards/DEV_BASE.md`. Summary:

- Spec-literal implementation on `feature/<id>` branches.
- `scripts/checks.sh` green before every commit (single source of truth — CI runs the same file).
- Two human gates (authorize + merge). No auto-merge.
- Halt-and-ask on ambiguity — never guess.
- Land work via `/ship`; run `/qa` before PR; scaffold handoff with `/handoff`.

**Branch flow:** `feature/*` → `main`. `main` is protected: PR + green CI required. `develop`
was never created.

Repo-specific STOP conditions (data plausibility, loud fallbacks, v1 scoring freeze during the
v2 build) are defined in `docs/SIGNAL_GUIDE.md` and enforced as a declared lens — they extend,
and never relax, this base.

## QA Pairing

| Role | Model | Rationale |
|---|---|---|
| Builder | Claude Opus 4.8 | authoring + implementation |
| Reviewer | Claude Sonnet 5 (`/qa`) + CI security/quality gates | local QA model ≠ builder model — decorrelates blind spots |

The builder builds; the reviewer reviews the diff independently. Heterogeneous QA is a
structural requirement, not a style preference.

**Fail-closed:** if the reviewer cannot run, the CI check is red. Merge is blocked until it
passes or the operator explicitly overrides.

## QA Parameters

| Parameter | Value |
|---|---|
| Check command | `scripts/checks.sh` (ruff check + ruff format + pytest --cov); invoked directly by CI `quality.yml` — one file, no drift |
| QA model | Claude Sonnet 5 (local `/qa` fork) |
| Reviewer entry point | `.github/workflows/quality.yml` (ruff + pytest) · `.github/workflows/security.yml` (TruffleHog + gitleaks) · `.github/workflows/docker.yml` (compose config) |

## QA Lenses

standards_root: ../project-jarvis/docs/dev-standards
- @std/PY_STANDARDS.md
- @std/SQL_STANDARDS.md
- @std/AIRFLOW_STANDARDS.md
- @std/API_CLIENT_STANDARDS.md
- docs/SIGNAL_GUIDE.md

<!-- Signal is Python + SQL + Airflow + EODHD-client heavy, so all four shared lenses apply.
     docs/SIGNAL_GUIDE.md is the repo-local lens: domain conventions no shared lens covers
     (data-plausibility STOP conditions, robust statistics, split-seam invariant, insert-mapping
     tests). It declares two QA_LENSES §3 overrides — both strengthenings, listed in its
     override table.
     All five resolve to existing files with `## Review Checklist` tails as of rollout.
     settings.json additionalDirectories carries the absolute form of standards_root — Claude
     Code does not resolve relative entries there. CHARTER carries relative; settings absolute. -->

## Cascade

**Builder cascade:** `../project-jarvis/docs/dev-standards/DEV_BASE.md` (shared base) +
`CLAUDE.md` (Signal's operating contract / author overlay).

Adding a builder = write an overlay + fill a copy of
`../project-jarvis/docs/dev-standards/CHARTER_TEMPLATE.md`. The base is inherited; don't restate it.
For standing up a new repo on this framework, follow `../project-jarvis/docs/dev-standards/ROLLOUT.md`.
