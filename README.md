# Project Signal

A nightly stock intelligence pipeline that collects price data, computes technical indicators, builds cross-asset relatedness matrices, and runs a structured LLM decisioning layer. Built on Airflow, Postgres, Polygon.io, and the Anthropic API.

Designed as a portfolio-grade open source project. All logic is public. API keys and personal watchlists stay local.

---

## How it works

Four independent DAGs run in sequence each night:

```
Polygon.io + yfinance
        │
        ▼
dag_stock_ingest        →  raw_prices + ticker_metadata     (midnight EST)
        │
        ▼
dag_stock_indicators    →  stock_signals                     (3 AM EST)
        │
        ├──────────────►
        │              dag_stock_relatedness  →  relatedness_matrix + sector_beta  (Sundays)
        ▼
dag_llm_analysis        →  llm_analysis  →  Jarvis briefing  (TBD)
```

| DAG | Schedule | Purpose |
|---|---|---|
| `dag_stock_ingest` | Midnight EST, weekdays | Fetch OHLCV + VIX/VVIX |
| `dag_stock_indicators` | 3 AM EST, weekdays | Compute all technical indicators and composite signal score |
| `dag_stock_relatedness` | Sundays | Pearson correlation matrix, sector beta, peer clusters |
| `dag_llm_analysis` | TBD weekdays | Claude API: per-ticker bias, confidence, key levels, reasoning |

---

## Signal design

### Technical indicators

| Indicator | Weight | Logic |
|---|---|---|
| SMA 200 | 30% | Close > SMA_200 → bullish. Primary trend filter. |
| SMA 50 | 25% | Close > SMA_50 → short-term momentum confirmation. |
| MACD | 25% | MACD line > signal line → bullish momentum. |
| RSI (14) | 20% | < 30 oversold (+1), 30–40 recovering (+0.5), 40–70 neutral (0), > 70 overbought (−1) |
| Bollinger Bands | — | Computed and stored; used as context by LLM layer (Phase 5). |

Sub-signals are combined into `composite_score` (range: −1 to +1), normalised by the weight of present indicators so tickers with short history still produce a valid score. Score is then multiplied by the VIX regime multiplier to produce `composite_vix_adj`.

### Volatility overlay (VIX + VVIX)

VIX and VVIX act as macro regime multipliers applied to every per-stock composite score.

| VIX Level | Regime | Multiplier |
|---|---|---|
| < 15 | `low` | 0.85× — complacent market, dampen signals |
| 15–20 | `normal` | 1.00× — baseline |
| 20–30 | `elevated` | 1.10× — slight boost |
| 30–40 | `high` | 1.20× — strong breakout environment |
| > 40 | `extreme` | 0.70× — crisis mode, signals unreliable |

VVIX classifies the volatility-of-volatility into: `complacent` / `clean_fear` / `elevated` / `chaotic` / `spike`.

---

## Stack

| Layer | Technology |
|---|---|
| Orchestration | Apache Airflow 2.9 |
| Database | Postgres (native Windows, accessed via host.docker.internal) |
| Market data | Polygon.io (free → Starter at $29/mo) + yfinance (TSX/TSXV + VIX/VVIX) |
| LLM | Anthropic Claude (Sonnet for analysis, Haiku for classification) |
| Infrastructure | Docker Compose |

---

## Project structure

```
project-signal/
├── dags/                    # Airflow DAG definitions — orchestration only
│   ├── dag_stock_ingest.py
│   └── dag_stock_indicators.py
├── dag_components/          # Task implementations imported by DAGs
│   ├── dag_builder.py       # SignalDAG constructor
│   ├── ingest/tasks.py      # @task functions for dag_stock_ingest
│   └── indicators/
│       ├── calculations.py  # Pure pandas indicator math
│       └── tasks.py         # @task functions for dag_stock_indicators
├── plugins/                 # Shared library — clients, routing
│   ├── base_client.py       # BaseMarketClient + @rate_limited_call
│   ├── polygon_client.py    # US equities, ETFs, paid-tier indices
│   ├── yfinance_client.py   # TSX/TSXV equities (permanent) + VIX/VVIX (free-tier)
│   └── routing.py           # get_client_for_ticker(), resolve_vix_tickers()
├── config/
│   ├── config.py            # All tunable values: tiers, weights, thresholds, model names
│   ├── watchlist.py         # Loads from ticker_universe.json; sector ETFs hardcoded
│   └── ticker_universe.json # 650-ticker personal universe with exchange + has_data flags
├── sql/
│   ├── schema.sql           # All CREATE TABLE statements with indexes
│   └── migrations/          # ALTER TABLE scripts for live schema changes
├── scripts/
│   └── backfill_history.py  # One-time yfinance historical backfill (run inside Docker)
└── tests/
    ├── test_polygon_client.py
    ├── test_yfinance_client.py
    └── test_indicators.py
```

---

## Getting started

**Prerequisites:** Docker, Docker Compose, a [Polygon.io](https://polygon.io) API key, an [Anthropic](https://console.anthropic.com) API key, Postgres running natively.

```bash
git clone https://github.com/ajpoole1/project-signal.git
cd project-signal

cp .env.example .env
# fill in POLYGON_API_KEY, ANTHROPIC_API_KEY, POSTGRES_USER, POSTGRES_PASSWORD, etc.

docker compose up -d
```

Airflow UI is available at `http://localhost:8080`. Log in with `admin` / `admin`.

To backfill historical price data before enabling the nightly DAGs:

```bash
# Run inside Docker — Postgres is native Windows, accessible via host.docker.internal
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_history.py

# Override lookback window (default: 400 calendar days)
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_history.py --days 500
```

Then unpause `dag_stock_ingest` and `dag_stock_indicators` in the Airflow UI.

### Tier upgrade

The free Polygon tier limits to 5 requests/minute (~2 hours for 625 tickers). To remove throttling after upgrading to Starter ($29/mo), change three lines in `config/config.py`:

```python
POLYGON_TIER = "starter"   # was "free"
VIX_SOURCE   = "polygon"   # was "yfinance"
VVIX_SOURCE  = "polygon"   # was "yfinance"
```

After switching VIX source, run the migration in `sql/migrations/` to rename historical `^VIX`/`^VVIX` rows to `I:VIX`/`I:VVIX`. No DAG or client changes needed.

---

## Database schema

Six tables in Postgres. All writes are idempotent (`INSERT ... ON CONFLICT DO UPDATE`).

| Table | Key | Contents |
|---|---|---|
| `raw_prices` | `(ticker, date)` | OHLCV daily bars, currency, source |
| `ticker_metadata` | `ticker` | Name, sector, industry, market cap |
| `stock_signals` | `(ticker, date)` | All indicators + VIX regime + composite scores |
| `relatedness_matrix` | `(ticker_a, ticker_b, window_days)` | Pearson r at 30/90/365-day windows |
| `sector_beta` | `(ticker, etf_proxy, window_days)` | Beta vs SPY/QQQ/sector ETFs |
| `llm_analysis` | `(ticker, date)` | Claude output: bias, confidence, key levels, reasoning |

---

## CI/CD

Three GitHub Actions workflows run on every push and pull request:

| Workflow | Tools |
|---|---|
| Security scan | TruffleHog (full git history) + gitleaks |
| Python quality | Ruff lint + format, pytest on Python 3.11 and 3.12 |
| Docker validation | `docker compose config` with `.env.example` |

`main` is branch-protected. All checks must pass before merge.

Branch strategy: `feature/phase-N-name` → `develop` → `main`

---

## Build progress

| Phase | Status | Deliverable |
|---|---|---|
| Phase 1 — Foundation | ✅ Done | `polygon_client.py`, `schema.sql`, `config.py`, `watchlist.py`, CI/CD |
| Phase 2 — Ingest DAG | ✅ Done | `dag_stock_ingest` running nightly, `raw_prices` populating |
| Phase 3 — Indicators DAG | ✅ Done | `dag_stock_indicators` running nightly, `stock_signals` populating, 625-ticker universe live |
| Phase 4 — Relatedness DAG | 🔲 Not started | `dag_stock_relatedness`, `relatedness_matrix` + `sector_beta` |
| Phase 5 — LLM Analysis DAG | 🔲 Not started | `dag_llm_analysis`, `llm_analysis` table |
| Phase 6 — Jarvis integration | 🔲 Not started | Daily brief endpoint, alert triggers |

---

## Design principles

- **Config-driven tier upgrades** — Three lines in `config.py` moves from free to paid Polygon. No DAG rewrites.
- **Idempotent writes** — Every task can be safely re-run without duplicating data.
- **Separation of concerns** — Four independent DAGs. Each can fail and retry without corrupting the others.
- **Single throttle point** — All Polygon calls go through `base_client.py`. No `sleep()` calls in DAG or task files.
- **No beta library math** — Indicators implemented with pandas directly. No pandas-ta or similar.
- **Logic is public, data is local** — Secrets and personal watchlists never touch the repo.
