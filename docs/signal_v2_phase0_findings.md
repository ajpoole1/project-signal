# Signal v2 — Phase 0 Findings

**Date:** 2026-06-09
**Inputs:** Phase 0 diagnostic queries (spec §0.1–0.6) + data-quality follow-up queries, run against live `signal` DB.
**Population:** ~961k resolved v1 predictions (512k bullish / 449k bearish), 5/10/20d horizons, full history.

---

## Headline conclusions

1. **The ±1% fixed threshold was the dominant cause of sub-45% accuracy** — a measurement artifact, not signal failure. Bullish direction-only accuracy is 49.7–51.2% vs ~42% under the threshold metric; the dead zone (|move| ≤ 1%) absorbed 9–23% of all outcomes and counted every one as a miss.
2. **The underlying v1 composite carries ~zero directional information.** Bullish edge over base rate: < +1pt. Bearish: ~−2pts (mildly anti-predictive). Inversion does not help — flipped accuracy is below original everywhere. The composite points nowhere, not backwards. Phase 4 is a rebuild from features, not a remix.
3. **The contrarian half of the thesis is supported; the sell-side half is not.** RSI oversold is the strongest standalone condition (+1.6pts over base on n=75k). All three "extended" conditions (RSI > 70, >15% above SMA200, above upper band) sit exactly on base rate — overextension does not predict decline at 10d. **Recommendation: v2 is long-biased.** Bullish channel makes predictions; "extended" becomes a take-profit/avoid flag on held names, not a bearish forecast.
4. **Three data-quality defects found and diagnosed** (see addendum): an adjusted-price seam bug in incremental ingest, warrants/test tickers in the universe, and minor yfinance residue (31 tickers). All fixable; remediation is a Phase 1 prerequisite.
5. **Effective sample size is ~10× smaller than row counts** (512k/449k rows → 47.6k/46.1k episodes). Episode-head weighting in training and calibration is confirmed necessary.

---

## 0.1 Three-way outcome breakdown

| bias | h | n | up >1% | down >1% | dead zone | direction-only acc |
|---|---|---|---|---|---|---|
| bearish | 5 | 448,925 | 41.3% | 41.3% | 17.4% | 48.1% |
| bearish | 10 | 448,925 | 44.5% | 43.0% | 12.6% | 47.8% |
| bearish | 20 | 448,925 | 45.7% | 45.3% | 9.0% | 48.6% |
| bullish | 5 | 512,026 | 38.9% | 38.4% | 22.7% | 49.7% |
| bullish | 10 | 512,026 | 42.4% | 40.9% | 16.7% | 50.6% |
| bullish | 20 | 512,026 | 45.1% | 43.1% | 11.9% | 51.2% |

**Interpretation.** The dead zone mechanically capped threshold accuracy: at 5d, 17–23% of outcomes could not be scored correct for either bias. Direction-only accuracy recovers 8–9 points, landing near coin-flip. Bearish-signaled names go *up* slightly more often than down at every horizon — bearish signals select names that subsequently rise.

**Verdict — threshold redesign: GO.** Volatility-scaled, horizon-scaled thresholds (spec §1) are required; fixed ±1% is invalid at every horizon and degenerate at long ones.

## 0.2 Base rates

| h | n | up >1% | down >1% | up (any) |
|---|---|---|---|---|
| 5 | 960,951 | 40.0% | 39.8% | 49.2% |
| 10 | 960,951 | 43.3% | 41.9% | 50.2% |
| 20 | 960,951 | 45.4% | 44.1% | 50.4% |

(`avg_return` from this query is excluded — contaminated by the data defects in the addendum; medians in the addendum are the trustworthy central-tendency figures.)

**Interpretation.** "Always bullish on anything that fired a signal" scores ~50%. Every v2 metric is measured as edge over this row, never over an assumed 50%.

## 0.3 Inversion check

Inverted accuracy (bearish→bullish, bullish→bearish, threshold metric): 38.4–45.7%, below the originals at every bias × horizon.

**Verdict — inversion: NO-GO.** There is no free edge from flipping. Consistent with §0.1: the score is uninformative, not inverted.

## 0.4 Per-indicator standalone hit rates (10d, direction)

| condition | n | frac up 10d | edge vs base (50.2%) |
|---|---|---|---|
| rsi_oversold (<30) | 75,497 | **51.8%** | **+1.6** |
| above_sma200 | 481,128 | 50.8% | +0.6 |
| macd_bullish_state | 512,110 | 50.6% | +0.4 |
| far_above_sma200 (>15%) | 247,655 | 50.2% | 0.0 |
| below_sma200 | 417,223 | 50.1% | −0.1 |
| rsi_overbought (>70) | 114,303 | 50.1% | −0.1 |
| above_bb_upper | 95,350 | 50.0% | −0.2 |
| below_bb_lower | 62,685 | 49.6% | −0.6 |
| deep_below_sma200 (>15%) | 222,474 | 48.3% | −1.9 |

(avg_ret column excluded — contaminated; re-run post-remediation with median + winsorized mean.)

**Interpretation.**
- **RSI oversold is the strongest single signal** and it is contrarian — the value-buy thesis has empirical support.
- **No overextension condition predicts decline.** The sell-side thesis (out-of-norm highs → forthcoming drop) is unsupported at 10d.
- **deep_below_sma200 at 48.3%** is the only meaningfully *negative* condition: falling knives keep falling, on average. Hypothesis for Phase 3: this is regime-dependent — reverts in `ranging`, continues in `trending-down`. If confirmed, it is the one legitimate bearish niche.
- Trend-state conditions carry weak positive edge (+0.4 to +0.6) — usable as conditioning features, not as standalone signals.

**Verdict — indicator keep/drop:** keep RSI (continuous, contrarian framing), SMA distances (continuous, regime-conditioned), MACD histogram (weak conditioning feature), Bollinger %B (re-evaluate post-remediation; below_bb_lower's −0.6 may be microcap noise). Drop nothing yet; let the Phase 4 fit assign weights from clean data.

## 0.5 Confidence calibration curve

Decile-level curve confirms the coarse table: frac_up_10d oscillates 46.6–54.2% with no monotonic relationship to confidence for either bias. (avg_ret column contaminated.)

**Verdict:** v1 confidence is a dead feature. Phase 5's empirical bucket calibration replaces it; the success criterion stands — the v2 OOS confidence curve must be monotonic.

## 0.6 Episode inflation

| bias | raw rows | episodes | inflation |
|---|---|---|---|
| bullish | 512,026 | 47,598 | 10.8× |
| bearish | 449,295 | 46,147 | 9.7× |

**Interpretation.** ~47k independent episodes per bias — ample for training, but all row-level error bars to date were ~3× optimistic. Episode-head weighting (spec §4.2) and episode-head-only rollup variants (spec §1.4) confirmed necessary.

---

## Addendum — Data-quality findings (remediation is a Phase 1 prerequisite)

### A. Adjusted-price seam bug (severity: high — corrupts returns)

Top return outliers are dominated by serial reverse-splitters (PPCB, TBLT, MRIN, IVP) showing impossible returns (e.g., $0.0004 → $10.00 in 10 trading days, all `source='eodhd'`).

**Mechanism:** nightly ingest stores each day's `adjusted_close` *as of fetch night*. Adjusted prices are valid only relative to a point in time; every later split/dividend retroactively changes the adjustment basis for all history. Rows ingested before a corporate action remain in the old basis while rows after it are in the new basis. Any return computed across the seam is garbage. Bulk backfills are internally consistent (single fetch epoch); the defect accumulates in incrementally-ingested history for any ticker with corporate actions since the last full backfill. EODHD's data is correct; the storage model is the defect.

**Remediation (one-time):** full `backfill_eodhd.py` re-run resets all tickers to a single adjustment epoch (~7k API calls, idempotent upserts). Then re-run `backfill_indicators.py` and the prediction backfills — all downstream values computed across seams are invalid.

**Remediation (permanent, → Phase 1 scope):**
1. Nightly corporate-actions task: poll EODHD splits/dividends endpoints; for any ticker with a new action, re-fetch its full OHLCV history (re-basing all stored rows).
2. Sanity guard in the v2 outcome resolver: quarantine (do not resolve) any prediction whose price window contains a 1-day ratio beyond `V2_MAX_DAILY_RATIO` (proposed: 3.0) pending the corporate-actions refresh.

### B. Universe contamination (severity: medium)

- **Nasdaq test symbols** present (ZWZZT observed; check ZVZZT/ZXZZT and the full test-symbol family). These are fake instruments.
- **US-style warrants** present (WLDSW): bare `W`-suffix warrants pass `fetch_exchange_listings.py`'s `_SKIP_SUFFIXES` filter, which only catches dotted suffixes (`.WT`, `.WS`, …).

**Remediation:** flag both classes `has_data=False` in `ticker_universe.json`; patch `fetch_exchange_listings.py` to reject Nasdaq test symbols by explicit list and to flag bare-W-suffix codes whose root ticker also exists in the listing (warrant heuristic) for exclusion.

### C. yfinance residue (severity: low)

31 tickers carry mixed `source` rows — pre-migration `backfill_history.py` leftovers. The full re-backfill in (A) overwrites everything EODHD serves; afterward, `DELETE FROM raw_prices WHERE source = 'yfinance'` removes stragglers (anything remaining is data EODHD cannot replace and should not be kept).

### D. Price-bucket distribution (justifies `V2_MIN_PRICE`)

| bucket | n | median ret 10d | notes |
|---|---|---|---|
| < $0.10 | 15,929 | 0.0000 | untradeable; mean destroyed by seams + microcap ticks |
| $0.10–1 | 44,325 | 0.0000 | same character |
| $1–5 | 178,046 | −0.53% | believable small-cap bleed |
| ≥ $5 | 722,651 | +0.16% | sane (~4%/yr drift); residual outliers are seam artifacts (ZWZZT et al.) |

**Remediation:** `V2_MIN_PRICE = 1.00` (USD/CAD nominal) applied at signal date for v2 prediction eligibility; consider a minimum dollar-volume filter alongside. All v2 rollups report median and winsorized mean (1%/99%), never raw means.

---

## Resulting spec amendments (to fold into `docs/signal_v2_rebuild.md`)

1. **Phase 1 gains a "Data hygiene" subsection (prerequisite, runs first):** universe scrub (B), one-time full re-backfill + yfinance purge (A/C), downstream backfill re-runs, corporate-actions refresh task, `V2_MAX_DAILY_RATIO` quarantine guard, `V2_MIN_PRICE` filter, robust-stats reporting.
2. **Phase 4 architecture: long-biased.** Bullish channel is the predictive engine. Bearish output is reframed as a calibrated "extended / reduce / avoid" flag unless Phase 3's regime split confirms the falling-knife niche (`deep_below_sma200` × `trending-down`) as genuinely predictive — the only bearish hypothesis with supporting evidence.
3. **Phase 0.4 avg-return columns to be re-run post-remediation** (median + winsorized) before final feature pruning.

## Phase 0 status: ✅ Complete

---

## Methodology note — market-adjusted returns (appended post-Phase 1)

All return metrics in Phase 0 are **raw returns** (`(future_price - price_at_signal) / price_at_signal`), evaluated against a fixed ±1% threshold. This is the v1 scoreboard.

Phase 1 replaced this with **excess returns**: `raw_return − beta × benchmark_return`, where benchmark is SPY over the same row-offset trading-day window and beta is sourced from `sector_beta` (SPY proxy, 90-day window, fallback 1.0). The volatility-scaled threshold (`k * sigma_daily * sqrt(h)`, stored as `threshold_used` in `prediction_outcomes`) replaces the fixed ±1%.

Consequence for Phase 0 tables: direction-only accuracy figures (§0.1) are directionally correct but measured on raw returns; the excess-return equivalents are in `docs/signal_v2_phase3_findings.md`. The base-rate numbers in §0.2 are raw-return base rates; excess-return base rates will differ due to benchmark adjustment removing market-drift. All Phase 0 conclusions about the *relative* standing of conditions (RSI oversold best, overextension flat) are expected to survive re-measurement on excess returns, since SPY adjustment is symmetric across conditions.

Phase 3 findings are based on excess returns throughout. See `docs/signal_v2_phase3_findings.md` header for the full methodology statement.
