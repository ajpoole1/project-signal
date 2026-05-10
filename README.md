# Project Signal

A nightly stock intelligence pipeline that collects price data, computes technical indicators, builds cross-asset relatedness matrices, and runs a structured LLM decisioning layer. Built on Airflow, Postgres, EODHD, and the Anthropic API.

Designed as a portfolio-grade open source project. All logic is public. API keys and personal watchlists stay local.

---

## How it works

Four independent DAGs run in sequence each night:

```
EODHD (US + TSX/TSX-V + VIX/VVIX)
        │
        ▼
dag_stock_ingest        →  raw_prices + ticker_metadata          (midnight EST)
        │
        ▼
dag_stock_indicators    →  stock_signals                          (3 AM EST)
        │
        ├──────────────►
        │              dag_stock_relatedness  →  relatedness_matrix + sector_beta  (Sundays 5 AM EST)
        │              dag_parameter_review   →  daily_brief (parameter_review)    (Sundays 6 AM EST)
        ▼
dag_llm_analysis        →  llm_analysis + daily_brief            (4 AM EST)
        │
        ▼
dag_outcome_tracker     →  signal_predictions + signal_accuracy  (1 AM EST)
```

| DAG | Schedule | Purpose |
|---|---|---|
| `dag_stock_ingest` | Midnight EST, weekdays | Fetch OHLCV + VIX/VVIX from EODHD |
| `dag_stock_indicators` | 3 AM EST, weekdays | Compute all technical indicators and composite signal score |
| `dag_stock_relatedness` | Sundays 5 AM EST | Pearson correlation matrix, sector beta, peer clusters |
| `dag_llm_analysis` | 4 AM EST, weekdays | Algorithmic per-ticker bias + confidence + key levels; Sonnet daily brief |
| `dag_outcome_tracker` | 1 AM EST, weekdays | Record signal predictions; resolve matured outcomes; weekly accuracy rollup |
| `dag_parameter_review` | Sundays 6 AM EST | Sonnet weekly parameter health report and proposal recommendations |

---

## Signal design

### Technical indicators

| Indicator | Weight | Logic |
|---|---|---|
| SMA 200 | 30% | Close > SMA_200 → bullish. Primary trend filter. |
| SMA 50 | 25% | Close > SMA_50 → short-term momentum confirmation. |
| MACD | 25% | MACD line > signal line → bullish momentum. |
| RSI (14) | 20% | < 30 oversold (+1), 30–40 recovering (+0.5), 40–70 neutral (0), > 70 overbought (−1) |
| Bollinger Bands | — | Computed and stored; used as key-level context by the LLM layer. |

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
| Market data | EODHD basic plan ($20/mo) — US equities, TSX/TSX-V, VIX/VVIX in a single API |
| LLM | Anthropic Claude (Sonnet for daily brief + weekly parameter review; Haiku for proposal rationale) |
| Infrastructure | Docker Compose |

---

## Project structure

```
project-signal/
├── dags/                        # Airflow DAG definitions — orchestration only
│   ├── dag_stock_ingest.py
│   ├── dag_stock_indicators.py
│   ├── dag_stock_relatedness.py
│   ├── dag_llm_analysis.py
│   ├── dag_outcome_tracker.py
│   └── dag_parameter_review.py
├── dag_components/              # Task implementations imported by DAGs
│   ├── dag_builder.py           # SignalDAG constructor
│   ├── ingest/tasks.py          # @task functions for dag_stock_ingest
│   ├── indicators/
│   │   ├── calculations.py      # Pure pandas indicator math
│   │   └── tasks.py             # @task functions for dag_stock_indicators
│   ├── relatedness/
│   │   ├── calculations.py      # Pearson r, beta — pure pandas
│   │   └── tasks.py             # @task functions for dag_stock_relatedness
│   ├── llm/
│   │   ├── calculations.py      # Algorithmic bias/confidence/key levels/reasoning
│   │   ├── prompt_builder.py    # Sonnet brief system prompt + message builder
│   │   └── tasks.py             # @task functions for dag_llm_analysis
│   └── outcome_tracker/
│       ├── calculations.py      # is_correct(), nth_trading_day_price(), accuracy rollup
│       └── tasks.py             # @task functions for dag_outcome_tracker + dag_parameter_review
├── plugins/                     # Shared library — clients, routing
│   ├── base_client.py           # BaseMarketClient + @rate_limited_call
│   ├── eodhdclient.py           # EODHDClient — all market data (US, TSX/TSX-V, indices)
│   ├── polygon_client.py        # Retained for reference — not active
│   ├── yfinance_client.py       # Retained for reference — not active
│   └── routing.py               # get_client_for_ticker(), resolve_vix_tickers()
├── config/
│   ├── config.py                # All tunable values: weights, thresholds, SIGNAL_VERSION
│   ├── watchlist.py             # Loads from ticker_universe.json; sector ETFs hardcoded
│   ├── ticker_universe.json     # 650-ticker personal universe with exchange + has_data flags
│   └── parameter_overrides.json # Approved parameter changes — starts as {}
├── docs/
│   └── phase6_prediction_tracking.md
├── sql/
│   ├── schema.sql               # All CREATE TABLE statements with indexes
│   └── migrations/              # Schema migration scripts
├── scripts/
│   ├── backfill_eodhd.py        # Full OHLCV history backfill via EODHD (5 years)
│   ├── backfill_indicators.py   # Full indicator backfill over raw_prices history
│   ├── backfill_predictions.py  # Retroactive signal_predictions from llm_analysis history
│   └── optimize_parameters.py  # Grid search → parameter_proposals (never auto-applies)
└── tests/
    ├── test_eodhd_client.py
    ├── test_polygon_client.py
    ├── test_yfinance_client.py
    ├── test_indicators.py
    ├── test_relatedness.py
    ├── test_llm_analysis.py
    └── test_outcome_tracker.py
```

---

## Getting started

**Prerequisites:** Docker, Docker Compose, an [EODHD](https://eodhd.com) API key, an [Anthropic](https://console.anthropic.com) API key, Postgres running natively.

```bash
git clone https://github.com/ajpoole1/project-signal.git
cd project-signal

cp .env.example .env
# fill in EODHD_API_KEY, ANTHROPIC_API_KEY, POSTGRES_USER, POSTGRES_PASSWORD

docker compose up -d
```

Airflow UI is available at `http://localhost:8080`. Log in with `admin` / `admin`.

To backfill historical price and indicator data before enabling the nightly DAGs:

```bash
# Run inside Docker — Postgres is native Windows, accessible via host.docker.internal

# Full OHLCV history (5 years by default, or pass --start YYYY-MM-DD)
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_eodhd.py

# Full indicator history over the backfilled prices
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_indicators.py
```

Then unpause the DAGs in the Airflow UI in order: ingest → indicators → relatedness → llm_analysis.

---

## Database schema

Ten tables in Postgres. All writes are idempotent (`INSERT ... ON CONFLICT DO UPDATE`).

| Table | Key | Contents |
|---|---|---|
| `raw_prices` | `(ticker, date)` | OHLCV daily bars, currency, source |
| `ticker_metadata` | `ticker` | Name, sector, industry, market cap |
| `stock_signals` | `(ticker, date)` | All indicators + VIX regime + composite scores |
| `relatedness_matrix` | `(ticker_a, ticker_b, window_days)` | Pearson r at 30/90/365-day windows |
| `sector_beta` | `(ticker, etf_proxy, window_days)` | Beta vs SPY/QQQ/sector ETFs |
| `llm_analysis` | `(ticker, date)` | Algorithmic bias, confidence, key levels, reasoning |
| `daily_brief` | `date` | Sonnet-generated morning brief from top conviction signals |
| `signal_predictions` | `(ticker, signal_date)` | Signal snapshot + forward price outcomes (5/10/20d) |
| `signal_accuracy` | `(signal_version, vix_regime, vol_environment, bias, horizon_days)` | Weekly accuracy rollup by regime |
| `parameter_proposals` | `id` | LLM-generated parameter change proposals pending human review |

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
| Phase 1 — Foundation | ✅ Done | `schema.sql`, `config.py`, `watchlist.py`, CI/CD |
| Phase 2 — Ingest DAG | ✅ Done | `dag_stock_ingest` running nightly via EODHD, `raw_prices` populating |
| Phase 3 — Indicators DAG | ✅ Done | `dag_stock_indicators` running nightly, `stock_signals` populating, 5-year backfill complete |
| Phase 4 — Relatedness DAG | ✅ Done | `dag_stock_relatedness` running weekly, `relatedness_matrix` + `sector_beta` populating |
| Phase 5 — LLM Analysis DAG | ✅ Done | `dag_llm_analysis` running nightly, `llm_analysis` + `daily_brief` populating |
| Phase 6 — Prediction Tracking | 🔲 Not started | Signal feedback loop: outcome evaluation, accuracy by regime, parameter optimization |
| Phase 7 — Jarvis integration | 🔲 Not started | Daily brief endpoint, alert triggers |

---

## Design principles

- **Single data source** — All market data (US, TSX/TSX-V, VIX/VVIX) flows through EODHD. No multi-source routing complexity.
- **Idempotent writes** — Every task can be safely re-run without duplicating data.
- **Separation of concerns** — Four independent DAGs. Each can fail and retry without corrupting the others.
- **Single throttle point** — All API calls go through `base_client.py`. No `sleep()` calls in DAG or task files.
- **No beta library math** — Indicators implemented with pandas directly. No pandas-ta or similar.
- **Logic is public, data is local** — Secrets and personal watchlists never touch the repo.
