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

# --- 1d. Schema bootstrap (B1): apply schema.sql if the signal DB has no tables --
# On a fresh box the bootstrap step creates the empty `signal` DATABASE, but the
# TABLES are schema.sql's job (run AS signal_app — it owns them, §2/§8). Without
# this, a cold box has an empty signal DB and every downstream task fails on a
# missing table. Detect the empty-schema case and apply once; on an already-built
# DB this is a no-op (schema.sql is CREATE TABLE IF NOT EXISTS throughout).
if [ -n "${POSTGRES_HOST:-}" ]; then
    # The count query must SUCCEED and return a number. A failed query (signal DB
    # unreachable, signal_app password blank/wrong) must NOT be read as "0 tables"
    # and trigger a bogus schema apply — §1c only proves the *airflow* DB reachable
    # as airflow_app, a different DB+role, so it does not cover this path. On query
    # failure we STOP: an empty result and a broken connection are different states.
    table_count="$(db_python "
import psycopg2, os
c = psycopg2.connect(host=os.environ['POSTGRES_HOST'], dbname='signal',
                     user=os.environ['SIGNAL_DB_USER'], password=os.environ['SIGNAL_DB_PASSWORD'])
cur = c.cursor(); cur.execute(\"SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'\")
print(cur.fetchone()[0]); c.close()" 2>/dev/null | tr -d '[:space:]')"

    if ! printf '%s' "$table_count" | grep -qE '^[0-9]+$'; then
        log "FAIL: could not read signal DB table count (got: '${table_count}')."
        log "      signal DB unreachable as signal_app, or SIGNAL_DB_PASSWORD blank/wrong."
        log "      Not assuming an empty DB — fix the signal connection and re-run."
        exit 1
    fi

    if [ "$table_count" = "0" ]; then
        log "schema bootstrap: signal DB has 0 tables — applying schema.sql as signal_app"
        docker compose run --rm --no-deps -T \
            -e SIGNAL_DB_PASSWORD="${SIGNAL_DB_PASSWORD:?SIGNAL_DB_PASSWORD must be set}" \
            -e POSTGRES_HOST="${POSTGRES_HOST}" \
            -e SIGNAL_DB_USER="${SIGNAL_DB_USER:-signal_app}" \
            -v "$(pwd)/sql/schema.sql:/tmp/schema.sql:ro" \
            airflow-scheduler python -c "
import psycopg2, os
conn = psycopg2.connect(host=os.environ['POSTGRES_HOST'], dbname='signal',
                        user=os.environ['SIGNAL_DB_USER'], password=os.environ['SIGNAL_DB_PASSWORD'])
conn.autocommit = True
with conn.cursor() as cur, open('/tmp/schema.sql') as f:
    cur.execute(f.read())
conn.close()"
        log "schema bootstrap: schema.sql applied"
    else
        log "schema bootstrap: signal DB has $table_count tables — skip (already built)"
    fi
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

# --- 3. Bring the stack to the desired state ----------------------------------
# Three cases, in priority order:
#   (B2) stack not running  -> `up -d`, unconditionally. This is the cold-boot case
#        that blocked inauguration: a no-change boot with the stack down would
#        otherwise hit `restart`, which FAILS when there's nothing to restart. Boot
#        state, not the git diff, decides whether we must start the stack.
#   build inputs changed    -> `up -d --build` (rebuild the image).
#   code-only change on a    -> `restart airflow-scheduler` (mounted checkout already
#        running stack           has the new DAGs/plugins; just reload).
# Count running services for this project; 0 means the stack is down.
running="$(docker compose ps --status running --quiet 2>/dev/null | grep -c . || true)"

if [ "${running:-0}" = "0" ]; then
    log "stack not running ($running services up) -> up -d"
    docker compose up -d
elif echo "$changed_files" | grep -qE '(^requirements.*\.txt$|^docker-compose\.ya?ml$|^Dockerfile)'; then
    log "build inputs changed -> up -d --build"
    docker compose up -d --build
elif [ -z "$changed_files" ]; then
    log "no file changes, stack already up -> nothing to do"
else
    log "code-only change -> restart scheduler"
    docker compose restart airflow-scheduler
fi

# --- 4. Smoke check -----------------------------------------------------------
# A green boot means: DAGs parse, the DB is reachable, containers are healthy.
# Any failure exits nonzero (surfaced by the start hook) rather than leaving a
# half-live stack that looks up but can't run the nightly chain.

# (B3) Assert the secrets are actually non-blank INSIDE the running container.
# The "declared-done != verified-live" defect class: a secret can exist in Secrets
# Manager as an empty container, or the shim can export a blank, and everything
# looks fine until a task authenticates with "" and fails at 2am. Check the live
# container, not this script's env — the container is what runs the pipeline.
#
# Each secret is checked WHERE IT ACTUALLY LANDS (verified against compose, not
# assumed): four ride the container env directly; AIRFLOW_DB_PASSWORD is NOT a
# standalone env var — it is interpolated into AIRFLOW__DATABASE__SQL_ALCHEMY_CONN
# at render time, so we assert that conn string carries a non-empty password rather
# than looking for an env var that was never passed (which would false-fail).
log "smoke: secrets non-blank in-container"
missing_secrets="$(docker compose exec -T airflow-scheduler python -c "
import os, re
bad = []
# Direct container-env secrets:
for k in ['EODHD_API_KEY', 'ANTHROPIC_API_KEY', 'AIRFLOW__CORE__FERNET_KEY',
          'AIRFLOW__WEBSERVER__SECRET_KEY', 'SIGNAL_DB_PASSWORD']:
    if not os.environ.get(k, '').strip():
        bad.append(k)
# airflow_app password lives inside the metadata conn string: user:PASSWORD@host.
conn = os.environ.get('AIRFLOW__DATABASE__SQL_ALCHEMY_CONN', '')
m = re.search(r'://[^:]+:([^@]*)@', conn)
if not conn or m is None or not m.group(1).strip():
    bad.append('AIRFLOW_DB_PASSWORD(in SQL_ALCHEMY_CONN)')
print(','.join(bad))" 2>/dev/null | tr -d '[:space:]')"
if [ -n "$missing_secrets" ]; then
    log "FAIL: secret(s) blank/absent inside the container: $missing_secrets"
    log "      Secrets Manager container empty, or the shim exported a blank value."
    exit 1
fi
log "smoke: all required secrets present in-container"

log "smoke: DAG import errors"
# `list-import-errors` prints a table listing /opt/airflow/dags/... paths on error,
# or a "No data found" line when clean. Assert the clean sentinel POSITIVELY:
# the old double-negative (not-clean AND has-dag-path) fell through to "parse clean"
# on any garbled/contaminated output that matched neither branch — a check that
# doesn't gate. Now: an error path present -> FAIL; the clean sentinel absent -> also
# FAIL (we could not confirm clean, so we do not proceed).
import_errors="$(docker compose exec -T airflow-scheduler airflow dags list-import-errors 2>&1)"
if echo "$import_errors" | grep -q '/opt/airflow/dags'; then
    log "FAIL: DAG import errors present"
    echo "$import_errors"
    exit 1
fi
if ! echo "$import_errors" | grep -qiE 'no data|no import errors'; then
    log "FAIL: could not confirm DAGs parse clean (unexpected list-import-errors output):"
    echo "$import_errors"
    exit 1
fi
log "smoke: DAGs parse clean"

log "smoke: signal DB reachable"
# Capture stdout ALONE and assert the expected token, rather than trusting the
# exit status to propagate through the `docker compose exec` boundary. Two ways a
# broken DB slips past a bare `exec … python -c` under set -e: (a) compose/image
# noise (pull, pip, warnings) rides stdout and the exec still returns 0, so a
# contaminated run looks healthy; (b) a `$(… | tr)` pipe reports the LAST element's
# status (tr always succeeds), masking the inner failure even under pipefail.
# So: send diagnostics to stderr (2>/dev/null), keep only the sentinel on stdout,
# and gate the exit on the sentinel value — the same token-assert the table_count
# guard already uses. A failure here is a hard stop: never "deploy OK" on an
# unreachable DB.
db_ping="$(docker compose exec -T airflow-scheduler python -c "
from airflow.providers.postgres.hooks.postgres import PostgresHook
print(PostgresHook(postgres_conn_id='signal_postgres').get_first('SELECT 1')[0])" \
    2>/dev/null | tr -d '[:space:]')"
if [ "$db_ping" != "1" ]; then
    log "FAIL: signal DB smoke check did not return 1 (got: '${db_ping}')."
    log "      DB unreachable via signal_postgres, or output was contaminated —"
    log "      either way the nightly chain cannot run. Not reporting deploy OK."
    exit 1
fi
log "smoke: DB reachable"

log "deploy OK ($after)"
