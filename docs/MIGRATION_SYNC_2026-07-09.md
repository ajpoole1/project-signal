# [SIGNAL] → Planning agent — migration sync (2026-07-09 EOD)

Cross-agent sync of where [SIGNAL] reached, every variation from the plan of record
(v1.2.7) and how it was handled, and the current blocking state. For syncing all
three agents before the next inauguration attempt.

---

## 1. Headline

- **All [SIGNAL] pre-migration work (§7) is merged to `main`.** PRs #16–20.
- **Inauguration day started; reached §8 Step 1a (schema apply) — GATE PASS.**
- **STOPPED at §8 Step 1b (EODHD backfill), BLOCKED on an ownership gap:** nobody
  brought the airflow container stack up on the box. Not a code problem — a
  bringup-ownership gap between [SIGNAL] and [INFRA] (detail V2). Paused for the
  night by operator decision; resumes tomorrow.

---

## 2. Merged work (all on `main`; deploy-on-boot will pull it)

| PR | Scope | Plan ref |
|---|---|---|
| #16 | Indicators batch-streaming (kill universe-sized XCom; peak RSS 224 MiB) | §7.1 |
| #17 | Env-driven DB host + shared `scripts/_db.py` helper; relatedness stop-safety; relatedness trim | §7.2/7.3/7.4 |
| #18 | deploy.sh (deploy-on-boot) + `-- MANUAL` migration guard; DagBag CI job; cron→2:05am ET tz-aware; `one_failed` alert; tools cap 8g→4g; jarvis Airflow role; polygon/yfinance deletion | §7.5-partial/7.6/7.7/7.9 |
| #19/#20 | Q12 two-DB-role split (airflow_app/signal_app) + secrets-fetch shim + first-boot guard | Q12 / §5.1 |

**Still [SIGNAL]-owned, NOT yet built (correctly deferred, not dropped):**
- **PR 3** — wind-down task (async workbench-stop invoke) + run-summary writer. Parked
  on live S3 prefix + boto3. Contract frozen (Q11 signed; recorded in roadmap decision log).
- **§7.8 sizing report** — per-table sizes + wall-clocks, produced ON inauguration day (this run).
- **Dev-standards load** (§9 step 1) — operator task, still pending, independent.

---

## 3. Inauguration progress (§8)

| Step | State |
|---|---|
| **1a — schema.sql as signal_app** | ✅ **DONE, GATE PASS.** 12/12 tables + 32 indexes present. Applied over the SSM DB tunnel (localhost:5433 → RDS) as signal_app. Idempotent — safe to re-run. |
| **1b — backfill_eodhd (5yr, full universe)** | ⏸️ **BLOCKED** (container not up, V2). Command ready: `docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_eodhd.py`. |
| 2–8 | Not started. |

**As of Step 1a completion:** signal DB = 12 tables, 32 indexes, 0 data rows. (Live
re-verify at EOD skipped — tunnel closed as operator wrapped up. State above is the
verified reading from when Step 1a's gate passed.)

---

## 4. Variations from the plan + how each was handled

Places reality diverged from the plan text. Each resolved in-session; flagged here so
all agents and the plan converge.

### V1 — Access model was DB-tunnel, not shell-on-box (resolved; clarify in plan)
Plan §6/§9 implies [SIGNAL] drives inauguration commands "on the box via SSM." Actual
operator setup: an SSM **port-forward of RDS to localhost:5433**, with [SIGNAL]'s shell as
a *peer*, not on the box. So [SIGNAL] has **direct SQL access to RDS**, not a box shell.
- **Handled:** Step 1a (pure SQL) ran over the tunnel via psycopg2 as signal_app, password
  fetched from Secrets Manager in-process. Step 1b (backfill) CANNOT run this way — it needs
  box-side secrets + outbound EODHD HTTPS, so it must run in the container on the box.
- **Working division:** inauguration SQL steps = [SIGNAL] over tunnel; script steps = on-box.
  Worth encoding in §8/§9.

### V2 — Container bringup ownership gap (THE BLOCKER; needs a plan fix)
**Sync-critical.** §5.1 says deploy.sh is [SIGNAL]-owned and "the platform start-hook invokes
it"; §9 step-4/5 puts box bringup under [INFRA]. On inauguration night the airflow stack was
**never started** — [INFRA] assumed [SIGNAL] would run deploy.sh; [SIGNAL] assumed [INFRA]'s
start-hook fires it. Neither did. DB-side proof: only the [SIGNAL] tunnel session on `signal`,
**zero airflow connections** on either DB.
- **Handled tonight:** surfaced it; did NOT paper over. Operator working it with [INFRA].
- **Open diagnostic for tomorrow:** did deploy.sh run-and-fail (check `docker compose logs`;
  if the first-boot guard printed "airflow DB absent — run bootstrap first", bootstrap didn't
  take) — or was the start-hook simply never wired to fire on this boot?
- **Plan fix needed:** name the single owner + trigger for "first deploy.sh invocation on
  inauguration boot." New register item.

### V3 — Latent PR#18 bug found & fixed during Q12 (already merged, no action)
PR#18's deploy.sh migration-runner referenced `${SIGNAL_DATABASE_URL:?...}` — a variable
**never set anywhere**; migrations would have hard-failed on first real boot. The Q12
credential rework forced touching that path and caught it. Fixed in #19: DB access (first-boot
guard + migrations) now runs INSIDE the airflow image via psycopg2 (the box installs
docker/compose/git/aws-cli but **not host psql**, so host-psql steps would fail).

### V4 — §8 gates are manual SQL, not script-enforced (operational note)
`backfill_eodhd.py` prints per-ticker progress + failed list + total rows, but does NOT assert
the §8 Step-1 gates (per-exchange counts, zero sub-$0 closes, delisting review). [SIGNAL]
drives those as SQL over the tunnel after the backfill reports Done. Not a defect — clarifying
the gates live with the reviewer, not the script.

### V5 — Dead scaffolding flagged, deliberately not deleted (register candidate)
`sql/init/01_create_signal_db.sh` (old container-Postgres model) is unreferenced by compose and
dead, but still carries `POSTGRES_USER`/`POSTGRES_DB` refs and points at a nonexistent
`02_signal_schema.sql`. Left out of the focused Q12 PR to avoid scope-creep; also stale in
`docs/architecture.md:260`. Needs a deliberate cleanup PR or explicit retire.

---

## 5. Secrets

- `consulting-platform/dev/eodhd_api_key` — ✅ loaded + round-trip verified (AWSCURRENT) by
  [SIGNAL] from `.env`, in-process (never in chat/logs).
- Other five ([INFRA]-provisioned): signal_db_password confirmed readable + working (Step 1a
  authenticated with it). anthropic / fernet / webserver_secret / airflow_db_password assumed
  present per [INFRA] "done" — not independently verified by [SIGNAL].

---

## 6. Resume path for tomorrow (fastest re-sync)

1. **[INFRA]/operator:** resolve V2 — get the airflow stack up. Confirm `docker compose ps`
   (want scheduler + webserver `Up`).
2. **Operator → [SIGNAL]:** paste `docker compose ps` (+ `logs --tail=50 airflow-scheduler`
   if not Up). [SIGNAL] reads the diagnosis from there.
3. **Fire Step 1b on the box:** `docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_eodhd.py`.
   Idempotent upserts — a 5am kill is safe; re-run resumes where it left off (never redoes the
   full 5yr from a partial). Operator plan: fire at start-of-work, let it run, kill if done /
   accept wait to 5am otherwise.
4. **[SIGNAL] drives §8 gates over the tunnel** on Done: per-exchange counts;
   `SELECT COUNT(*) FROM raw_prices WHERE close <= 0` must be 0 (STOP if not); failed/delisting review.

---

## 7. Asks of the planning agent

1. **Encode V2** — register/plan item: single owner + trigger for the first deploy.sh
   invocation on inauguration boot (the gap that blocked tonight).
2. **Encode V1** — clarify §8/§9: inauguration SQL steps run [SIGNAL]-over-tunnel; script
   steps run on-box. Different seats.
3. **V5** — add `sql/init/` dead-scaffolding cleanup to the deferred register (D-series).
4. Confirm PR 3's S3 prefix + boto3 timing so [SIGNAL] can unblock the last §7 item.

---

_Note: this file is the migration sync. `docs/RECOVERY.md` is a SEPARATE, older
(2026-06) WSL-crash/data-gap recovery runbook — not related; do not conflate._
