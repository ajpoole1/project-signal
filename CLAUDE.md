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

| DAG | UTC Schedule | EST | Purpose |
|---|---|---|---|
| `dag_stock_ingest` | `0 5 * * 1-5` | midnight | Fetch OHLCV + VIX/VVIX from Polygon/yfinance |
| `dag_stock_indicators` | `0 8 * * 1-5` | 3 AM | SMA, MACD, RSI, Bollinger, VIX regime, composite score |
| `dag_stock_relatedness` | TBD (Sundays) | TBD | Pearson correlation matrix, sector beta, peer clusters |
| `dag_llm_analysis` | TBD (weekdays) | TBD | Claude API: per-ticker signal + Jarvis briefing |

**Ordering:** Ingest runs first (midnight). Indicators runs 3 hours later (3 AM). At free-tier Polygon (5 req/min, 625 tickers), ingest can take ~2 hours — the 3-hour buffer is intentional. With a paid key, ingest finishes in minutes and the buffer is slack.

### Key Design Decisions — Do not revisit without good reason

- **Postgres is the shared layer.** All four DAGs read/write the same schema. No per-DAG side databases.
- **`plugins/` is the shared library.** DAG files import from plugins; DAG files contain no business logic.
- **All config in `config/config.py`.** Signal weights, VIX thresholds, correlation windows, model names — nothing hardcoded in DAG or plugin files.
- **Watchlist in `config/watchlist.py`.** Loads US/TSX tickers from `config/ticker_universe.json` at import time (`has_data=True` only). Sector ETFs are hardcoded separately as Phase 4 beta-proxy infrastructure. Extending the watchlist means editing the JSON — never a DAG file.
- **VIX/VVIX not in watchlist.** Resolved at runtime via `plugins/routing.py:resolve_vix_tickers()` based on `config.VIX_SOURCE`. No format inspection in DAG/task files.
- **Ticker routing in `plugins/routing.py`.** Never inspect ticker format (`.TO`, `^`, `I:`) directly in DAG or task files. Call `get_client_for_ticker()` and `resolve_vix_tickers()` instead.
- **Airflow TaskFlow API only.** Use `@task` decorator for all Python tasks. No classic operators for Python logic.
- **Data source interface.** All provider clients (`PolygonClient`, `YFinanceClient`) implement `fetch_ohlcv(ticker, start, end)` and `fetch_metadata(ticker)` returning normalized dicts. Tasks use the interface, never provider-specific methods.
- **No pandas-ta.** All indicator math uses pandas directly (rolling, EWM). No beta library dependencies for core calculations.

---

## File Conventions

```
project-signal/
├── dags/                        # Airflow DAG definitions — orchestration only, no logic
│   ├── dag_stock_ingest.py
│   └── dag_stock_indicators.py
├── dag_components/              # Task implementations imported by DAGs
│   ├── dag_builder.py           # OOP DAG constructor (DAGBuilder base + SignalDAG)
│   ├── ingest/
│   │   └── tasks.py             # @task functions for dag_stock_ingest
│   └── indicators/
│       ├── calculations.py      # Pure pandas functions — no Airflow imports
│       └── tasks.py             # @task functions for dag_stock_indicators
├── plugins/                     # Shared library: provider clients, routing
│   ├── base_client.py           # BaseMarketClient + rate_limited_call decorator
│   ├── polygon_client.py        # PolygonClient — US equities, ETFs, paid-tier indices
│   ├── yfinance_client.py       # YFinanceClient — TSX equities (permanent), VIX/VVIX (free-tier)
│   └── routing.py               # get_client_for_ticker(), resolve_vix_tickers()
├── config/
│   ├── config.py                # All tunable values: tiers, weights, thresholds, model names
│   ├── watchlist.py             # Loads from ticker_universe.json; sector ETFs hardcoded
│   └── ticker_universe.json     # 650-ticker personal universe (has_data flags, exchange labels)
├── sql/
│   ├── schema.sql               # All CREATE TABLE + indexes — run once on fresh Postgres
│   └── migrations/              # ALTER TABLE scripts for live database upgrades
├── scripts/                     # Manual utility scripts — run inside Docker
│   └── backfill_history.py      # One-time yfinance backfill — 400 days into raw_prices
└── tests/                       # One test file per plugin/component module
    ├── test_polygon_client.py
    ├── test_yfinance_client.py
    └── test_indicators.py
```

**Every plugin and dag_components module must have a corresponding test file.**

---

## Ticker Universe

- **Source of truth:** `config/ticker_universe.json` — 650 tickers across 21 categories
- **Active universe:** 616 tickers with `has_data=True` (527 US + 89 TSX/TSX-V)
- **Sector ETFs:** 7 hardcoded in `watchlist.py` (SPY, QQQ, XLK, XLF, XLE, XLV, XLY) — Phase 4 infrastructure, not in JSON
- **Total ingest load:** 625 tickers per night (623 equity/ETF + ^VIX + ^VVIX)
- **Routing:** US equities + ETFs → Polygon; `.TO` tickers + VIX/VVIX → yfinance

`get_all_tickers()` → 623 tickers (used by ingest + indicators)
`get_equity_tickers()` → 616 tickers (used by Phase 4 relatedness — excludes sector ETFs)

---

## Database Rules

- All tables defined in `sql/schema.sql`. Never create tables in Python code.
- All writes: `INSERT ... ON CONFLICT (ticker, date) DO UPDATE SET ...`
- Never `TRUNCATE` or `DELETE` in DAG tasks. Soft deletes only (update `computed_at`, let queries filter by recency).
- All DB connections via Airflow Postgres hook in DAG tasks. No hardcoded connection strings.
- `scripts/` are standalone utilities — they connect directly via psycopg2, hardcode `dbname="signal"`, and read credentials from `.env`.
- Primary keys: `(ticker, date)` on all time-series tables.

**Backfill command (run inside Docker, not from WSL directly):**
```bash
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_history.py
```
Postgres is native Windows; Docker containers reach it via `host.docker.internal`. The `POSTGRES_HOST` env var is pre-set in docker-compose — running inside a container is the correct execution context for scripts.

---

## Indicator Logic (Phase 3 — complete)

Implemented in `dag_components/indicators/calculations.py`. All windows from `config.py`.

### Sub-signals → composite_score

| Indicator | Config | Score |
|---|---|---|
| SMA 200 | `SMA_LONG_WINDOW=200` | `+1` close > SMA200, `-1` below |
| SMA 50 | `SMA_SHORT_WINDOW=50` | `+1` close > SMA50, `-1` below |
| MACD | `MACD_FAST=12, SLOW=26, SIGNAL=9` | `+1` MACD line > signal line, `-1` below |
| RSI 14 | `RSI_WINDOW=14` | `+1` < 30, `+0.5` 30–40, `0` 40–70, `-1` > 70 |

`composite_score` = weighted sum / total weight present (normalises if indicators are null due to short history). Range: `[-1, 1]`.

`composite_vix_adj` = composite_score × VIX regime multiplier.

### VIX regime multipliers

| VIX | Regime | Multiplier |
|---|---|---|
| < 15 | `low` | 0.85 |
| 15–20 | `normal` | 1.00 |
| 20–30 | `elevated` | 1.10 |
| 30–40 | `high` | 1.20 |
| ≥ 40 | `extreme` | 0.70 |

VIX trend: ratio of close to 20-day SMA → `expanding` (> 1.20) / `stable` / `contracting` (< 0.85)

VVIX vol environment: `complacent` (< 85) → `clean_fear` (< 100) → `elevated` (< 115) → `chaotic` (< 120) → `spike`

---

## Model Selection

| Use Case | Model |
|---|---|
| Classification, tagging, regime labeling | `claude-haiku-4-5-20251001` |
| Signal interpretation, briefing generation | `claude-sonnet-4-6` |

Never use Opus for automated/scheduled tasks. Cost not justified.

---

## Rate Limiting Rules

- `base_client.py` owns all throttling. The `@rate_limited_call` decorator is the only place `time.sleep()` is called for rate-limiting. `YFinanceClient` uses a courtesy 0.5s sleep only.
- `POLYGON_TIER = "free"` → 5 req/min enforced. `POLYGON_TIER = "starter"` → effectively unlimited.
- Changing the tier in `config.py` is the **only** change needed.
- Retry strategy: exponential backoff, `backoff_factor=2`, retries on 429/500/502/503/504, respects `Retry-After` header.

### Polygon → Starter upgrade checklist (3 lines in config.py)

```python
POLYGON_TIER = "starter"   # was "free"
VIX_SOURCE   = "polygon"   # was "yfinance"
VVIX_SOURCE  = "polygon"   # was "yfinance"
```

**After switching VIX source:** run a migration to normalize `raw_prices` ticker symbols:
```sql
UPDATE raw_prices SET ticker = 'I:VIX'  WHERE ticker = '^VIX';
UPDATE raw_prices SET ticker = 'I:VVIX' WHERE ticker = '^VVIX';
```
Add this as a script under `sql/migrations/`. No DAG or plugin code changes needed — routing handles the rest automatically.

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
| Phase 1 | ✅ Complete | Foundation: `polygon_client.py`, `schema.sql`, `config.py`, `watchlist.py` |
| Phase 2 | ✅ Complete | Ingest DAG live, `raw_prices` populating. yfinance added for TSX + VIX/VVIX. |
| Phase 3 | ✅ Complete | Indicators DAG live, `stock_signals` populating. Full 625-ticker universe backfilled. |
| Phase 4 | 🔲 Not started | Relatedness DAG, `relatedness_matrix` + `sector_beta` |
| Phase 5 | 🔲 Not started | LLM Analysis DAG, `llm_analysis` table |
| Phase 6 | 🔲 Not started | Jarvis integration: daily brief endpoint, alert triggers |

Update this table as phases complete.

---

## Phase 4 Starting Context

**Goal:** `dag_stock_relatedness` — reads `raw_prices`, writes `relatedness_matrix` and `sector_beta`.

**Schema already defined** in `sql/schema.sql`:
- `relatedness_matrix (ticker_a, ticker_b, window_days)` — Pearson r at 30/90/365-day windows
- `sector_beta (ticker, etf_proxy, window_days)` — beta vs SPY/QQQ/sector ETFs at 90/365-day windows

**Config already defined** in `config.py`:
```python
CORRELATION_WINDOWS = [30, 90, 365]   # days for Pearson r
PEER_CLUSTER_THRESHOLD = 0.65         # r threshold for peer grouping
BETA_WINDOWS = [90, 365]              # days for beta calculation
SECTOR_ETFS = {                       # ETF proxies for sector beta
    "tech": "XLK", "financials": "XLF", "energy": "XLE",
    "healthcare": "XLV", "consumer": "XLY", "market": "SPY", "nasdaq": "QQQ",
}
```

**Key decisions for Phase 4 agent:**
- Use `get_equity_tickers()` (616 tickers) for correlation pairs — excludes sector ETFs
- Sector ETFs (SPY, QQQ, etc.) are already in `raw_prices` (ingested nightly) — use them as beta targets
- With 616 equity tickers: 616×615/2 = ~189,540 unique pairs × 3 windows = ~568,620 rows. Design for batch inserts, not row-by-row.
- Schedule: Sundays only — correlation is a weekly computation, daily is overkill
- Same DAG conventions: `SignalDAG`, `@task`, `PostgresHook("signal_postgres")`, idempotent upserts
- New module: `dag_components/relatedness/tasks.py` + `dag_components/relatedness/calculations.py`
- New test file: `tests/test_relatedness.py` (required — every module needs a test file)

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
