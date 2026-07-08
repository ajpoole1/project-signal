# Phase 3.5 — Regime-Conditional Signal Tuning

_Status: 🔄 In progress | Started 2026-06-09_

---

## Motivation

Aggregate signal accuracy is a misleading average. The first resolved-prediction
baseline (v1.0-backfill) revealed:

| Bias | VIX regime | 5d | 10d | 20d | n |
|---|---|---|---|---|---|
| Bullish | **elevated** | 40.4% | 49.7% | **62.2%** | 193 |
| Bullish | normal | 31.0% | 34.1% | 36.7% | 684 |
| Bearish | (all) | 45.1% | 48.8% | 44.2% | 1022 |
| Bullish | (all) | 33.1% | 37.5% | 42.3% | 877 |

**Key finding:** bullish signals in *elevated* VIX hit 62% at the 20-day horizon —
a real, exploitable edge — while bullish signals in *normal* VIX are barely above
noise (36.7%). The 33% aggregate is the average of a great signal and a bad one.

**Wrong move:** globally tune the whole system toward the 62% case. That would
optimize toward the mediocre middle and destroy the edge in the regime where it lives.

**Right move:** treat each VIX regime as its own predictive context with its own
tuned parameters. Codify the elevated-VIX edge as its own ruleset; tune within it
rather than averaging it away.

---

## Architecture Decisions

### 1. Per-regime weight sets (chosen)
`SIGNAL_WEIGHTS` becomes a dict keyed by `vix_regime`, each value a full weight set.
`compute_composite` takes the day's regime and looks up the matching set, falling
back to `default` for untuned regimes.

```python
SIGNAL_WEIGHTS = {
    "default":  {"sma_200": 0.30, "sma_50": 0.25, "macd": 0.25, "rsi": 0.20},
    "elevated": {"sma_200": 0.20, "sma_50": 0.20, "macd": 0.30, "rsi": 0.30},
    "normal":   {"sma_200": 0.35, "sma_50": 0.25, "macd": 0.20, "rsi": 0.20},
    # low / high / extreme inherit "default" until tuned
}
```

Rejected alternatives:
- **Global weights + per-regime gate** — only tunes *which* signals to act on, not
  how they're scored. Less powerful; leaves the scoring blind to regime.
- **Per-(regime, bias) matrix** — maximally expressive but needs far more data per
  cell. Revisit later if per-regime alone proves insufficient.

### 2. Optimizer recomputes from `stock_signals` (chosen)
The existing optimizer uses directional heuristics/proxies because it operates on
the *already-blended* `composite_vix_adj` stored in `signal_predictions` — it cannot
honestly evaluate new weights. `stock_signals` retains every sub-signal input
(`sma_50`, `sma_200`, `macd`, `macd_signal`, `rsi_14`) plus `close` from `raw_prices`,
so we can re-run `compute_composite` with any candidate weight set and measure *real*
accuracy against actual returns. No proxy, no schema change to enable it.

### 3. `signal_version` enters the predictions PK
PK becomes `(ticker, signal_date, signal_version)` so v1.0 baseline and v1.1
candidates coexist for clean A/B comparison via `signal_accuracy`. This is a
temporary storage cost (multiple versions of the same prediction) accepted for
the tuning campaign; prune old versions once knobs are locked.

---

## The scoring seam

`dag_components/indicators/calculations.py::compute_composite` is where the
sub-signals (`{-1, -0.5, 0, 0.5, 1}` each) get blended by weight. This is the
single function that changes shape. Everything upstream (indicator math) and
downstream (bias/confidence classification) is unaffected.

---

## Build Order

| Step | File | Change |
|---|---|---|
| 1 | `sql/migrations/004_regime_conditional_tuning.sql` | PK → `(ticker, signal_date, signal_version)`; add `regime_weight_set` audit column |
| 2 | `config/config.py` | `SIGNAL_WEIGHTS` → regime-keyed dict; `_get_regime_weights()` helper; bump `SIGNAL_VERSION = "v1.1"` |
| 3 | `indicators/calculations.py` | `compute_composite(..., regime: str = "default")` looks up weight set |
| 4 | `indicators/tasks.py` + `tests/test_indicators.py` | pass `vix_regime` into `compute_composite`; tests asserting regime changes the composite |
| 5 | `scripts/optimize_parameters.py` | rewrite: `_rescore_predictions()` honest evaluator; `--regime` flag; per-regime grid search |
| 6 | backfill + A/B | re-backfill predictions as v1.1; compare `signal_accuracy` by `signal_version × vix_regime` |
| 7 | `tests/test_optimize_parameters.py` + CLAUDE.md | new test file; update docs + phase table |

**Only Step 3 is a breaking change.** `regime` defaults to `"default"`, so any
missed caller silently falls back to the old global behavior rather than erroring.

---

## parameter_overrides.json extension

Regime-scoped keys use dotted notation:
```json
{
  "elevated.macd_weight": 0.30,
  "elevated.rsi_weight": 0.30,
  "normal.sma_200_weight": 0.35
}
```
`config.py` parses the prefix to route the override into the correct regime set.
Unprefixed keys continue to apply to `default`. Starts as `{}`; only approved
proposals land here, committed to git.

---

## A/B validation query (Step 6)

```sql
SELECT signal_version, vix_regime, bias, horizon_days,
       total_signals, accuracy_rate
FROM signal_accuracy
WHERE signal_version IN ('v1.0-backfill', 'v1.1')
ORDER BY vix_regime, bias, horizon_days, signal_version;
```
Success = v1.1 beats v1.0 within each regime bucket, *especially* not regressing
the elevated-VIX bullish edge.

---

## Open questions / future work

- If per-regime weights alone don't separate well, escalate to per-(regime, bias).
- The `confidence` metric is currently flat across tiers (33–34%) — it adds no
  predictive value. Revisit its formula (`classify_confidence`) as a separate task.
- Consider whether `high`/`extreme` VIX bullish signals should invert rather than
  amplify — fear regimes may behave structurally differently.
