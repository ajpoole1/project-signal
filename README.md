# Project Signal

A nightly stock intelligence pipeline that collects price data, computes technical indicators, builds cross-asset relatedness matrices, and runs a structured LLM decisioning layer. Built on Airflow, Postgres, EODHD, and the Anthropic API.

Designed as a portfolio-grade open source project. All logic is public. API keys and personal watchlists stay local.

---

## How it works

Six independent DAGs run in sequence each night, orchestrated by `dag_orchestrator`:

```
EODHD (US + TSX/TSX-V + VIX/VVIX)
        │
        ▼
dag_stock_ingest        →  raw_prices + ticker_metadata
        │
        ▼
dag_stock_indicators    →  stock_signals + signal_features
        │
        ├── (Sundays) ──►  dag_stock_relatedness  →  relatedness_matrix + sector_beta
        │
        ▼
dag_llm_analysis        →  llm_analysis + daily_brief
        │
        ▼
dag_outcome_tracker     →  signal_predictions + signal_accuracy + prediction_outcomes
        │
        └── (Sundays) ──►  dag_parameter_review   →  weekly parameter health brief
```

| DAG | Schedule | Purpose |
|---|---|---|
| `dag_orchestrator` | 5 AM UTC, Sun–Fri | Sequences all sub-DAGs; handles Sunday branch |
| `dag_stock_ingest` | Triggered | OHLCV + metadata from EODHD; nightly corporate-actions refresh |
| `dag_stock_indicators` | Triggered | Technical indicators + v2 continuous features + regime classification |
| `dag_stock_relatedness` | Sundays only | Pearson correlation matrix + sector beta |
| `dag_llm_analysis` | Triggered | Algorithmic bias/confidence/key levels; Sonnet daily brief |
| `dag_outcome_tracker` | Triggered | v1 prediction tracking + v2 volatility-scaled outcomes |
| `dag_parameter_review` | Sundays only | Sonnet weekly parameter health report |

---

## Signal design

### Technical indicators

| Indicator | Weight | Logic |
|---|---|---|
| SMA 200 | 30% | Close > SMA200 → bullish. Primary trend filter. |
| SMA 50 | 25% | Close > SMA50 → short-term momentum confirmation. |
| MACD | 25% | MACD line > signal line → bullish momentum. |
| RSI (14) | 20% | < 30 oversold (+1), 30–40 recovering (+0.5), 40–70 neutral (0), > 70 overbought (−1) |
| Bollinger Bands | — | Computed and stored; used as key-level context. |

Sub-signals combine into `composite_score` (−1 to +1), normalised by the weight of present indicators. Score is multiplied by the VIX regime multiplier to produce `composite_vix_adj`. Phase 3.5 introduced regime-conditional weights (see `config/config.py`).

### Volatility overlay

| VIX Level | Regime | Multiplier |
|---|---|---|
| < 15 | `low` | 0.85× |
| 15–20 | `normal` | 1.00× |
| 20–30 | `elevated` | 1.10× |
| 30–40 | `high` | 1.20× |
| > 40 | `extreme` | 0.70× |

VVIX classifies volatility-of-volatility into: `complacent` / `clean_fear` / `elevated` / `chaotic` / `spike`.

---

## Stack

| Layer | Technology |
|---|---|
| Orchestration | Apache Airflow 2.9 |
| Database | Postgres (native Windows, accessed via `host.docker.internal`) |
| Market data | EODHD — US equities, TSX/TSX-V, VIX/VVIX in a single API |
| LLM | Anthropic Claude (`claude-sonnet-4-6` for brief + review; `claude-haiku-4-5-20251001` for rationale) |
| Infrastructure | Docker Compose |

---

## Project structure

```
project-signal/
├── dags/                        # Airflow DAG definitions — orchestration only
│   ├── dag_orchestrator.py
│   ├── dag_stock_ingest.py
│   ├── dag_stock_indicators.py
│   ├── dag_stock_relatedness.py
│   ├── dag_llm_analysis.py
│   ├── dag_outcome_tracker.py
│   └── dag_parameter_review.py
├── dag_components/              # Task implementations and pure-math calculations
│   ├── dag_builder.py
│   ├── ingest/                  # tasks.py
│   ├── indicators/              # calculations.py + tasks.py
│   ├── relatedness/             # calculations.py + tasks.py
│   ├── llm/                     # calculations.py + prompt_builder.py + tasks.py
│   ├── outcome_tracker/         # calculations.py + calculations_v2.py + tasks.py
│   └── features/                # calculations.py + tasks.py (v2 feature layer)
├── plugins/                     # Shared library
│   ├── base_client.py           # BaseMarketClient + backoff session
│   ├── eodhdclient.py           # All market data (US, TSX/TSX-V, indices)
│   ├── polygon_client.py        # Retained for reference — not active
│   ├── yfinance_client.py       # Retained for reference — not active
│   └── routing.py               # get_client_for_ticker(), resolve_vix_tickers()
├── config/
│   ├── config.py                # All tunables: weights, thresholds, model names, v2 config
│   ├── watchlist.py             # Loads ticker_universe.json; sector ETFs hardcoded
│   ├── ticker_universe.json     # ~6,983 tickers with exchange + has_data flags
│   └── parameter_overrides.json # Approved parameter changes — starts as {}
├── docs/                        # Architecture, patterns, roadmap, reference
├── sql/
│   ├── schema.sql               # All CREATE TABLE statements with indexes
│   └── migrations/              # Numbered schema migration scripts
├── scripts/
│   ├── backfill_eodhd.py
│   ├── backfill_indicators.py
│   ├── backfill_predictions.py
│   ├── backfill_features.py
│   ├── backfill_outcomes_v2.py
│   └── optimize_parameters.py
└── tests/
```

---

## Getting started

**Prerequisites:** Docker, Docker Compose, an [EODHD](https://eodhd.com) API key, an [Anthropic](https://console.anthropic.com) API key, Postgres running natively on Windows.

```bash
git clone https://github.com/ajpoole1/project-signal.git
cd project-signal

cp .env.example .env
# fill in EODHD_API_KEY, ANTHROPIC_API_KEY, POSTGRES_USER, POSTGRES_PASSWORD

docker compose up -d
```

Airflow UI: `http://localhost:8080` (admin / admin).

To backfill historical data before enabling nightly DAGs:

```bash
# Run inside Docker — Postgres is on the Windows host, accessible via host.docker.internal
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_eodhd.py
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_indicators.py
```

Then unpause the DAGs in order: ingest → indicators → relatedness → llm_analysis → outcome_tracker.

---

## Database schema

Twelve tables in Postgres. All writes are idempotent (`INSERT ... ON CONFLICT DO UPDATE`).

| Table | Key | Contents |
|---|---|---|
| `raw_prices` | `(ticker, date)` | OHLCV daily bars, currency, source |
| `ticker_metadata` | `ticker` | Name, sector, industry, market cap |
| `stock_signals` | `(ticker, date)` | All indicators + VIX/VVIX context + composite scores |
| `relatedness_matrix` | `(ticker_a, ticker_b, window_days)` | Pearson r at 30/90/365d windows |
| `sector_beta` | `(ticker, etf_proxy, window_days)` | Beta vs SPY/QQQ/sector ETFs at 90/365d |
| `llm_analysis` | `(ticker, date)` | Algorithmic bias, confidence, key levels, reasoning |
| `daily_brief` | `date` | Sonnet-generated morning brief |
| `signal_predictions` | `(ticker, signal_date, signal_version)` | Signal snapshot + v1 forward-price outcomes (5/10/20d); v1/v2 coexist via `signal_version` |
| `signal_accuracy` | `(signal_version, vix_regime, vol_environment, bias, horizon_days)` | Weekly accuracy rollup |
| `parameter_proposals` | `id` | LLM-generated proposals pending human review |
| `prediction_outcomes` | `(ticker, signal_date, horizon_days)` | v2 long-format: vol-scaled, benchmark-adjusted, episodic |
| `signal_features` | `(ticker, date)` | v2 continuous features + regime classification |

---

## CI/CD

| Workflow | Tools |
|---|---|
| Security scan | TruffleHog (full git history) + gitleaks |
| Python quality | Ruff lint + format, pytest on Python 3.11 and 3.12 |
| Docker validation | `docker compose config` with `.env.example` |

`main` is branch-protected. Branch strategy: `feature/*` → `develop` → `main`.

---

## Build status

See [`docs/roadmap.md`](docs/roadmap.md) for current phase status and decision log.

---

## Design principles

- **Single data source** — All market data flows through EODHD. No multi-source routing complexity.
- **Idempotent writes** — Every task can be safely re-run without duplicating data.
- **Separation of concerns** — Independent DAGs; each can fail and retry without corrupting the others.
- **Single throttle point** — All API calls go through `base_client.py`. No `sleep()` in DAG or task files.
- **Pure-pandas math** — Indicators and features implemented directly. No pandas-ta or indicator libraries.
- **Logic is public, data is local** — Secrets and personal watchlists never touch the repo.
