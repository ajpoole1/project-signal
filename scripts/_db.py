"""Shared DB connection helper for standalone scripts.

The single place a script builds its psycopg2 connection, so the host is
env-driven in exactly one spot and cannot drift back to a hardcoded
`host.docker.internal` per script (§7.4 of the AWS migration plan).

SIGNAL_GUIDE: DAG tasks connect via the `signal_postgres` Airflow hook; standalone
`scripts/` connect via psycopg2 with `dbname="signal"` and `.env` credentials. This
module is the scripts-side implementation of that rule.

Host resolution: `POSTGRES_HOST` from the environment, defaulting to `localhost` —
correct for WSL against native Postgres in local dev, and always overridden by env
on the AWS box (injected from Secrets Manager / instance env). The default is never
`host.docker.internal`: that is the in-container value, meaningless from a script,
and is being purged repo-wide.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[1]

# Signal's database name is fixed; only the host varies by environment.
SIGNAL_DB = "signal"


def load_env(env_path: Path | None = None) -> None:
    """Load KEY=VALUE lines from `.env` into os.environ (setdefault — never clobbers).

    Defaults to the repo-root `.env`. A missing file is a no-op: on the AWS box
    there is no `.env` and the environment is already populated from Secrets Manager.
    """
    path = env_path if env_path is not None else REPO_ROOT / ".env"
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def get_connection():
    """Return a psycopg2 connection to the signal database.

    Host from `POSTGRES_HOST` (default `localhost`); user/password required from the
    environment — a missing credential raises immediately rather than connecting as
    the wrong identity.
    """
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        dbname=SIGNAL_DB,
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
