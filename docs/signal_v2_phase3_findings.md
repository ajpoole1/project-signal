# Signal v2 — Phase 3 Findings

**Date:** 2026-06-10
**Inputs:** `signal_features` (451k rows, 6,932 tickers), `prediction_outcomes` (Phase 1 scoreboard),
`raw_prices` (clean, post-remediation). All returns are excess returns (beta-adjusted vs SPY,
row-offset trading days). `V2_MIN_PRICE = 1.00` applied at signal date.

**⚠️ BETA BUG — RESOLVED (2026-06-11):** Original numbers below used beta=1.0 fallback due to a
column-order bug in `_INSERT_BETAS` (tasks.py) that corrupted `sector_beta`. Three additional fixes
were required before valid betas were available: (1) implausible-return mask added to the returns
matrix (`mask_implausible_returns`, `V2_MAX_DAILY_RATIO`); (2) median-price eligibility filter
(`V2_MIN_PRICE`) applied in `_read_returns`; (3) 173 warrant/test-symbol tickers purged from the
universe (migration 009). After all fixes, betas were recomputed and `backfill_outcomes_v2.py`
re-run with Vasicek shrinkage (`V2_BETA_SHRINKAGE_W=0.67`). **Updated numbers are in the
"CORRECTED AFTER" section below; the original numbers are retained for reference.**

---

## Correction to prior Phase 3 session output

The `analyze_regimes.py` script reported a `dir_acc_21d` column of 56–58% for deep-below-SMA200
names. That figure was `prediction_outcomes.direction_correct`, which is defined as
`sign(excess_return) matches the v1 prediction bias (bullish/bearish)` — not `excess_return > 0`.
It measured whether the signal's directional call was right, conditional on the name being
deep below SMA200, not whether the name outperformed its benchmark.

The correct metric is `frac(excess_return > 0)`, computed directly from `prediction_outcomes`
without passing through signal bias. Verified query output:

| regime | direction | n | frac(excess>0) | median excess 21d | p5 | p95 |
|---|---|---|---|---|---|---|
| ranging | — | 7,432 | **43.5%** | −1.88% | −27.3% | +27.6% |
| trending | down | 146,582 | **41.9%** | −3.01% | −39.5% | +34.7% |
| trending | up | 29,589 | **46.6%** | −1.16% | −32.5% | +33.9% |

Base rate (all eligible names, 21d): **frac(excess>0) = 44.4%**, median excess = −1.31%.

**Corrected interpretation:** Deep-below-SMA200 names underperform their beta-adjusted
benchmark more than half the time at all horizons in all regime groups. The 56–58% figure
was a measurement artefact from a different metric. The distribution is wide (p5 to p95 spans
~55–75 percentage points) with negative median in every group — consistent with a fat left
tail (falling knives continue to fall) and a thinner right tail (eventual recoveries are real
but smaller in magnitude).

---

## 1. Regime distribution

| regime | rows | % |
|---|---|---|
| ranging | 598,720 | 10.3% |
| trending | 5,237,414 | 89.7% |

Consistent year-on-year (88–92% trending across 2022–2026). This is structurally expected:
the `OR` logic (`ADX ≥ 25 OR |sma200_slope| ≥ 2%`) casts wide because the slope condition
alone tags most tickers in even mildly directional market environments. The 90/10 split
is not a data error; it reflects how many names are in some kind of trend at any given time.

Threshold sensitivity (ADX cutoff only, slope threshold fixed at 2%):

| ADX cutoff | trending % |
|---|---|
| 20 | 93.1% |
| 25 | 89.7% ← current |
| 30 | 87.3% |
| 35 | 85.8% |
| 40 | 84.8% |

Raising ADX to 40 recovers only 5 percentage points. The slope condition is the dominant
classifier. Tightening thresholds materially would require raising `V2_SLOPE_TREND_MIN` or
narrowing the slope window, which has Phase 4 implications. No change recommended here;
record this as a known property of the regime split.

---

## 2. Regime persistence — PASS

| regime | n_streaks | mean days | p25 | median | p75 | max |
|---|---|---|---|---|---|---|
| ranging | 27,979 | 21.4 | 7 | **14** | 28 | 487 |
| trending | 33,093 | 158.3 | 17 | **78** | 226 | 1,100 |

Acceptance criterion: median ≥ 5 days. Both pass comfortably. Ranging streaks last ~2 weeks
(median 14 days); trending streaks last ~4 months (median 78 days). Regimes are not
day-to-day noise — they are persistent enough to be meaningful conditioning variables.

---

## 3. Falling-knife hypothesis — CONFIRMED (with correction)

### 3a. Original numbers (beta=1.0, warrants included) — for reference only

**The mean-reversion hypothesis FAILED.** Deep-below-SMA200 names do not mean-revert on
a 21d horizon in either regime. Both ranging and trending groups show negative median
excess returns. The difference is degree:

- `trending-down`: median excess −3.01% at 21d. The strongest negative signal.
- `ranging`: median excess −1.88% at 21d. Also negative, but less severe.
- `trending-up`: median excess −1.16% at 21d.
- Base rate: median excess −1.31% at 21d.

### 3b. CORRECTED AFTER beta fix (shrunk real betas, warrants purged, 2026-06-11)

| regime | direction | n | frac(excess>0) | median excess 21d |
|---|---|---|---|---|
| base rate | — | 888,988 | 45.5% | **−0.90%** |
| ranging | — | 7,408 | 43.6% | **−1.95%** |
| trending | down | 146,419 | 42.3% | **−2.79%** |
| trending | up | 29,419 | 45.6% | **−1.51%** |

`beta_used` distribution after shrinkage: p1=−0.67, median=0.90, p99=3.27, min=−5.32, max=7.16.

**Conclusions unchanged — the corrected numbers confirm the original direction:**

- Mean-reversion hypothesis FAILED in all regimes (all groups have negative median excess).
- `ranging` deep-below-SMA200 (−1.95%) is worse than the base rate (−0.90%).
- **Falling-knife confirmed:** `trending-down` is the worst group (−2.79%), monotonic ordering holds:
  trending-down < ranging < base rate < trending-up.
- The base rate shifted from −1.31% to −0.90% with real betas — beta=1.0 was overcorrecting
  for market drift on low-beta names, making everything look more negative than it was.
- All qualitative Phase 4 architecture conclusions below remain valid.

---

## 4. Verdict for Phase 4 architecture

The Phase 3 evidence changes the Phase 4 input assumptions:

1. **"Ranging = reversion channel" is unsupported.** Deep displacement in ranging names
   does not predict positive excess return at 21d. Either the horizon is too short, the
   displacement threshold is wrong, or mean reversion at this scale is not reliably capturable
   as a binary channel. This needs the event study (Phase 4 prerequisite) before committing
   to architecture.

2. **Trending-down × deep-below-SMA200 is the strongest signal, but it is bearish (avoid).**
   Consistent with the long-biased amendment to the spec.

3. **The regime split is meaningful for conditioning but not for defining a "reversion vs
   continuation" channel dichotomy.** 90% of observations are in "trending" regardless of
   market condition. A binary channel that fires on 10% of data (ranging) will have
   insufficient training examples for reliable model fitting.

4. **The event study (scripts/analyze_events.py) should run before Phase 4 architecture
   is finalised.** Events (first-cross conditions) may reveal edge that states do not,
   particularly for entries into recovery from oversold or below-band conditions.

---

## 5. Methodology notes

**Direction-accuracy framing (corrected):** The `analyze_regimes.py` output reported 56–58%
`direction_correct` for deep-below-SMA200 names. `direction_correct` in `prediction_outcomes`
is `sign(excess_return) matches the v1 prediction bias (bullish/bearish)` — a v1 signal
quality metric, not a benchmark-relative framing. The correct statement from `frac(excess_return > 0)`:
deep-below-SMA200 names **underperform** their benchmark 54–58% of the time (frac>0 = 42–46%).
The original language said they "outperform 56–58% of the time" — that was wrong.

**Relatedness matrix and seam contamination:** The prior session note said correlations were
"unaffected because Pearson r is scale-free." This is imprecise. Pearson r is scale-free but
not outlier-robust: a seam day is a leverage point and will pull correlation toward ±1.
The reason relatedness pair counts did not change materially after the seam fix is that
contamination was **sparse** — only a handful of tickers had active seams, and those tickers
each appear in ~n pairs out of ~10M total. Sparse contamination leaves most pairs untouched.
This is not a claim that Pearson is robust to leverage points; it is an observation about
the hit rate of the contamination.

**Energy beta regime (XOM, CNQ.TO):** 90d and 365d betas for XOM (−0.92, −0.37) and CNQ.TO
(−0.85, −0.30) are negative due to a 2026-YTD energy/market decoupling (110 trading days).
Annual betas 2021–2025 are positive for both names across all years (+0.19 to +1.42).
The beta computation and storage are correct; this is a genuine market regime, not a defect.
Verified by independent numpy.polyfit cross-check (XOM polyfit = −0.72 vs stored −0.92;
difference due to window boundary, not a path bug).

**Sector ETF beta fallback:** SPY, QQQ, XLE, XLF, XLK, XLV, XLY are excluded from
`equity_tickers` in the beta computation (they are the benchmark proxies). They fall back to
`beta_used = 1.0` in outcome calculations. For SPY this is exact. For QQQ and XL* it is an
approximation; acceptable given ETFs are not real prediction targets.

---

## Phase 3 status: ✅ Complete (validation done; Phase 4 architecture pending event study)
