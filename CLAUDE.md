# Project Signal — Claude Code Memory

Nightly stock intelligence pipeline. Stack: Airflow + Postgres + EODHD + Anthropic API.
Portfolio-grade open source project. Logic is public; API keys and personal watchlists are always local-only.

---

## Design Principles — These govern every decision

- **Single data source** — All market data (US equities, TSX/TSX-V, VIX/VVIX) flows through EODHD via `plugins/eodhdclient.py`. No multi-source routing complexity.
- **Idempotent writes** — Every DB write uses `INSERT ... ON CONFLICT DO UPDATE`. Tasks are safe to re-run.
- **Separation of concerns** — Four independent DAGs: ingest → indicators → relatedness → LLM. Each can fail and retry without corrupting the others.
- **Rate limit compliance** — All EODHD calls go through `eodhdclient.py`. The `@rate_limited_call` decorator in `base_client.py` is the single throttling point.
- **Security by design** — Secrets via `.env` only. No hardcoded credentials. No personal data in logs.

---

## Architecture

### DAG Pipeline

| DAG | UTC Schedule | EST | Purpose |
|---|---|---|---|
| `dag_stock_ingest` | `0 5 * * 1-5` | midnight | Fetch OHLCV + VIX/VVIX from EODHD |
| `dag_stock_indicators` | `0 8 * * 1-5` | 3 AM | SMA, MACD, RSI, Bollinger, VIX regime, composite score |
| `dag_stock_relatedness` | `0 10 * * 0` | 5 AM Sun | Pearson correlation matrix, sector beta, peer clusters |
| `dag_llm_analysis` | `0 9 * * 1-5` | 4 AM | Claude API: per-ticker signal analysis |

**Ordering:** Ingest runs first (midnight). Indicators runs 3 hours later (3 AM). EODHD with basic plan: ingest finishes in a few minutes. The 3-hour buffer is comfortable slack.

### Key Design Decisions — Do not revisit without good reason

- **Postgres is the shared layer.** All four DAGs read/write the same schema. No per-DAG side databases.
- **`plugins/` is the shared library.** DAG files import from plugins; DAG files contain no business logic.
- **All config in `config/config.py`.** Signal weights, VIX thresholds, correlation windows, model names — nothing hardcoded in DAG or plugin files.
- **Watchlist in `config/watchlist.py`.** Loads US/TSX tickers from `config/ticker_universe.json` at import time (`has_data=True` only). Sector ETFs are hardcoded separately as Phase 4 beta-proxy infrastructure. Extending the watchlist means editing the JSON — never a DAG file.
- **VIX/VVIX not in watchlist.** Resolved at runtime via `plugins/routing.py:resolve_vix_tickers()` → returns `("VIX.INDX", "VVIX.INDX")`. No format inspection in DAG/task files.
- **Ticker routing in `plugins/routing.py`.** Never inspect ticker format (`.TO`, `.V`, `.INDX`) directly in DAG or task files. Call `get_client_for_ticker()` and `resolve_vix_tickers()` instead.
- **Airflow TaskFlow API only.** Use `@task` decorator for all Python tasks. No classic operators for Python logic.
- **Data source interface.** `EODHDClient` implements `fetch_ohlcv(ticker, start, end)` and `fetch_metadata(ticker)` returning normalized dicts. EODHD symbol mapping: `.TO`/`.V`/`.INDX` passed through; everything else gets `.US` appended. Adjusted close used for `close`; O/H/L scaled by the same factor.
- **No pandas-ta.** All indicator math uses pandas directly (rolling, EWM). No beta library dependencies for core calculations.

---

## File Conventions

```
project-signal/
├── dags/                        # Airflow DAG definitions — orchestration only, no logic
│   ├── dag_stock_ingest.py
│   ├── dag_stock_indicators.py
│   ├── dag_stock_relatedness.py
│   └── dag_llm_analysis.py
├── dag_components/              # Task implementations imported by DAGs
│   ├── dag_builder.py           # OOP DAG constructor (DAGBuilder base + SignalDAG)
│   ├── ingest/
│   │   └── tasks.py             # @task functions for dag_stock_ingest
│   ├── indicators/
│   │   ├── calculations.py      # Pure pandas functions — no Airflow imports
│   │   └── tasks.py             # @task functions for dag_stock_indicators
│   ├── relatedness/
│   │   ├── calculations.py      # Pearson r, beta — pure pandas, no Airflow imports
│   │   └── tasks.py             # @task functions for dag_stock_relatedness
│   └── llm/
│       ├── calculations.py      # Key-level candidates, signal trend — pure functions
│       ├── prompt_builder.py    # System prompt, tool schema, user message builder
│       └── tasks.py             # @task functions for dag_llm_analysis
├── plugins/                     # Shared library: provider clients, routing
│   ├── base_client.py           # BaseMarketClient + rate_limited_call decorator
│   ├── eodhdclient.py           # EODHDClient — all market data (US, TSX/TSX-V, indices)
│   ├── polygon_client.py        # PolygonClient — retained for reference, not active
│   ├── yfinance_client.py       # YFinanceClient — retained for reference, not active
│   └── routing.py               # get_client_for_ticker(), resolve_vix_tickers()
├── config/
│   ├── config.py                # All tunable values: weights, thresholds, model names
│   ├── watchlist.py             # Loads from ticker_universe.json; sector ETFs hardcoded
│   └── ticker_universe.json     # 650-ticker personal universe (has_data flags, exchange labels)
├── sql/
│   ├── schema.sql               # All CREATE TABLE + indexes — run once on fresh Postgres
│   └── migrations/              # ALTER TABLE scripts for live database upgrades
├── scripts/                     # Manual utility scripts — run inside Docker
│   ├── backfill_eodhd.py        # Full OHLCV history backfill via EODHD (5 years)
│   └── backfill_indicators.py   # Full indicator backfill over raw_prices history
└── tests/                       # One test file per plugin/component module
    ├── test_eodhd_client.py
    ├── test_polygon_client.py
    ├── test_yfinance_client.py
    ├── test_indicators.py
    ├── test_relatedness.py
    └── test_llm_analysis.py
```

**Every plugin and dag_components module must have a corresponding test file.**

---

## Ticker Universe

- **Source of truth:** `config/ticker_universe.json` — 650 tickers across 21 categories
- **Active universe:** 616 tickers with `has_data=True` (527 US + 85 TSX + 4 TSX-V)
- **Sector ETFs:** 7 hardcoded in `watchlist.py` (SPY, QQQ, XLK, XLF, XLE, XLV, XLY) — Phase 4 beta-proxy infrastructure, not in JSON
- **Total ingest load:** 625 tickers per night (623 equity/ETF + VIX.INDX + VVIX.INDX)
- **Routing:** All tickers → EODHD. Symbol mapping: `.TO`/`.V`/`.INDX` pass-through; everything else → `.US` suffix.

`get_all_tickers()` → 623 tickers (used by ingest + indicators)
`get_equity_tickers()` → 616 tickers (used by relatedness — excludes sector ETFs)

---

## Database Rules

- All tables defined in `sql/schema.sql`. Never create tables in Python code.
- All writes: `INSERT ... ON CONFLICT (ticker, date) DO UPDATE SET ...`
- Never `TRUNCATE` or `DELETE` in DAG tasks. Soft deletes only (update `computed_at`, let queries filter by recency).
- All DB connections via Airflow Postgres hook in DAG tasks. No hardcoded connection strings.
- `scripts/` are standalone utilities — they connect directly via psycopg2, hardcode `dbname="signal"`, and read credentials from `.env`.
- Primary keys: `(ticker, date)` on all time-series tables.

**Backfill commands (run inside Docker, not from WSL directly):**
```bash
# Full OHLCV history (5 years by default, or pass --start YYYY-MM-DD)
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_eodhd.py

# Full indicator history (reads raw_prices, writes stock_signals)
docker compose exec airflow-scheduler python /opt/airflow/scripts/backfill_indicators.py
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

- `base_client.py` owns all throttling. The `@rate_limited_call` decorator is the only place `time.sleep()` is called for rate-limiting.
- EODHD basic plan: 100K API calls/day — no per-minute rate limit concern at 625 tickers/night.
- Retry strategy: exponential backoff, `backoff_factor=2`, retries on 429/500/502/503/504, respects `Retry-After` header.

---

## Security Rules

- **Never hardcode secrets.** All credentials via `.env` (local) or environment variables.
- **Never commit:** `.env`, `data/`, `logs/`, any `.db` file, personal watchlist extensions
- **Always commit:** `.env.example` with placeholder values
- **Before any PR to main:** verify no personal data or API keys are staged

---

## EODHD Endpoint Reference

| Data | Endpoint |
|---|---|
| OHLCV daily bars | `GET /api/eod/{TICKER}.{EXCHANGE}?api_token=...&fmt=json&from=YYYY-MM-DD&to=YYYY-MM-DD&period=d` |
| Ticker fundamentals/metadata | `GET /api/fundamentals/{TICKER}.{EXCHANGE}?api_token=...&filter=General` |
| VIX | `VIX.INDX` |
| VVIX | `VVIX.INDX` |

EODHD returns `adjusted_close` for the close field. O/H/L are scaled by `adjusted_close / close` to maintain consistency.

---

## Phase Status

| Phase | Status | Goal |
|---|---|---|
| Phase 1 | ✅ Complete | Foundation: `schema.sql`, `config.py`, `watchlist.py` |
| Phase 2 | ✅ Complete | Ingest DAG live, `raw_prices` populating via EODHD (US + TSX/TSX-V + VIX/VVIX) |
| Phase 3 | ✅ Complete | Indicators DAG live, `stock_signals` populating. Full 5-year backfill complete. |
| Phase 4 | ✅ Complete | Relatedness DAG live, `relatedness_matrix` + `sector_beta` populating |
| Phase 5 | 🔄 Implemented | LLM Analysis DAG implemented, `llm_analysis` table. Pending prod validation. |
| Phase 6 | 🔲 Not started | Jarvis integration: daily brief endpoint, alert triggers |

Update this table as phases complete.

---

## Phase 5 Context (LLM Analysis)

**Goal:** `dag_llm_analysis` — reads `stock_signals` + `raw_prices` + `relatedness_matrix`, writes `llm_analysis`.

**Key design decisions:**
- Haiku for per-ticker analysis (cost), Sonnet reserved for future briefing generation
- Forced structured output via `tool_choice={"type": "tool", "name": "record_analysis"}` — no free-text parsing
- Prompt caching on the static system block (saves ~60% of input tokens at scale)
- Option C key levels: 8 rule-based candidates pre-computed (sma_50/200, bb_upper/lower, 52w/20d high/low from raw_prices); LLM only assigns role/significance/note — prices never LLM-generated
- `LLM_SIGNAL_THRESHOLD = 0.5` in config.py — only tickers above this `abs(composite_vix_adj)` are analyzed. Raised from 0.3 once the 5-year backfill populated SMA-200 for all tickers.
- `llm_analysis` stores output as JSONB (`analysis` column) — flexible schema for future expansion

**Output schema (per ticker):**
```json
{
  "bias": "bullish|bearish|neutral",
  "confidence": 0.0-1.0,
  "key_levels": [{"price": float, "type": str, "role": "support|resistance|neutral", "significance": "high|medium|low", "note": str}],
  "reasoning": "string"
}
```

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
