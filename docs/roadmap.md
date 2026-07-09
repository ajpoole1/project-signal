# Roadmap — Project Signal

Current-status view as of 2026-06-10. For phase specs see `docs/signal_v2_rebuild.md`.

---

## v1 pipeline — phase status

All v1 phases are complete and running in production.

| Phase | Status | Deliverable |
|---|---|---|
| Phase 1 — Foundation | ✅ Complete | `sql/schema.sql`, `config/config.py`, `config/watchlist.py`, CI/CD |
| Phase 2 — Ingest DAG | ✅ Complete | `dag_stock_ingest` running nightly; `raw_prices` + `ticker_metadata` populating |
| Phase 3 — Indicators DAG | ✅ Complete | `dag_stock_indicators` running nightly; `stock_signals` populating; 5-year backfill complete |
| Phase 4 — Relatedness DAG | ✅ Complete | `dag_stock_relatedness` running Sundays; `relatedness_matrix` + `sector_beta` populating |
| Phase 5 — LLM Analysis DAG | ✅ Complete | `dag_llm_analysis` running nightly; `llm_analysis` + `daily_brief` populating |
| Phase 6 — Prediction Tracking | ✅ Complete | `dag_outcome_tracker` running nightly; `signal_predictions` + `signal_accuracy` populating; `backfill_predictions.py` run |
| Phase 3.5 — Regime-conditional weights | ✅ Complete | `SIGNAL_WEIGHTS` keyed by `vix_regime`; `parameter_overrides.json` supports dotted keys; `SIGNAL_VERSION = "v1.1"` |

**Note on the v1 LLM layer:** `dag_llm_analysis` is algorithmic — it does not call the LLM per ticker. The Sonnet model is called once per run to generate `daily_brief`. The per-ticker bias/confidence/key_levels/reasoning are computed from `stock_signals` and `raw_prices` via `dag_components/llm/calculations.py`.

---

## Signal v2 rebuild — phase status

Phases 0–3 complete. Phases 4–6 not started.

| Phase | Status | Deliverable |
|---|---|---|
| Phase 0 — Diagnostics | ✅ Complete | `docs/signal_v2_phase0_findings.md`; three data-quality defects identified and remediated |
| Phase 1 — Measurement rework | ✅ Complete | `prediction_outcomes` populated full-history (5.3M rows, 6 horizons); v2 resolver live in `dag_outcome_tracker` |
| Phase 2 — Feature layer | ✅ Complete | `signal_features` populated full-history (451k rows, 6,932 tickers); 11 continuous features |
| Phase 3 — Regime classification | ✅ Complete | Regime columns folded into Phase 2 migration (007) and backfill; `docs/signal_v2_phase3_findings.md` |
| Phase 4 — Cross-sectional rank model | 🔲 Not started | `model_coefficients_v2.json`, walk-forward top/bottom-decile spread report, runtime scoring; single logistic model per horizon on state features |
| Phase 5 — Calibrated confidence | 🔲 Not started | `confidence_calibration_v2.json`; monotonic OOS accuracy across rank deciles × trend_direction × vix_regime |
| Phase 6 — Parallel run & cutover | 🔲 Not started | Comparison view `v_signal_version_compare`; cutover criteria: v2 episode-head accuracy ≥ v1 + 3pts at 42d on 60+ live trading days |

---

## Migration log

| # | File | Applied | Purpose |
|---|---|---|---|
| 001 | `001_add_currency_exchange_vix_source.sql` | — | Add `currency`, `exchange` to `raw_prices`; `vix_source` to `stock_signals` |
| 002 | `002_add_daily_brief.sql` | — | Create `daily_brief` table |
| 003 | `003_phase6_prediction_tracking.sql` | — | Create `signal_predictions` (PK: ticker, signal_date), `signal_accuracy`, `parameter_proposals` |
| 004a | `004_gics_fields.sql` | — | Add GICS sector/industry fields to `ticker_metadata` |
| 004b | `004_regime_conditional_tuning.sql` | archived | Phase 3.5 PK-widening + `regime_weight_set`. Archived unmerged on `archive/phase-3-5-regime-weights`; PK-widening re-issued as migration 010 (v2). |
| 005 | `005_v2_prediction_outcomes.sql` | — | Create `prediction_outcomes` (Phase 1 long-format) |
| 006 | `006_remediation_cleanup.sql` | 2026-06-09 | Delete junk tickers (WLDSW, ZWZZT, ZVZZT, ZXZZT); TRUNCATE `prediction_outcomes` pre-backfill |
| 007 | `007_signal_features.sql` | 2026-06-10 | Create `signal_features` (Phase 2+3); 451k rows backfilled |
| 008 | `008_fix_sector_beta_bug.sql` | 2026-06-10 | TRUNCATE `sector_beta` after column-order bug corrupted beta values; relatedness re-run |
| 009 | `009_purge_junk_tickers.sql` | 2026-06-11 | Purge 173 warrant/test-symbol tickers (rule-based) from all tables |
| 010 | `010_signal_predictions_version_pk.sql` | 2026-06-11 | Widen `signal_predictions` PK to `(ticker, signal_date, signal_version)` for v2 parallel run (extracted from retired 004b) |

---

## Decision log

**2026-06-09 — Threshold redesign: GO.**
Phase 0.1 confirmed fixed ±1% threshold was the dominant cause of sub-45% accuracy. Dead zone (|move| ≤ 1%) absorbed 9–23% of outcomes at 5–20d horizons, mechanically capping accuracy regardless of signal quality. Volatility-scaled, horizon-scaled thresholds adopted in Phase 1.

**2026-06-09 — v2 is long-biased.**
Phase 0.4 showed RSI oversold is the strongest standalone condition (+1.6 pts edge). All three overextension conditions (RSI > 70, far above SMA200, above BB upper) sit exactly on the base rate — overextension does not predict decline at 10d. Bearish channel reframed as "extended / reduce / avoid" flag on held names, not a directional forecast, unless Phase 3 confirms the falling-knife niche.

**2026-06-09 — Two-channel design deferred pending Phase 3.**
Phase 4's "ranging = reversion / trending = continuation" two-channel design was contingent on Phase 3 confirming that deep-below-SMA200 names mean-revert in ranging regimes. Phase 3 disproved this — ranging deep-below-SMA200 names show negative median excess return (−1.88% at 21d), worse than the base rate (−1.31%). Channel architecture to be revisited after the event study.

**2026-06-10 — Falling-knife hypothesis confirmed.**
Phase 3 confirmed trending-down × deep-below-SMA200 is the worst subgroup: median excess −3.01% at 21d, 41.9% frac(excess > 0). This is the only bearish hypothesis with empirical support. Bearish output from Phase 4 limited to this niche pending the event study.

**2026-06-10 — sector_beta column-swap bug.**
`_INSERT_BETAS` in `dag_components/relatedness/tasks.py` had column order `(ticker, etf_proxy, beta, window_days)` but `beta_values()` returns `(ticker, etf_proxy, window_days, beta)`. Beta floats were truncated into the INTEGER column; `ON CONFLICT` collapsed the table to 2 rows. All `prediction_outcomes` computed with beta=1.0 fallback. Fix: migration 008 (TRUNCATE), column order corrected in tasks.py, relatedness re-triggered, `backfill_outcomes_v2.py` re-run. Phase 3 conclusions marked preliminary pending beta recalculation.

**2026-06-10 — Phase 3 metric correction.**
`analyze_regimes.py` initially reported `direction_correct` (56–58%) as the signal strength metric for deep-below-SMA200 names. This was incorrect — `direction_correct` measures whether the v1 prediction bias matched the excess return direction, not whether the name outperformed its benchmark. Correct metric is `frac(excess_return > 0)` at 43.5–46.6% depending on regime, all below the 44.4% base rate. Corrected in `docs/signal_v2_phase3_findings.md`.

**2026-06-11 — Events retired as signal class; Phase 4 = cross-sectional state-feature rank model.**
Event study (`scripts/analyze_events.py`, 7M eligible ticker-dates, 11 event types E1–E11) showed all bullish event edges dissolve when compared against slope-conditioned base rates. Slope>0 base @42d = −1.10% vs overall −2.19%; every bullish event conditioned on slope>0 falls within ±0.5pp of its slope-conditioned base. E7 (high_52w_breakout) incremental edge = +0.11pp above slope>0 base. Continuous `dist_52w_high` state (d9 within 2% = +0.14%) beats the binary breakout event (d10 at/above = −0.03%). Confluence fails monotonicity after correcting computation to run over full universe. Volume confirmation inverted across all event types. Conclusion: the edge lives in states, not crossings. Two-channel architecture retired. Phase 4 redesigned as a single logistic model per horizon on continuous state features from `signal_features`. Events not included as features. See `docs/signal_v2_event_study.md` for full tables.

**2026-06-11 — Phase 3.5 regime-conditional weights archived unmerged.**
Archived on branch `archive/phase-3-5-regime-weights` — premise retired by Phase 0/Phase 3 findings (v1 composite carries ~zero directional information; regimes modulate degree not direction). v1 frozen at v1.0 as the parallel-run baseline. The `signal_version` PK widening it introduced (needed by v2 for v1/v2 row coexistence) was extracted and re-issued as migration `010`, attributed to the v2 spec; the `regime_weight_set` column was dropped.

**2026-07-08 — Relatedness trimmed for the AWS move; peer lookup flagged wire-or-retire.**
AWS migration §7.3. `CORRELATION_WINDOWS` dropped the 30d window (kept 90d + 365d) and `RELATEDNESS_MIN_R` raised 0.20 → 0.50, cutting ~20M weekly rows ahead of the shared-RDS move (migration `011`, MANUAL-marked — contains DELETEs). **Finding F:** `relatedness_matrix` has **no wired production reader** today — the LLM peer-lookup consumer is designed (`LLM_PEER_CORRELATION_MIN_R`, `LLM_PEER_COUNT` in config) but **zero Python references either constant**. The trim is therefore behavior-neutral, but that is a statement about future code, not a live-verified invariant.
> **⏳ Open decision — wire or retire the peer lookup (owner: Phase 4 feature selection).** Either the Phase 4 cross-sectional model consumes `relatedness_matrix` (peer / sector-relative features), justifying the DAG's nightly cost — or the DAG and table are retired deliberately. A table with no reader **and no decision date** is how the next Finding F happens; this is the decision date.

**2026-07-08 — Wind-down mechanism recorded for PR 3 (do not build stale).**
AWS migration §5/§7.5. When the run-summary + wind-down work lands (PR 3), the orchestrator wind-down task must **asynchronously invoke the `workbench-stop` Lambda and exit** — it must never stop RDS/EC2 directly, because stopping RDS from inside Airflow kills the scheduler's own metadata DB mid-task. Instance role gets `lambda:InvokeFunction` on workbench-stop, not raw `rds:Stop`. Ownership: Signal self-checks for other running DAGs while it is the sole tenant; a platform-global wind-down DAG replaces it at ≥2 tenants (plan open-Q10). Recorded now so PR 3 is designed to the resolved mechanism.

**2026-07-08 — Run-summary schema v1 signed off; Q11 closed. PR 3 contract.**
AWS migration §6.3/§7.5. Jarvis drafted the schema (`project-jarvis/skills/signal/run_summary_schema_v1.json`), Signal reviewed for producibility, both signed off. **This is the contract PR 3's run-summary writer builds against** — the orchestrator's terminal `all_done` task writes JSON to `s3://…/signal/run-summaries/<run_date>.json`. Agreed producer decisions:
> - **`rows_written`**: `COUNT(*) WHERE date = <run_date>` per table at summary time (PK-indexed, ~5 cheap counts) — NOT cross-DAG XCom. Semantic: **rows *present* for run_date**, not write-delta (idempotent re-run reflects final state — what Jarvis's delta-detection wants). Confirmed with Jarvis.
> - **`quarantine_count`**: add a quarantine counter to `resolve_outcomes_v2`'s return ("quarantined this run"), not a diff-against-prior-set.
> - **`deploy` block**: `deploy.sh` writes `deploy_status.json` (exit_status, tier, commit_hash, timestamp_utc, detail) to the checkout root; summary task reads it. **PR #18's deploy.sh does NOT write it yet** — v1 emits `deploy: null` (key present, null value satisfies `required`); add the ~3-line writer in PR 3 → v1.1-required. Map deploy.sh tiers onto the enum (`migration-applied` when a non-MANUAL migration ran, `unknown` fallback).
> - **`wind_down`**: producer-facts only `{attempted, result}`; `result ∈ {invoked, skipped_other_dags_running, invoke_failed}`. `skipped_other_dags_running` = metadata-DB query for running DagRuns of other tenants' dag_ids (the §5 self-check). completed/backstop are Jarvis-side inferences, not fields.
> - **`dags[]`** (logical_date, state, wall_clock, failed_tasks[].log_excerpt ≤20 lines): from the Airflow metadata DB via `@provide_session` in the summary task. logical_date guaranteed (orchestrator triggers with explicit execution_date).
> - **`data_gates`**: absent in v1; shape `{check: {passed, detail}}` confirmed for when the hardening pass makes the SIGNAL_GUIDE STOP-conditions programmatic.
> - Timestamps ISO-8601 UTC; S3 key date == `meta.run_date` (America/Toronto logical date). Bump `schema_version` to 2 for any field-shape change — never mutate v1 (Jarvis gates on `== 1`).

**2026-07-08 — Q12 closed: two DB roles, one per database. First-boot-prerequisite PR.**
AWS migration §2/§5.1/Q12. [INFRA] added an `airflow_app` role owning the `airflow` metadata DB + a 5th secret `airflow_db_password`; Signal owns the conn-string mapping. **Signal's ground-truth check found the post-§7.4 compose unsatisfiable as-is** — both conn strings shared one credential pair (`POSTGRES_USER`/`POSTGRES_PASSWORD`), so `signal_app` would have owned Airflow's metadata DB (Q12's exact failure mode). Fix (dedicated PR, pulled forward as a first-boot prerequisite — airflow-init can't authenticate without it):
> - **Two-credential split, roles + DBs hardcoded** in `docker-compose.yml`: `SQL_ALCHEMY_CONN = airflow_app:${AIRFLOW_DB_PASSWORD}@host/airflow`, `AIRFLOW_CONN_SIGNAL_POSTGRES = signal_app:${SIGNAL_DB_PASSWORD}@host/signal`. Role and DB names are structural constants (fixed by bootstrap-databases.sql), not secrets — hardcoding both collapse-proofs the strings. Only the two passwords are injected.
> - **`scripts/_db.py`** (scripts → signal DB only) now reads `SIGNAL_DB_USER` (default `signal_app`) + `SIGNAL_DB_PASSWORD`, not generic `POSTGRES_USER`/`POSTGRES_PASSWORD`.
> - **Dead vars removed**: `POSTGRES_USER`/`POSTGRES_DB` deleted from compose + `.env.example` (also dropped the stale `POLYGON_API_KEY` from `.env.example`, a PR#18 residue).
> - **`deploy.sh` secrets-fetch shim (§5.1)**: fetches the 6 named secrets via `aws secretsmanager get-secret-value` (instance role), exports for the compose invocation only, never to disk. Gated on `SECRETS_PREFIX` (local dev uses `.env`). Plus the first-boot guard: detect airflow-DB-absent and emit the specific "run bootstrap first" message rather than a generic error.
> - Env-var names are Signal's (both shim and compose are Signal-owned); INFRA's only contract is the secret names.
> - Not touched (separate cleanup): `sql/init/01_create_signal_db.sh` is dead (old container-Postgres model, unreferenced by compose) but out of scope for this PR.
> - **Local-dev note:** Tier-2 local Docker is retired (§7 dev lifecycle), so hardcoding `airflow_app`/`signal_app` in compose is not a local regression — nobody runs the airflow container locally; local dev is Tier-1 venv + Tier-3 on AWS. `_db.py`'s `SIGNAL_DB_USER` override covers a differently-named local role.

---

## Next actions

1. Phase 4: `scripts/train_signal_v2.py` — single LogisticRegression per horizon on state features; walk-forward expanding window; export `config/model_coefficients_v2.json`; verify `vol_ratio` coefficient is negative
2. Phase 5: `scripts/calibrate_confidence_v2.py` — bucket calibration from walk-forward OOS predictions; buckets = (rank_decile, trend_direction, vix_regime); export `config/confidence_calibration_v2.json`
3. Phase 6: parallel run comparison view; cutover decision (≥60 live trading days)
