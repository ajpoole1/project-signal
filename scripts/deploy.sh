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

# --- 1b. Secrets-fetch shim (§5.1) --------------------------------------------
# Fetch the named secrets from Secrets Manager via the instance role and export
# them for THIS process only — they flow to the compose invocation via the
# environment and are never written to disk. INFRA's contract is only that the
# instance role can read these secret ARNs; the env-var names are Signal's.
# Skipped entirely when SECRETS_PREFIX is unset (local dev reads .env instead).
fetch_secret() {  # $1 = secret name under the prefix, $2 = env var to export
    local value
    value="$(aws secretsmanager get-secret-value \
        --secret-id "${SECRETS_PREFIX}/$1" \
        --query SecretString --output text 2>/dev/null)" || {
        log "FAIL: could not read secret ${SECRETS_PREFIX}/$1 (instance role missing grant?)"
        exit 1
    }
    export "$2=$value"
}

if [ -n "${SECRETS_PREFIX:-}" ]; then
    log "fetching secrets from ${SECRETS_PREFIX}/* (in-memory, never written to disk)"
    fetch_secret eodhd_api_key                 EODHD_API_KEY
    fetch_secret anthropic_api_key             ANTHROPIC_API_KEY
    fetch_secret airflow_fernet_key            AIRFLOW__CORE__FERNET_KEY
    fetch_secret airflow_webserver_secret_key  AIRFLOW__WEBSERVER__SECRET_KEY
    fetch_secret airflow_db_password           AIRFLOW_DB_PASSWORD
    fetch_secret signal_db_password            SIGNAL_DB_PASSWORD
    log "secrets loaded into environment"
else
    log "SECRETS_PREFIX unset — using .env (local dev)"
fi

# Ensure the airflow image is present before any container-based step below (the
# first-boot guard and migrations both run through it). Idempotent + cheap when
# already pulled; a fresh box has no image until now.
log "ensuring images present"
docker compose pull --quiet airflow-scheduler || docker compose build airflow-scheduler

# All DB access below runs INSIDE the airflow image (it has psycopg2), never via
# host psql — the box's user_data installs docker/compose/git/aws-cli but not psql,
# so a host-psql step would fail on the box. A single helper runs one-off python in
# a throwaway container with the two DB passwords passed through the environment.
db_python() {  # $1 = python snippet; DB creds come from the current env
    docker compose run --rm --no-deps -T \
        -e AIRFLOW_DB_PASSWORD="${AIRFLOW_DB_PASSWORD:-}" \
        -e SIGNAL_DB_PASSWORD="${SIGNAL_DB_PASSWORD:-}" \
        -e POSTGRES_HOST="${POSTGRES_HOST:-host.docker.internal}" \
        -e SIGNAL_DB_USER="${SIGNAL_DB_USER:-signal_app}" \
        airflow-scheduler python -c "$1"
}

# --- 1c. First-boot guard: the airflow metadata DB must exist (§5.1 runbook) ---
# Chicken-and-egg by design: boot #1 runs before INFRA's bootstrap-databases.sql
# has created the airflow/signal databases, so airflow-init would fail obscurely.
# Detect the missing-database case explicitly and say what to do, rather than
# emitting a generic connection error that reads like a real outage.
if [ -n "${POSTGRES_HOST:-}" ]; then
    log "first-boot guard: probing airflow_app@${POSTGRES_HOST}/airflow"
    if ! db_python "import psycopg2, os; psycopg2.connect(host=os.environ['POSTGRES_HOST'], dbname='airflow', user='airflow_app', password=os.environ['AIRFLOW_DB_PASSWORD']).close()" \
            >/dev/null 2>&1; then
        log "FAIL: airflow metadata DB not reachable as airflow_app@${POSTGRES_HOST}/airflow."
        log "      If this is the first boot, the databases do not exist yet —"
        log "      tunnel in and run bootstrap-databases.sql, then re-run deploy.sh (§5.1)."
        exit 1
    fi
    log "first-boot guard: airflow DB reachable"
fi

# --- 2. Apply safe migrations BEFORE the scheduler starts ---------------------
# Idempotent, non-destructive migrations only. A migration whose first line is the
# `-- MANUAL` marker is destructive (DELETE/TRUNCATE/DROP) and is applied by hand via
# the SSM tunnel — never auto-applied on boot. "Idempotent" is not "non-destructive":
# a boot-time loop must not re-run a purge every night. This is the mechanical guard
# for the manual-migration policy (§7.6) — the marker is grep-checkable; prose is not.
# Applied against the signal DB as signal_app (Q12), inside the airflow image via
# psycopg2 (already present for the postgres provider) — no host psql dependency.
apply_migrations() {
    local applied=0 skipped=0
    for migration in $(ls sql/migrations/*.sql 2>/dev/null | sort); do
        if head -n 1 "$migration" | grep -q '^-- MANUAL'; then
            log "SKIP (manual): $(basename "$migration") — destructive, apply by hand via SSM"
            skipped=$((skipped + 1))
            continue
        fi
        log "applying $(basename "$migration")"
        # Pipe the SQL file into a container-side psycopg2 runner — keeps DB access
        # inside the image (no host psql) and uses the signal_app identity.
        docker compose run --rm --no-deps -T \
            -e SIGNAL_DB_PASSWORD="${SIGNAL_DB_PASSWORD:?SIGNAL_DB_PASSWORD must be set}" \
            -e POSTGRES_HOST="${POSTGRES_HOST:-host.docker.internal}" \
            -e SIGNAL_DB_USER="${SIGNAL_DB_USER:-signal_app}" \
            -v "$(pwd)/$migration:/tmp/migration.sql:ro" \
            airflow-scheduler python -c "
import psycopg2, os
conn = psycopg2.connect(host=os.environ['POSTGRES_HOST'], dbname='signal',
                        user=os.environ['SIGNAL_DB_USER'], password=os.environ['SIGNAL_DB_PASSWORD'])
conn.autocommit = True
with conn.cursor() as cur, open('/tmp/migration.sql') as f:
    cur.execute(f.read())
conn.close()"
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
