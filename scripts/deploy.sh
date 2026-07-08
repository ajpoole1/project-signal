#!/usr/bin/env bash
# deploy-on-boot — runs at every instance start (nightly and on-demand workbench).
# The AWS box is off most of the day, so push-triggered deploys are impossible;
# deployment inverts to: update the repo checkout, apply safe migrations, restart.
# See the AWS migration plan §5. The platform start-hook (INFRA) invokes this with
# the checkout path as CWD and surfaces a nonzero exit to CloudWatch; this script
# owns WHAT deploy does, the hook owns WHERE and how failures are reported.
#
# Exit nonzero on any failure so a boot against broken main says so rather than
# starting the scheduler on a half-deployed tree.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

# --- 1. Sync the checkout to origin/main --------------------------------------
# reset --hard, not pull: the checkout is a deploy artifact, never edited in place,
# so a clean reset is the whole update. Capture the before/after SHAs to drive tier
# detection (what actually changed decides whether we rebuild or just restart).
log "fetching origin/main"
before="$(git rev-parse HEAD)"
git fetch --quiet origin main
git reset --hard --quiet origin/main
after="$(git rev-parse HEAD)"
log "checkout $before -> $after"

changed_files=""
if [ "$before" != "$after" ]; then
    changed_files="$(git diff --name-only "$before" "$after")"
fi

# --- 2. Apply safe migrations BEFORE the scheduler starts ---------------------
# Idempotent, non-destructive migrations only. A migration whose first line is the
# `-- MANUAL` marker is destructive (DELETE/TRUNCATE/DROP) and is applied by hand via
# the SSM tunnel — never auto-applied on boot. "Idempotent" is not "non-destructive":
# a boot-time loop must not re-run a purge every night. This is the mechanical guard
# for the manual-migration policy (§7.6) — the marker is grep-checkable; prose is not.
apply_migrations() {
    local applied=0 skipped=0
    for migration in $(ls sql/migrations/*.sql 2>/dev/null | sort); do
        if head -n 1 "$migration" | grep -q '^-- MANUAL'; then
            log "SKIP (manual): $(basename "$migration") — destructive, apply by hand via SSM"
            skipped=$((skipped + 1))
            continue
        fi
        log "applying $(basename "$migration")"
        psql "${SIGNAL_DATABASE_URL:?SIGNAL_DATABASE_URL must be set}" \
            --set ON_ERROR_STOP=1 --quiet --file "$migration"
        applied=$((applied + 1))
    done
    log "migrations: $applied applied, $skipped skipped (manual)"
}
apply_migrations

# --- 3. Tier detection: rebuild only when build inputs changed ----------------
# Code-only change -> restart the scheduler (picks up new DAGs/plugins from the
# mounted checkout). requirements/compose change -> full rebuild. On first boot
# (before == after impossible after reset, but empty diff) do a rebuild to be safe.
needs_rebuild=false
if [ -z "$changed_files" ]; then
    log "no file changes since last boot — restarting scheduler only"
elif echo "$changed_files" | grep -qE '(^requirements.*\.txt$|^docker-compose\.ya?ml$|^Dockerfile)'; then
    needs_rebuild=true
    log "build inputs changed -> full rebuild"
else
    log "code-only change -> scheduler restart"
fi

if [ "$needs_rebuild" = true ]; then
    docker compose up -d --build
else
    docker compose restart airflow-scheduler
fi

# --- 4. Smoke check -----------------------------------------------------------
# A green boot means: DAGs parse, the DB is reachable, containers are healthy.
# Any failure exits nonzero (surfaced by the start hook) rather than leaving a
# half-live stack that looks up but can't run the nightly chain.
log "smoke: DAG import errors"
import_errors="$(docker compose exec -T airflow-scheduler airflow dags list-import-errors 2>&1)"
if ! echo "$import_errors" | grep -qiE 'no data|No import errors'; then
    # `list-import-errors` prints a table of errors, or a "No data found" line when clean.
    if echo "$import_errors" | grep -q '/opt/airflow/dags'; then
        log "FAIL: DAG import errors present"
        echo "$import_errors"
        exit 1
    fi
fi
log "smoke: DAGs parse clean"

log "smoke: signal DB reachable"
docker compose exec -T airflow-scheduler \
    python -c "from airflow.providers.postgres.hooks.postgres import PostgresHook; PostgresHook(postgres_conn_id='signal_postgres').get_first('SELECT 1')"
log "smoke: DB reachable"

log "deploy OK ($after)"
