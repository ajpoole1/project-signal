# Reference — Project Signal

---

## EODHD API endpoints

All market data flows through `plugins/eodhdclient.py`. Base URL: `https://eodhd.com/api`.

| Endpoint | Method | Purpose | Used by |
|---|---|---|---|
| `/eod/{symbol}` | GET | Daily OHLCV bars (adjusted); `period=d`, `fmt=json` | `EODHDClient.fetch_ohlcv()` |
| `/splits/{symbol}` | GET | Stock splits on/after `from` date; returns `[{date, split}]` | `EODHDClient.fetch_splits()` |
| `/div/{symbol}` | GET | Dividends on/after `from` date; returns `[{date, value}]` | `EODHDClient.fetch_dividends()` |
| `/fundamentals/{symbol}` | GET | Company metadata; `filter=General` | `EODHDClient.fetch_metadata()` |

### Symbol format

| Ticker type | Internal format | EODHD symbol | Example |
|---|---|---|---|
| US equity / ETF | `AAPL` | `AAPL.US` | `AAPL.US` |
| TSX equity | `TD.TO` | `TD.TO` (pass-through) | `TD.TO` |
| TSX Venture | `CANN.V` | `CANN.V` (pass-through) | `CANN.V` |
| VIX index | `VIX.INDX` | `VIX.INDX` (pass-through) | `VIX.INDX` |
| VVIX index | `VVIX.INDX` | `VVIX.INDX` (pass-through) | `VVIX.INDX` |

Tickers with `.TO`, `.V`, or `.INDX` suffixes are passed through unchanged. All others get `.US` appended (`EODHDClient._eodhd_symbol()`).

### Adjusted-price convention

EODHD returns both `close` (unadjusted) and `adjusted_close` (split/dividend adjusted). `fetch_ohlcv` stores `adjusted_close` as the `close` field and scales O/H/L by the same factor (`adj_close / raw_close`), matching `yfinance auto_adjust=True` behaviour. **Stored prices are valid only relative to the fetch epoch** — see the adjusted-price epoch problem in `docs/architecture.md`.

### Rate limiting and backoff

`BaseMarketClient._get_session()` pre-configures exponential backoff on HTTP 429, 500, 502, 503, 504 (Retry: total=5, backoff_factor=2, `respect_retry_after_header=True`). No manual `sleep()` calls in any DAG or task file. The `rate_limited_call` decorator in `base_client.py` is wired only to the inactive `PolygonClient`.

EODHD's EOD API rate limits depend on the plan tier. The pipeline makes one request per ticker per nightly ingest run. At 6,983 tickers this is ~7k requests per night spread across the ingest window — well within any paid plan.

---

## Model selection

| Model | Config constant | Used for | Max tokens |
|---|---|---|---|
| `claude-sonnet-4-6` | `ANTHROPIC_MODEL_ANALYSIS` | Daily brief generation; weekly parameter review | 500 (brief), default (review) |
| `claude-haiku-4-5-20251001` | `ANTHROPIC_MODEL_CLASSIFICATION` | Parameter proposal rationale generation | 80 |

Model constants are in `config/config.py:109-110`. Change them there to switch model versions; the change takes effect on the next scheduler restart.

---

## Ticker universe stats

Computed from `config/ticker_universe.json` as of 2026-06-10.

| Exchange | `has_data=True` | Notes |
|---|---|---|
| US (`exchange = "us"`) | 6,037 | NYSE, NASDAQ, AMEX equities and ETFs |
| TSX (`exchange = "tsx"`) | 907 | Toronto Stock Exchange |
| TSX Venture (`exchange = "tsx_venture"`) | 5 | TSX Venture Exchange |
| **Total active** | **6,949** | Loaded by `watchlist.py` |
| `has_data=False` (flagged) | 34 | Includes test symbols (ZWZZT, ZVZZT, ZXZZT) and warrants (WLDSW); flagged per Phase 0 remediation |
| **Total in universe file** | **6,983** | |

**Note:** The universe was scrubbed in June 2026 as part of Phase 0 / Phase 1 remediation. Prior to that it was ~650 tickers. The expansion to ~7k was done in a separate feature branch and merged before the v2 rebuild.

`config/watchlist.py` loads `has_data=True` tickers only. Sector ETFs (SPY, QQQ, XLK, XLF, XLE, XLV, XLY) are hardcoded in `watchlist.py` and not in the JSON universe — they are beta proxy infrastructure.

### Universe categories

The JSON file contains a `categories` dict mapping named groups to ticker lists. Categories include: `added_2026`, `automotive`, `chemicals_energy`, `china`, `construction`, and others. Categories are metadata only — `watchlist.py` does not use them; the active universe is all `has_data=True` tickers regardless of category.

### Personal extensions

Add personal or sensitive tickers in `config/watchlist_personal.py` (gitignored). Define `US_TICKERS_EXTRA` and/or `TSX_TICKERS_EXTRA` lists there. `get_all_tickers()` and `get_equity_tickers()` append them automatically.

---

## Config constants quick reference

All in `config/config.py`.

### Indicator windows

| Constant | Value | Used for |
|---|---|---|
| `SMA_SHORT_WINDOW` | 50 | SMA50 |
| `SMA_LONG_WINDOW` | 200 | SMA200 |
| `MACD_FAST` | 12 | MACD fast EMA |
| `MACD_SLOW` | 26 | MACD slow EMA |
| `MACD_SIGNAL` | 9 | MACD signal EMA |
| `RSI_WINDOW` | 14 | RSI Wilder smoothing |
| `BB_WINDOW` | 20 | Bollinger Bands |
| `BB_STD` | 2 | Bollinger Bands std multiplier |
| `VIX_SMA_WINDOW` | 20 | VIX trend SMA |
| `PRICE_HISTORY_DAYS` | 250 | Indicator lookback (+ 30 buffer in tasks) |
| `RELATEDNESS_HISTORY_DAYS` | 400 | Correlation/beta lookback |

### VIX regime bands

| Upper bound | Label | Composite multiplier |
|---|---|---|
| < 15 | `low` | 0.85× |
| 15–20 | `normal` | 1.00× |
| 20–30 | `elevated` | 1.10× |
| 30–40 | `high` | 1.20× |
| > 40 | `extreme` | 0.70× |

VIX trend (ratio of VIX to 20-day SMA): > 1.20 → `expanding`; < 0.85 → `contracting`; else → `stable`.

### VVIX environment thresholds

| Threshold | Label |
|---|---|
| < 85 | `complacent` |
| 85–100 | `clean_fear` |
| 100–115 | `elevated` |
| 115–120 | `chaotic` |
| ≥ 120 | `spike` |

### Signal v2 key constants

| Constant | Value | Purpose |
|---|---|---|
| `V2_PREDICTION_HORIZONS` | [5, 10, 20, 21, 42, 63] | All resolution horizons (trading days) |
| `V2_PRIMARY_HORIZON` | 42 | Primary training/calibration target |
| `V2_VOL_LOOKBACK_DAYS` | 20 | Trailing vol window |
| `V2_THRESHOLD_VOL_MULT` | 0.5 | `k` in `k * sigma_daily * sqrt(h)` |
| `V2_BENCHMARK_TICKER` | `"SPY"` | Benchmark for excess-return calculation |
| `V2_BETA_WINDOW` | 90 | Which `sector_beta` window to use for beta lookup |
| `V2_EPISODE_GAP_DAYS` | 5 | Trading-day gap that starts a new episode |
| `V2_MAX_DAILY_RATIO` | 3.0 | Quarantine guard: 1-day close ratio above this flags a corp-action seam |
| `V2_MIN_PRICE` | 1.00 | USD/CAD nominal floor for v2 eligibility |
| `SIGNAL_VERSION_V2` | `"v2.0"` | Version tag for v2 predictions |
