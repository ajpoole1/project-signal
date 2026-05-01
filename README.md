# Project Signal

A nightly stock intelligence pipeline that collects price data, computes technical indicators, builds cross-asset relatedness matrices, and runs a structured LLM decisioning layer. Built on Airflow, Postgres, Polygon.io, and the Anthropic API.

Designed as a portfolio-grade open source project. All logic is public. API keys and personal watchlists stay local.

---

## How it works

Four independent DAGs run in sequence each night:

```
Polygon.io API
      │
      ▼
dag_stock_ingest        →  raw_prices + ticker_metadata
      │
      ▼
dag_stock_indicators    →  stock_signals  (SMA, MACD, RSI, Bollinger, VIX regime, composite score)
      │
      ├──────────►
      │          dag_stock_relatedness  →  relatedness_matrix + sector_beta  (weekly)
      ▼
dag_llm_analysis        →  llm_analysis  →  Jarvis briefing
```

| DAG | Schedule | Purpose |
|---|---|---|
| `dag_stock_ingest` | 9 PM ET, weekdays | Fetch OHLCV + VIX/VVIX from Polygon.io |
| `dag_stock_indicators` | 10 PM ET, weekdays | Compute all technical indicators and composite signal score |
| `dag_stock_relatedness` | 11 PM ET, Sundays | Pearson correlation matrix, sector beta, peer clusters |
| `dag_llm_analysis` | 11 PM ET, weekdays | Claude API: per-ticker bias, confidence, key levels, reasoning |

---

## Signal design

### Technical indicators

| Indicator | Weight | Logic |
|---|---|---|
| SMA 200 | 30% | Close > SMA_200 → bullish. Primary trend filter. |
| SMA 50 | 25% | Close > SMA_50 → short-term momentum confirmation. |
| MACD Histogram | 25% | MACDh > 0 → bullish momentum. |
| RSI (14) | 20% | 40–70 = healthy. < 30 = oversold. > 70 = overbought. |
| Bollinger Bands | modifier | Near lower band + other signals → boost composite. |

Scores are combined into a `composite_score` (0.0–1.0), then adjusted by the VIX regime multiplier to produce `composite_vix_adj`.

### Volatility overlay (VIX + VVIX)

VIX and VVIX act as macro regime multipliers applied to every per-stock composite score, not additive components.

| VIX Level | Regime | Multiplier |
|---|---|---|
| < 15 | low | 0.85× — complacent, dampen signals |
| 15–20 | normal | 1.00× — baseline |
| 20–30 | elevated | 1.10× — slight boost |
| 30–40 | high | 1.20× — best breakout environment |
| > 40 | extreme | 0.70× — crisis mode, signals unreliable |

VVIX classifies the volatility *of* volatility into four environments: `clean_fear`, `chaotic_fear`, `complacent`, `vol_spike`, or `normal`.

---

## Stack

| Layer | Technology |
|---|---|
| Orchestration | Apache Airflow 2.9 |
| Database | Postgres 15 |
| Market data | Polygon.io (free tier → Starter at $29/mo) |
| LLM | Anthropic Claude (Sonnet for analysis, Haiku for classification) |
| Infrastructure | Docker Compose |

---

## Project structure

```
project-signal/
├── dags/              # Airflow DAG definitions — orchestration only
├── plugins/           # Shared library: polygon_client, indicators, vix_classifier, relatedness, llm_client
├── config/
│   ├── config.py      # All tunable values: tiers, weights, thresholds, model names
│   └── watchlist.py   # Ticker list + ETF proxies
├── sql/
│   └── schema.sql     # All CREATE TABLE statements with indexes
├── scripts/           # Manual utility scripts (bulk CSV loader, etc.)
└── tests/             # One test file per plugin module
```

---

## Getting started

**Prerequisites:** Docker, Docker Compose, a [Polygon.io](https://polygon.io) API key, an [Anthropic](https://console.anthropic.com) API key.

```bash
git clone https://github.com/ajpoole1/project-signal.git
cd project-signal

cp .env.example .env
# fill in POLYGON_API_KEY and ANTHROPIC_API_KEY

docker compose up -d
```

Airflow UI will be available at `http://localhost:8081`. The schema is applied automatically on first Postgres startup via `sql/schema.sql`.

To load historical data manually instead of waiting for nightly ingest:

```bash
# CSV format: ticker,date,open,high,low,close,volume
python scripts/bulk_load_history.py --file data/history.csv
```

### Tier upgrade

The free Polygon tier limits to 5 requests/minute. To remove throttling after upgrading to Starter ($29/mo), change one line in `config/config.py`:

```python
POLYGON_TIER = "starter"  # was "free"
```

No other changes needed.

---

## Database schema

Six tables in Postgres. All writes are idempotent (`INSERT ... ON CONFLICT DO UPDATE`).

| Table | Key | Contents |
|---|---|---|
| `raw_prices` | `(ticker, date)` | OHLCV daily bars from Polygon |
| `ticker_metadata` | `ticker` | Name, sector, industry, market cap |
| `stock_signals` | `(ticker, date)` | All indicators + VIX regime + composite scores |
| `relatedness_matrix` | `(ticker_a, ticker_b, window_days)` | Pearson correlation at 30/90/365-day windows |
| `sector_beta` | `(ticker, etf_proxy, window_days)` | Beta vs SPY/QQQ/sector ETFs |
| `llm_analysis` | `(ticker, date)` | Claude output: bias, confidence, key levels, reasoning |

---

## CI/CD

Three GitHub Actions workflows run on every push and pull request to `main` and `develop`:

| Workflow | Tools |
|---|---|
| Security scan | TruffleHog (full git history) + gitleaks |
| Python quality | Ruff lint + format, pytest on Python 3.11 and 3.12 |
| Docker validation | `docker compose config` with `.env.example` |

`main` is branch-protected. All five checks must pass before merge.

---

## Build progress

| Phase | Status | Deliverable |
|---|---|---|
| Phase 1 — Foundation | ✅ Done | `polygon_client.py`, `schema.sql`, `config.py`, `watchlist.py`, CI/CD |
| Phase 2 — Ingest DAG | 🔲 Not started | `dag_stock_ingest` running nightly, `raw_prices` populating |
| Phase 3 — Indicators DAG | 🔲 Not started | `dag_stock_indicators`, `stock_signals` populating |
| Phase 4 — Relatedness DAG | 🔲 Not started | `dag_stock_relatedness`, `relatedness_matrix` + `sector_beta` |
| Phase 5 — LLM Analysis DAG | 🔲 Not started | `dag_llm_analysis`, `llm_analysis` table |
| Phase 6 — Jarvis integration | 🔲 Not started | Daily brief endpoint, alert triggers, expanded watchlist |

---

## Design principles

- **Config-driven tier upgrades** — One line change in `config.py` removes rate limiting. No DAG rewrites.
- **Idempotent writes** — Every task can be safely re-run without duplicating data.
- **Separation of concerns** — Four independent DAGs. Each can fail and retry without corrupting the others.
- **Single throttle point** — All Polygon calls go through `polygon_client.py`. No `sleep()` calls elsewhere.
- **Logic is public, data is local** — Secrets and watchlists never touch the repo.
