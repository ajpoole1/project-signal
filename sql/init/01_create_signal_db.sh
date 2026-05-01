#!/bin/bash
# Creates the signal database and applies the schema on first Postgres init.
# This script runs once — only when the data directory is empty (fresh volume).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE signal;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "signal" \
    -f /docker-entrypoint-initdb.d/02_signal_schema.sql
