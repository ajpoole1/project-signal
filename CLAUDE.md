# Project Signal — Claude Code Memory

Nightly stock intelligence pipeline. Stack: Airflow + Postgres + Polygon.io + Anthropic API.
Portfolio-grade open source project. Logic is public; API keys and personal watchlists are always local-only.

---

## Design Principles — These govern every decision

- **Config-driven tier upgrades** — Changing `POLYGON_TIER` in `config.py` is the only change needed to move from free to paid. No DAG rewrites, no hardcoded limits elsewhere.
- **Idempotent writes** — Every DB write uses `INSERT ... ON CONFLICT DO UPDATE`. Tasks are safe to re-run.
- **Separation of concerns** — Four independent DAGs: ingest → indicators → relatedness → LLM. Each can fail and retry without corrupting the others.
- **Rate limit compliance** — All Polygon calls go through `polygon_client.py`. No `sleep()` elsewhere. The `@rate_limited_call` decorator is the single throttling point.
- **Security by design** — Secrets via `.env` only. No hardcoded credentials. No personal data in logs.

---

## Architecture

### DAG Pipeline

| DAG | Schedule | Purpose |
|---|---|---|
| `dag_stock_ingest` | 9 PM ET weekdays | Fetch OHLCV + VIX/VVIX from Polygon |
| `dag_stock_indicators` | 10 PM ET weekdays | SMA, MACD, RSI, Bollinger, VIX regime, composite score |
| `dag_stock_relatedness` | 11 PM ET Sundays | Pearson correlation matrix, sector beta, peer clusters |
| `dag_llm_analysis` | 11 PM ET weekdays | Claude API: per-ticker signal + Jarvis briefing |

### Key Design Decisions — Do not revisit without good reason

- **Postgres is the shared layer.** All four DAGs read/write the same schema. No per-DAG side databases.
- **`plugins/` is the shared library.** DAG files import from plugins; DAG files contain no business logic.
- **All config in `config/config.py`.** Signal weights, VIX thresholds, correlation windows, model names — nothing hardcoded in DAG or plugin files.
- **Watchlist in `config/watchlist.py`.** Not in DAG files, not in config.py. Extending the watchlist never requires touching a DAG.
- **Airflow TaskFlow API only.** Use `@task` decorator for all Python tasks. No classic operators for Python logic.

---

## File Conventions

```
project-signal/
├── dags/              # Airflow DAG definitions — orchestration only, no logic
├── plugins/           # Shared library: polygon_client, indicators, vix_classifier, relatedness, llm_client
├── config/
│   ├── config.py      # All tunable values: tiers, weights, thresholds, model names
│   └── watchlist.py   # Ticker list + ETF proxies
├── sql/
│   └── schema.sql     # All CREATE TABLE + indexes — run once against Postgres
├── scripts/           # Manual utility scripts (not DAGs)
└── tests/             # One test file per plugin module
```

**Every plugin module must have a corresponding test file.**

---

## Database Rules

- All tables defined in `sql/schema.sql`. Never create tables in Python code.
- All writes: `INSERT ... ON CONFLICT (ticker, date) DO UPDATE SET ...`
- Never `TRUNCATE` or `DELETE` in DAG tasks. Soft deletes only (update `computed_at`, let queries filter by recency).
- All DB connections via Airflow Postgres hook. No hardcoded connection strings.
- Primary keys: `(ticker, date)` on all time-series tables.

---

## Model Selection

| Use Case | Model |
|---|---|
| Classification, tagging, regime labeling | `claude-haiku-4-5-20251001` |
| Signal interpretation, briefing generation | `claude-sonnet-4-6` |

Never use Opus for automated/scheduled tasks. Cost not justified.

---

## Rate Limiting Rules

- `polygon_client.py` owns all throttling. The `@rate_limited_call` decorator is the only place `time.sleep()` is called.
- `POLYGON_TIER = "free"` → 5 req/min enforced. `POLYGON_TIER = "starter"` → effectively unlimited.
- Changing the tier in `config.py` is the **only** change needed.
- Retry strategy: exponential backoff, `backoff_factor=2`, retries on 429/500/502/503/504, respects `Retry-After` header.

---

## Security Rules

- **Never hardcode secrets.** All credentials via `.env` (local) or environment variables.
- **Never commit:** `.env`, `data/`, `logs/`, any `.db` file, personal watchlist extensions
- **Always commit:** `.env.example` with placeholder values
- **Before any PR to main:** verify no personal data or API keys are staged

---

## Polygon.io Endpoint Reference

| Data | Endpoint |
|---|---|
| OHLCV daily bars | `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` |
| Ticker metadata | `GET /v3/reference/tickers/{ticker}` |
| VIX | ticker = `I:VIX` |
| VVIX | ticker = `I:VVIX` |

---

## Phase Status

| Phase | Status | Goal |
|---|---|---|
| Phase 1 | 🔄 In progress | Foundation: `polygon_client.py`, `schema.sql`, `config.py`, `watchlist.py` |
| Phase 2 | 🔲 Not started | Ingest DAG live, `raw_prices` populating |
| Phase 3 | 🔲 Not started | Indicators DAG, `stock_signals` populating |
| Phase 4 | 🔲 Not started | Relatedness DAG, `relatedness_matrix` + `sector_beta` |
| Phase 5 | 🔲 Not started | LLM Analysis DAG, `llm_analysis` table |
| Phase 6 | 🔲 Not started | Jarvis integration: daily brief endpoint, alert triggers |

Update this table as phases complete.

---

## CI/CD Pipeline

Three GitHub Actions workflows — all must pass before merging to `main`:

| Workflow | Tools |
|---|---|
| `security.yml` | TruffleHog (full history) + gitleaks |
| `quality.yml` | Ruff + pytest (Python 3.11 + 3.12 matrix) |
| `docker.yml` | `docker compose config` with `.env.example` |

Branch strategy: `feature/phase-N-name` → `develop` → `main` (protected)

---

## Jarvis Integration (Phase 6)

Project Signal feeds Project Jarvis via a shared DB table or webhook. The `push_to_jarvis` task in `dag_llm_analysis` is stubbed as a no-op until Phase 6. Do not implement the integration until Phase 5 is complete and validated.
