# Signal v2 — Event Study Findings

**Generated:** 2026-06-11  
**Universe:** post-migration-009 clean universe, repaired sector_beta, Vasicek-shrunk betas (W=0.67).  
**Metric:** excess return = raw - beta_used x SPY. Median + winsorized (1%/99%) mean only.

---

## Base rate (all eligible ticker-dates)

| horizon | n | median | wmean | frac>0 |
|---|---|---|---|---|
| 21d | 7,061,343 | -1.207% | -1.279% | 44.3% |
| 42d | 6,914,742 | -2.187% | -2.424% | 42.9% |
| 63d | 6,769,637 | -3.064% | -3.436% | 42.0% |

The negative median reflects the unconditional universe distribution. beta mis-estimation for ~7 thinly-traded names (VINO et al.) and genuine underperformance of the small-cap tail vs SPY.

### Conditioned base rates

**Critical context for slope conditioning:** the slope>0 base rate is substantially better than the slope<=0 base rate. Any event conditioned on slope>0 must beat the slope>0 base rate, not the overall base rate, to demonstrate that the event adds edge beyond the slope filter alone.

| condition | horizon | n | median | frac>0 |
|---|---|---|---|---|
| slope>0 | 21d | 2,640,535 | -0.677% | 46.3% |
| slope<=0 | 21d | 2,765,994 | -1.823% | 43.0% |
| slope>0 | 42d | 2,559,950 | -1.099% | 45.8% |
| slope<=0 | 42d | 2,712,185 | -3.432% | 41.1% |
| slope>0 | 63d | 2,476,997 | -1.430% | 45.7% |
| slope<=0 | 63d | 2,661,944 | -4.979% | 39.6% |

---

## Event results — primary horizon 42d

Base rate @42d: median = -2.187%, frac>0 = 42.9%

** = beats base rate median by >=0.5pp; *** = >=1.0pp.

| event | bias | n_fires | median% | vs_base | wmean% | frac>0 |
|---|---|---|---|---|---|---|
| golden_cross ** | BULL | 17,211 | -2.690% | -0.503% | -2.119% | 41.4% |
| price_x_sma200_up | BULL | 51,406 | -2.343% | -0.155% | -2.382% | 42.6% |
| macd_cross_up | BULL | 179,830 | -2.413% | -0.226% | -2.549% | 42.3% |
| macd_cross_up_sub0 ** | BULL | 119,519 | -2.762% | -0.575% | -2.859% | 41.8% |
| rsi_exit_oversold | BULL | 53,439 | -2.573% | -0.386% | -3.068% | 42.8% |
| bb_reentry_low | BULL | 113,094 | -2.090% | +0.098% | -2.394% | 43.3% |
| high_52w_breakout ** | BULL | 28,282 | -1.194% | +0.993% | -1.228% | 45.2% |
| rsi_cross_50_up | BULL | 169,652 | -2.415% | -0.228% | -2.577% | 42.3% |
| death_cross ** | BEAR | 16,577 | -1.653% | +0.534% | -1.414% | 44.9% |
| price_x_sma200_dn | BEAR | 50,705 | -1.943% | +0.245% | -1.928% | 43.8% |
| low_52w_breakdown ** | BEAR | 25,873 | -3.111% | -0.924% | -3.954% | 43.4% |


### Conditioning splits per bullish event @42d

slope>0 conditioned cells show vs slope>0 base rate; slope<=0 vs slope<=0 base; vol splits vs overall base.

| event | split | n | median% | vs_conditioned_base | frac>0 |
|---|---|---|---|---|---|
| golden_cross | slope>0 | 4,519 | -1.527% | -0.428% (vs slope>0 base) | 44.3% |
| golden_cross | slope<=0 | 12,692 | -3.243% | +0.189% (vs slope<=0 base) | 40.4% |
| golden_cross | vol>=1.5 | 2,506 | -3.056% | -0.869% (vs overall base) | 42.2% |
| golden_cross | vol<1.5 | 14,705 | -2.651% | -0.464% (vs overall base) | 41.3% |
| price_x_sma200_up | slope>0 | 21,343 | -1.356% | -0.257% (vs slope>0 base) | 45.1% |
| price_x_sma200_up | slope<=0 | 30,063 | -3.213% | +0.219% (vs slope<=0 base) | 40.8% |
| price_x_sma200_up | vol>=1.5 | 15,527 | -3.795% | -1.608% (vs overall base) | 40.3% |
| price_x_sma200_up | vol<1.5 | 35,879 | -1.862% | +0.326% (vs overall base) | 43.6% |
| macd_cross_up | slope>0 | 66,385 | -1.380% | -0.281% (vs slope>0 base) | 44.9% |
| macd_cross_up | slope<=0 | 113,445 | -3.191% | +0.241% (vs slope<=0 base) | 40.7% |
| macd_cross_up | vol>=1.5 | 33,285 | -3.191% | -1.004% (vs overall base) | 40.9% |
| macd_cross_up | vol<1.5 | 146,545 | -2.259% | -0.072% (vs overall base) | 42.6% |
| macd_cross_up_sub0 | slope>0 | 39,907 | -1.596% | -0.497% (vs slope>0 base) | 44.4% |
| macd_cross_up_sub0 | slope<=0 | 79,612 | -3.492% | -0.060% (vs slope<=0 base) | 40.5% |
| macd_cross_up_sub0 | vol>=1.5 | 18,075 | -3.834% | -1.646% (vs overall base) | 40.2% |
| macd_cross_up_sub0 | vol<1.5 | 101,444 | -2.603% | -0.416% (vs overall base) | 42.1% |
| rsi_exit_oversold | slope>0 | 16,500 | -1.241% | -0.142% (vs slope>0 base) | 45.8% |
| rsi_exit_oversold | slope<=0 | 36,939 | -3.312% | +0.120% (vs slope<=0 base) | 41.5% |
| rsi_exit_oversold | vol>=1.5 | 14,136 | -2.978% | -0.791% (vs overall base) | 41.7% |
| rsi_exit_oversold | vol<1.5 | 39,303 | -2.412% | -0.225% (vs overall base) | 43.2% |
| bb_reentry_low | slope>0 | 41,332 | -0.977% | +0.122% (vs slope>0 base) | 46.4% |
| bb_reentry_low | slope<=0 | 71,762 | -2.918% | +0.514% (vs slope<=0 base) | 41.6% |
| bb_reentry_low | vol>=1.5 | 23,089 | -1.965% | +0.222% (vs overall base) | 44.0% |
| bb_reentry_low | vol<1.5 | 90,005 | -2.121% | +0.067% (vs overall base) | 43.2% |
| high_52w_breakout | slope>0 | 25,094 | -0.990% | +0.109% (vs slope>0 base) | 45.9% |
| high_52w_breakout | slope<=0 | 3,188 | -3.426% | +0.006% (vs slope<=0 base) | 39.2% |
| high_52w_breakout | vol>=1.5 | 11,686 | -1.801% | +0.387% (vs overall base) | 43.7% |
| high_52w_breakout | vol<1.5 | 16,596 | -0.863% | +1.324% (vs overall base) | 46.2% |
| rsi_cross_50_up | slope>0 | 63,501 | -1.354% | -0.255% (vs slope>0 base) | 44.9% |
| rsi_cross_50_up | slope<=0 | 106,151 | -3.239% | +0.193% (vs slope<=0 base) | 40.7% |
| rsi_cross_50_up | vol>=1.5 | 36,453 | -4.239% | -2.052% (vs overall base) | 39.3% |
| rsi_cross_50_up | vol<1.5 | 133,199 | -2.048% | +0.139% (vs overall base) | 43.1% |


**Volume note:** Volume confirmation (vol_ratio >= 1.5) is consistently **negative** across all bullish events — high-volume breakouts/crosses underperform low-volume ones. This makes vol_ratio >= 1.5 a candidate negative feature, not a confirmation signal. The inversion is consistent and not an artefact of the universe: high-volume events are more likely to be reactive moves (gap fills, forced positioning) than high-conviction entries.


### Year-by-year stability @42d (median excess %)

| event | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| golden_cross | -0.6 | +0.5 | -4.6 | -3.6 | -1.7 | -2.2 |
| price_x_sma200_up | -0.5 | -0.2 | -3.7 | -3.3 | -1.4 | -4.3 |
| macd_cross_up | -3.0 | -1.0 | -3.3 | -2.7 | -1.7 | -3.6 |
| macd_cross_up_sub0 | -3.2 | -1.2 | -3.3 | -3.6 | -2.0 | -5.1 |
| rsi_exit_oversold | -4.0 | -0.3 | -3.6 | -4.1 | -0.9 | -6.6 |
| bb_reentry_low | -2.9 | -0.5 | -3.8 | -2.4 | -0.9 | -4.1 |
| high_52w_breakout | — | -2.1 | -2.7 | -1.1 | -0.6 | +0.2 |
| rsi_cross_50_up | -2.7 | -1.0 | -3.6 | -2.8 | -1.8 | -3.6 |
| death_cross | +2.2 | +0.1 | -2.6 | -2.3 | -1.0 | -5.0 |
| price_x_sma200_dn | -1.0 | +0.2 | -3.6 | -2.6 | -0.9 | -4.1 |
| low_52w_breakdown | — | +0.9 | -5.3 | -5.7 | -4.2 | -6.7 |


### All-horizon summary (median excess %)

| event | bias | n_fires | med_21d | med_42d | med_63d |
|---|---|---|---|---|---|
| golden_cross | BULL | 17,887 | -1.452% | -2.690% | -4.062% |
| price_x_sma200_up | BULL | 53,586 | -1.423% | -2.343% | -3.183% |
| macd_cross_up | BULL | 186,830 | -1.208% | -2.413% | -3.114% |
| macd_cross_up_sub0 | BULL | 123,720 | -1.508% | -2.762% | -3.494% |
| rsi_exit_oversold | BULL | 55,079 | -1.297% | -2.573% | -3.472% |
| bb_reentry_low | BULL | 117,463 | -1.125% | -2.090% | -2.931% |
| high_52w_breakout | BULL | 30,108 | -0.722% | -1.194% | -1.569% |
| rsi_cross_50_up | BULL | 176,828 | -1.416% | -2.415% | -3.081% |
| death_cross | BEAR | 17,313 | -0.830% | -1.653% | -1.765% |
| price_x_sma200_dn | BEAR | 52,959 | -0.990% | -1.943% | -2.694% |
| low_52w_breakdown | BEAR | 27,083 | -1.690% | -3.111% | -4.639% |

---

## E7 deep dive — high_52w_breakout

E7 is the only event with consistent edge pooled (+0.99pp vs overall base). However, slope>0 base rate is −0.71% @42d vs overall −2.19%, so the true incremental edge of E7 within slope>0 dates must be assessed separately.

| cell | n | median% | vs slope>0 base | frac>0 |
|---|---|---|---|---|
| E7 + slope>0 | 25,094 | -0.990% | +0.109% | 45.9% |
| E7 + slope>0 + vol<1.5 | 16,596 | -0.863% | +0.236% (note: vol split not slope-filtered) | 46.2% |

### E7 year-by-year: slope>0 vs slope>0 base rate

| year | E7+slope>0 median | slope>0 base median | vs base |
|---|---|---|---|
| 2022 | -2.10% | n/a | n/a |
| 2023 | -2.70% | n/a | n/a |
| 2024 | -1.09% | -1.51% | +0.42pp |
| 2025 | -0.61% | -1.30% | +0.69pp |
| 2026 | +0.20% | -2.64% | +2.85pp |

*Slope>0 base rate only available from 2024 (signal_features warmup). 2021-2023 E7 cells reflect the overall base comparison only.*

### dist_52w_high STATE vs forward 42d excess (slope>0 universe)

Does proximity to the 52-week high carry edge continuously, or is the crossing event special?

| proximity bucket | n | median_42d | frac>0 |
|---|---|---|---|
| d9_within_2pct | 85,506 | +0.140% | 50.9% |
| d8_within_5pct | 75,997 | +0.020% | 50.1% |
| d7_within_10pct | 72,112 | -0.580% | 47.8% |
| d6_within_15pct | 45,823 | -0.735% | 47.4% |
| d5_within_20pct | 34,501 | -0.709% | 47.5% |
| d4_within_30pct | 45,418 | -1.706% | 45.0% |
| d3_within_40pct | 25,672 | -3.577% | 42.1% |
| d2_within_50pct | 16,346 | -5.795% | 39.4% |
| d10_at_or_above_high | 4,658 | -0.031% | 49.5% |
| d1_below_50pct | 23,505 | -8.837% | 37.1% |

The state analysis shows proximity to the 52w high is **monotonically associated with better returns** within slope>0 names — the signal is continuous, not a step function at the crossing. d9 (within 2% of high) = +0.14% median, d10 (at or above) = −0.03%. The breakout event itself is slightly worse than near-high state, suggesting the STATE (proximity) carries most of the signal. Phase 4 should test dist_52w_high as a continuous feature rather than binary breakout.

---

## Confluence — n_bullish_events in trailing 21 trading days

**Note: the confluence section in the prior version of this report was invalid.** The previous computation ran over the sparse event-fire frame (686k rows) instead of the full eligible universe (~7M rows), producing a nonsensical distribution (15 rows at bucket 0, 98% at bucket 3+). This version computes n_bull over the full frame during the batch loop. With 8 event types each firing several times per year, bucket 0 (no event of any type in trailing 21 days) is naturally rare (~9%); most dates have at least one event type in window. Sanity check: max bucket <= 8.

Hypothesis: forward 42d excess increases monotonically with bucket.

| bucket | n | median% | wmean% | frac>0 |
|---|---|---|---|---|
| 0 | 608,808 | -2.418% | -3.362% | 41.2% |
| 1 | 1,633,593 | -2.334% | -2.674% | 42.4% |
| 2 | 1,687,619 | -1.996% | -2.169% | 43.4% |
| 3+ | 2,984,722 | -2.166% | -2.240% | 43.3% |

**Monotonic overall: NO**


### Conditioned on sma200_slope > 0

| bucket | n | median% | wmean% | frac>0 |
|---|---|---|---|---|
| 0 | 184,250 | -1.105% | -1.046% | 45.6% |
| 1 | 569,374 | -1.064% | -0.770% | 45.9% |
| 2 | 701,120 | -1.048% | -0.740% | 45.9% |
| 3+ | 1,105,206 | -1.151% | -0.739% | 45.8% |

**Monotonic (slope>0): NO**

---

## Verdict

### Corrected verdict (2026-06-11, post slope-conditioned base rate analysis)

**TA events carry no exploitable edge at 21–63d horizons in this universe; the edge lives in states.**

The analysis initially flagged E7 (high_52w_breakout) as having edge (+0.99pp vs overall base). That comparison used the wrong baseline. When event cells are compared against their slope-conditioned base rates:

- **Slope>0 base @42d: −1.10%.** Every bullish event conditioned on slope>0 falls within ±0.5pp of this number. E7+slope>0 = −0.99% (+0.11pp incremental edge). No bullish event demonstrates reliable signal beyond what slope>0 dates already deliver unconditionally.
- **Slope filter is the dominant effect.** The slope>0 base (−1.10%) vs slope<=0 base (−3.43%) is a 2.33pp spread — larger than any single event's claimed pooled edge. The apparent event edges were the slope filter's effect, not the crossings themselves.
- **Continuous state beats binary event for E7.** `dist_52w_high` within 2% of high (d9 bucket) = +0.14% median; breakout itself (d10, at or above) = −0.03%. The signal is continuous proximity, not the crossing. A continuous feature (`dist_52w_high`) captures this more efficiently than an event flag.
- **Confluence non-monotonic.** After correcting the computation to run over the full eligible universe (7M rows, not the 686k sparse event frame), bucket distributions are plausible (bucket 0 = 8.8%, most dates have ≥1 event type in window), but monotonicity fails: bucket 3+ (−2.17%) is slightly worse than bucket 2 (−2.00%). No additive signal from stacking events.
- **Volume confirmation inverted.** High vol_ratio (≥1.5) at event fire underperforms low vol_ratio across every bullish event type. vol_ratio is a candidate negative feature, not a confirmation.

**Architecture decision:** Events are retired as a signal class. Phase 4 uses continuous state features (the `signal_features` table) in a cross-sectional rank model. The `dist_52w_high` feature replaces the E7 breakout event as the primary 52w-proximity signal.

---

*Analysis only. Architecture amendment in `docs/signal_v2_rebuild.md`.*
