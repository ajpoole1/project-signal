# Project Signal

A nightly stock-intelligence pipeline. It ingests market data across ~6,900 US and Canadian
tickers (of ~6,983 in the universe, those with price history), computes technical indicators and cross-asset relatedness, scores every name, and
tracks how well each prediction actually played out — then feeds the results back into a
weekly, human-gated parameter-tuning loop.

Built on Apache Airflow, PostgreSQL, EODHD, and the Anthropic API. Runs nightly on AWS
(EC2 + RDS), starting cold and winding itself down when the work is done.

> **Portfolio-grade open source.** All logic is public. API keys and personal watchlists
> never touch the repo. The interesting part isn't that it computes a score — it's that a
> full **v2 rebuild** tore the original scoring model down to the studs after the data showed
> it carried almost no directional edge. That empirical teardown is documented below and in
> [`docs/`](docs/).

---

## How it works

Seven independent DAGs run each night, sequenced by `dag_orchestrator`. Each stage can fail
and retry without corrupting the others — the pipeline is a chain of idempotent writes, not a
monolith.

```
EODHD  (US equities + TSX/TSX-V + VIX/VVIX, one API)
        │
        ▼
dag_stock_ingest        →  raw_prices + ticker_metadata
        │
        ▼
dag_stock_indicators    →  stock_signals + signal_features
        │
        ├─ (Sundays) ─►  dag_stock_relatedness  →  relatedness_matrix + sector_beta
        │
        ▼
dag_llm_analysis        →  llm_analysis + daily_brief
        │
        ▼
dag_outcome_tracker     →  signal_predictions + prediction_outcomes + signal_accuracy
        │
        └─ (Sundays) ─►  dag_parameter_review   →  weekly parameter-health brief
```

| DAG | Schedule | Purpose |
|---|---|---|
| `dag_orchestrator` | 2:05am ET, Sun–Fri | Sequences all sub-DAGs; handles the Sunday branch; owns wind-down |
| `dag_stock_ingest` | Triggered | OHLCV + metadata from EODHD; nightly corporate-actions refresh |
| `dag_stock_indicators` | Triggered | Technical indicators, v2 continuous features, regime classification |
| `dag_stock_relatedness` | Sundays only | Pearson correlation matrix + sector beta |
| `dag_llm_analysis` | Triggered | Algorithmic bias/confidence/key levels; Sonnet daily brief |
| `dag_outcome_tracker` | Triggered | v1 prediction tracking + v2 volatility-scaled outcomes |
| `dag_parameter_review` | Sundays only | Sonnet weekly parameter-health report → human-reviewed proposals |

> The LLM layer is deliberately narrow. `dag_llm_analysis` does **not** call a model per
> ticker — per-name bias, confidence, and key levels are computed algorithmically from
> `stock_signals` and `raw_prices`. The model is called once per run to write the human-facing
> `daily_brief`, and once a week to draft parameter proposals that a human approves or rejects.
> No model output is ever auto-applied.

---

## The v2 rebuild — why the scoring changed

The original v1 signal (below) shipped, ran in production, and tracked its own outcomes. Then
the outcome data was turned on itself. The findings ([`docs/signal_v2_phase0_findings.md`](docs/signal_v2_phase0_findings.md),
[`_phase3_`](docs/signal_v2_phase3_findings.md), [`_event_study`](docs/signal_v2_event_study.md))
retired most of the original thesis:

- **The dead zone capped accuracy mechanically.** A fixed ±1% outcome threshold absorbed
  9–23% of moves into a "no call" band, so accuracy was bounded regardless of signal quality.
  v2 replaced it with volatility- and horizon-scaled thresholds.
- **Overextension does not predict decline.** RSI > 70, far-above-SMA200, and above-upper-band
  all sit exactly on the base rate at 10 days. The one bearish edge with empirical support is
  the *falling knife*: trending-down × deep-below-SMA200 (median excess −3.0% at 21d).
- **The edge lives in states, not crossings.** An 11-event study over 7M ticker-dates showed
  every bullish "event" edge dissolves once compared against a slope-conditioned base rate.
  Continuous distance-to-52-week-high beats the binary breakout. So v2 is a single logistic
  model per horizon over continuous state features — not a bag of crossover signals.

v1 is **frozen at `v1.0`** as the parallel-run baseline; v2 rows coexist in the same tables via
a `signal_version` key, and cutover is gated on v2 beating v1 by ≥3 points at 42 days over 60+
live trading days. Status and the full decision log live in [`docs/roadmap.md`](docs/roadmap.md).

---

## Signal design (v1 baseline)

### Technical indicators

| Indicator | Weight | Logic |
|---|---|---|
| SMA 200 | 30% | Close > SMA200 → bullish. Primary trend filter. |
| SMA 50 | 25% | Close > SMA50 → short-term momentum confirmation. |
| MACD | 25% | MACD line > signal line → bullish momentum. |
| RSI (14) | 20% | < 30 oversold (+1), 30–40 recovering (+0.5), 40–70 neutral (0), > 70 overbought (−1) |
| Bollinger Bands | — | Computed and stored; used as key-level context. |

Sub-signals combine into `composite_score` (−1 to +1), normalised by the weight of present
indicators, then multiplied by the VIX regime multiplier to produce `composite_vix_adj`.

### Volatility overlay

| VIX Level | Regime | Multiplier |
|---|---|---|
| < 15 | `low` | 0.85× |
| 15–20 | `normal` | 1.00× |
| 20–30 | `elevated` | 1.10× |
| 30–40 | `high` | 1.20× |
| > 40 | `extreme` | 0.70× |

VVIX classifies volatility-of-volatility into `complacent` / `clean_fear` / `elevated` /
`chaotic` / `spike`.

---

## Stack

| Layer | Technology |
|---|---|
| Orchestration | Apache Airflow 2.9 (TaskFlow API) — one DAG per pipeline stage |
| Compute | Python 3.11 / 3.12 — pure pandas/numpy runtime math; sklearn only in offline `scripts/` |
| Database | PostgreSQL — RDS on AWS, native-Windows Postgres for local dev |
| Market data | EODHD — US equities, TSX/TSX-V, and VIX/VVIX through a single API |
| LLM | Anthropic Claude — `claude-sonnet-4-6` (brief + review), `claude-haiku-4-5-20251001` (classification) |
| Infrastructure | Docker Compose; AWS EC2 + RDS + Secrets Manager |

---

## Deployment model

The pipeline runs on an AWS instance that is **off most of the day** — so there is no
push-to-deploy. Deployment inverts: `scripts/deploy.sh` runs at every boot, resets the checkout
to `origin/main`, applies safe migrations, and starts the scheduler. When the night's work is
done, the orchestrator triggers wind-down and the box stops itself.

Two properties are worth calling out:

- **Secrets are fetched in-memory, never written to disk.** On the box, `deploy.sh` pulls six
  named secrets from AWS Secrets Manager via the instance role and exports them for the single
  compose invocation. Locally, the same file falls back to `.env`. No code branches — the
  environment differs, not the logic.
- **Two database roles, one per database.** `airflow_app` owns the Airflow metadata DB;
  `signal_app` owns the signal data DB. Role and DB names are structural constants hardcoded
  into the connection strings (collapse-proofing them against misinterpolation); only the two
  passwords are injected. Standalone scripts only ever touch the signal DB, as `signal_app`.

Migrations are boot-applied only when idempotent and non-destructive. A migration whose first
line is `-- MANUAL` (any `DELETE`/`TRUNCATE`/`DROP`) is never auto-applied — it is run by hand
through an SSM tunnel. The marker is grep-checkable; prose is not.

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
├── dag_components/              # Task implementations + pure-math calculations
│   ├── dag_builder.py
│   ├── ingest/                  # tasks.py
│   ├── indicators/              # calculations.py + tasks.py
│   ├── relatedness/             # calculations.py + tasks.py
│   ├── llm/                     # calculations.py + prompt_builder.py + tasks.py
│   ├── outcome_tracker/         # calculations.py + calculations_v2.py + tasks.py
│   └── features/                # calculations.py + tasks.py (v2 feature layer)
├── plugins/                     # Shared library
│   ├── base_client.py           # BaseMarketClient + backoff session (single throttle point)
│   ├── eodhdclient.py           # All market data (US, TSX/TSX-V, indices)
│   └── routing.py               # get_client_for_ticker(), resolve_vix_tickers()
├── config/
│   ├── config.py                # All tunables: weights, thresholds, model names, v2 config
│   ├── watchlist.py             # Loads ticker_universe.json; sector ETFs hardcoded
│   ├── ticker_universe.json     # ~6,983 tickers with exchange + has_data flags
│   └── parameter_overrides.json # Human-approved parameter changes — starts as {}
├── sql/
│   ├── schema.sql               # All CREATE TABLE statements with indexes
│   └── migrations/              # Numbered schema migrations (-- MANUAL = hand-applied)
├── scripts/
│   ├── deploy.sh                # Deploy-on-boot: sync → migrate → start; secrets shim
│   ├── checks.sh                # ruff check + ruff format + pytest --cov (CI runs this file)
│   ├── _db.py                   # signal_app connection for standalone scripts
│   ├── backfill_*.py            # One-shot historical backfills
│   └── optimize_parameters.py   # Offline parameter search
├── docs/                        # Architecture, patterns, roadmap, v2 findings
└── tests/
```

---

## Getting started (local dev)

**Prerequisites:** Docker + Docker Compose, an [EODHD](https://eodhd.com) API key, an
[Anthropic](https://console.anthropic.com) API key, and a PostgreSQL instance the containers
can reach (locally: native-Windows Postgres via `host.docker.internal`).

```bash
git clone https://github.com/ajpoole1/project-signal.git
cd project-signal

cp .env.example .env
# Fill in EODHD_API_KEY and ANTHROPIC_API_KEY.
# Set AIRFLOW_DB_PASSWORD and SIGNAL_DB_PASSWORD to the password(s) of the local
# roles owning the airflow and signal databases (one local superuser for both is
# fine for dev). Leave POSTGRES_HOST unset to use host.docker.internal.

docker compose up -d
```

Airflow UI: `http://localhost:8080` (admin / admin).

Backfill history before enabling the nightly DAGs:

```bash
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_eodhd.py
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_indicators.py
```

Then unpause the DAGs in order: ingest → indicators → relatedness → llm_analysis →
outcome_tracker.

---

## Database schema

Twelve tables in PostgreSQL. All writes are idempotent (`INSERT ... ON CONFLICT DO UPDATE`).

| Table | Key | Contents |
|---|---|---|
| `raw_prices` | `(ticker, date)` | OHLCV daily bars, currency, source |
| `ticker_metadata` | `ticker` | Name, GICS sector/industry, market cap |
| `stock_signals` | `(ticker, date)` | All indicators + VIX/VVIX context + composite scores |
| `signal_features` | `(ticker, date)` | v2 continuous features + regime classification |
| `relatedness_matrix` | `(ticker_a, ticker_b, window_days)` | Pearson r at 90/365d windows |
| `sector_beta` | `(ticker, etf_proxy, window_days)` | Beta vs SPY/QQQ/sector ETFs at 90/365d |
| `llm_analysis` | `(ticker, date)` | Algorithmic bias, confidence, key levels, reasoning |
| `daily_brief` | `date` | Sonnet-generated morning brief |
| `signal_predictions` | `(ticker, signal_date, signal_version)` | Signal snapshot + v1 forward-price outcomes (5/10/20d); v1/v2 coexist |
| `prediction_outcomes` | `(ticker, signal_date, horizon_days)` | v2 long-format: vol-scaled, benchmark-adjusted, episodic |
| `signal_accuracy` | `(signal_version, vix_regime, vol_environment, bias, horizon_days)` | Weekly accuracy rollup |
| `parameter_proposals` | `id` | LLM-drafted proposals pending human review |

---

## CI/CD

| Workflow | Tools |
|---|---|
| `security.yml` | TruffleHog (full git history) + gitleaks |
| `quality.yml` | Ruff lint + format, pytest with coverage on Python 3.11 and 3.12 |
| `docker.yml` | `docker compose config` validated against `.env.example` |

CI runs `scripts/checks.sh` — the same file used locally, so there is no green-here /
red-in-CI drift. `main` is branch-protected; every change lands via `feature/*` → `main` with
a PR and green CI. Merge is always a human action.

---

## Design principles

- **Single data source.** All market data flows through EODHD — no multi-source routing.
- **Idempotent writes.** Every task re-runs safely without duplicating data.
- **Separation of concerns.** Independent DAGs; each fails and retries without corrupting others.
- **Single throttle point.** All API calls go through `base_client.py`. No `sleep()` in DAG or
  task files.
- **Pure-pandas math.** Indicators and features implemented directly — no pandas-ta or
  indicator libraries. sklearn appears only in offline scripts.
- **Empiricism over intuition.** Signal changes are justified by outcome data, not priors —
  see the v2 decision log in [`docs/roadmap.md`](docs/roadmap.md).
- **Logic is public, data is local.** Secrets and personal watchlists never touch the repo.
- **Two human gates.** No model output and no schema change is auto-applied; a human authorizes
  the branch and a human merges it.
```