"""
Grid search over signal parameters using historical prediction accuracy as the objective.

Reads signal_predictions (resolved outcomes), evaluates each parameter combination,
and writes proposals to parameter_proposals for human review. Never modifies any
config files. Run this after backfill_predictions.py has produced a sufficient
accuracy baseline.

Calls Haiku to generate a human-readable rationale for each proposal.
All proposals start with status='pending' — human approval required.

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/optimize_parameters.py
"""

from __future__ import annotations

import os
import sys
from itertools import product
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config

PARAM_GRID = {
    "sma_200_weight": [0.20, 0.25, 0.30, 0.35, 0.40],
    "sma_50_weight": [0.15, 0.20, 0.25, 0.30],
    "macd_weight": [0.15, 0.20, 0.25, 0.30],
    "rsi_weight": [0.10, 0.15, 0.20, 0.25],
    "vix_high_mult": [1.10, 1.15, 1.20, 1.25, 1.30],
    "vix_elevated_mult": [1.05, 1.08, 1.10, 1.12, 1.15],
    "vix_low_mult": [0.75, 0.80, 0.85, 0.90],
    "signal_threshold": [0.35, 0.40, 0.45, 0.50, 0.55, 0.60],
}

# Weights must sum to 1.0 (within floating-point tolerance)
_WEIGHT_KEYS = ["sma_200_weight", "sma_50_weight", "macd_weight", "rsi_weight"]
_WEIGHT_TOL = 0.001


def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _get_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "host.docker.internal"),
        dbname="signal",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def _load_resolved_predictions(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT signal_version, vix_regime, vol_environment, bias,
                   composite_vix_adj, correct_5d, correct_10d, correct_20d,
                   return_5d, return_10d, return_20d
            FROM signal_predictions
            WHERE resolved_at IS NOT NULL
            """
        )
        rows = cur.fetchall()
    return [
        {
            "signal_version": r[0],
            "vix_regime": r[1],
            "vol_environment": r[2],
            "bias": r[3],
            "composite_vix_adj": float(r[4]) if r[4] is not None else None,
            "correct_5d": r[5],
            "correct_10d": r[6],
            "correct_20d": r[7],
            "return_5d": float(r[8]) if r[8] is not None else None,
            "return_10d": float(r[9]) if r[9] is not None else None,
            "return_20d": float(r[10]) if r[10] is not None else None,
        }
        for r in rows
    ]


def _baseline_accuracy(
    predictions: list[dict], horizon: int, bias: str, vix_regimes: list[str] | None = None
) -> float | None:
    """Compute accuracy rate for a subset of resolved predictions."""
    field = f"correct_{horizon}d"
    subset = [
        p
        for p in predictions
        if p["bias"] == bias
        and p[field] is not None
        and (vix_regimes is None or p["vix_regime"] in vix_regimes)
    ]
    if len(subset) < config.ACCURACY_MIN_SAMPLE_SIZE:
        return None
    return sum(1 for p in subset if p[field]) / len(subset)


def _evaluate_threshold(
    predictions: list[dict], threshold: float, horizon: int, bias: str
) -> float | None:
    """Accuracy if we only kept predictions above abs(composite_vix_adj) >= threshold."""
    field = f"correct_{horizon}d"
    subset = [
        p
        for p in predictions
        if p["bias"] == bias
        and p[field] is not None
        and p["composite_vix_adj"] is not None
        and abs(p["composite_vix_adj"]) >= threshold
    ]
    if len(subset) < config.ACCURACY_MIN_SAMPLE_SIZE:
        return None
    return sum(1 for p in subset if p[field]) / len(subset)


def _generate_weight_combos() -> list[dict]:
    """All weight combinations from the grid that sum to 1.0."""
    combos = []
    for w200, w50, wmacd, wrsi in product(
        PARAM_GRID["sma_200_weight"],
        PARAM_GRID["sma_50_weight"],
        PARAM_GRID["macd_weight"],
        PARAM_GRID["rsi_weight"],
    ):
        if abs(w200 + w50 + wmacd + wrsi - 1.0) <= _WEIGHT_TOL:
            combos.append(
                {
                    "sma_200_weight": w200,
                    "sma_50_weight": w50,
                    "macd_weight": wmacd,
                    "rsi_weight": wrsi,
                }
            )
    return combos


def _generate_rationale(
    param_name: str, current: float, proposed: float, accuracy_delta: float, sample_size: int
) -> str:
    """Generate a concise rationale string using Haiku."""
    import anthropic

    prompt = (
        f"A stock signal system parameter is being tuned. Parameter: {param_name}. "
        f"Current value: {current}. Proposed value: {proposed}. "
        f"Projected accuracy improvement: {accuracy_delta:+.1%} on {sample_size} historical predictions. "
        "Write one concise sentence (under 40 words) explaining why this change may improve signal accuracy."
    )
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL_CLASSIFICATION,
        max_tokens=80,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _insert_proposal(
    conn,
    param_name: str,
    current: float,
    proposed: float,
    rationale: str,
    accuracy_delta: float,
    sample_size: int,
    vix_regime_scope: str | None = None,
    vol_env_scope: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO parameter_proposals
                (param_name, current_value, proposed_value, rationale,
                 accuracy_delta, sample_size, vix_regime_scope, vol_env_scope)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                param_name,
                current,
                proposed,
                rationale,
                accuracy_delta,
                sample_size,
                vix_regime_scope,
                vol_env_scope,
            ),
        )
    conn.commit()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    _load_env(root / ".env")

    conn = _get_connection()
    predictions = _load_resolved_predictions(conn)

    print(f"optimize_parameters: {len(predictions):,} resolved predictions loaded")
    if len(predictions) < config.ACCURACY_MIN_SAMPLE_SIZE * 10:
        print(
            f"WARNING: fewer than {config.ACCURACY_MIN_SAMPLE_SIZE * 10} predictions — proposals may be unreliable."
        )
    print()

    horizon = config.OPTIMIZATION_TARGET_HORIZON
    bias = config.OPTIMIZATION_TARGET_BIAS
    min_delta = config.OPTIMIZATION_MIN_ACCURACY_DELTA

    baseline = _baseline_accuracy(predictions, horizon, bias)
    if baseline is None:
        print(
            f"Not enough resolved predictions to compute baseline ({bias}, {horizon}d). Run backfill first."
        )
        conn.close()
        return

    print(f"Baseline accuracy ({bias}, {horizon}d): {baseline:.1%}")
    print()

    proposals_generated = 0

    # --- Signal threshold optimization ---
    current_threshold = config.LLM_SIGNAL_THRESHOLD
    print(f"Evaluating signal_threshold (current={current_threshold})...")
    best_threshold = current_threshold
    best_threshold_acc = baseline
    best_threshold_n = 0

    for threshold in PARAM_GRID["signal_threshold"]:
        if threshold == current_threshold:
            continue
        acc = _evaluate_threshold(predictions, threshold, horizon, bias)
        if acc is None:
            continue
        subset_n = sum(
            1
            for p in predictions
            if p["bias"] == bias
            and p[f"correct_{horizon}d"] is not None
            and p["composite_vix_adj"] is not None
            and abs(p["composite_vix_adj"]) >= threshold
        )
        if acc > best_threshold_acc:
            best_threshold = threshold
            best_threshold_acc = acc
            best_threshold_n = subset_n

    delta = best_threshold_acc - baseline
    if best_threshold != current_threshold and delta >= min_delta:
        print(
            f"  Proposing signal_threshold: {current_threshold} → {best_threshold} (+{delta:.1%}, n={best_threshold_n})"
        )
        rationale = _generate_rationale(
            "signal_threshold", current_threshold, best_threshold, delta, best_threshold_n
        )
        _insert_proposal(
            conn,
            "signal_threshold",
            current_threshold,
            best_threshold,
            rationale,
            delta,
            best_threshold_n,
        )
        proposals_generated += 1
    else:
        print(f"  No improvement found (best delta={delta:+.1%})")

    # --- VIX multiplier optimization ---
    vix_high_current = next(
        (mult for _, label, mult in config.VIX_REGIMES if label == "high"), 1.20
    )
    vix_elevated_current = next(
        (mult for _, label, mult in config.VIX_REGIMES if label == "elevated"), 1.10
    )
    vix_low_current = next((mult for _, label, mult in config.VIX_REGIMES if label == "low"), 0.85)

    for vix_label, current_mult, grid_key in [
        ("high", vix_high_current, "vix_high_mult"),
        ("elevated", vix_elevated_current, "vix_elevated_mult"),
        ("low", vix_low_current, "vix_low_mult"),
    ]:
        regime_baseline = _baseline_accuracy(predictions, horizon, bias, vix_regimes=[vix_label])
        if regime_baseline is None:
            print(f"  {grid_key}: insufficient data for '{vix_label}' regime — skipping")
            continue

        print(
            f"Evaluating {grid_key} (current={current_mult}, regime baseline={regime_baseline:.1%})..."
        )
        # VIX multipliers affect composite_vix_adj magnitude; we approximate improvement
        # by checking if higher multipliers correlate with accuracy in that regime
        # (directional heuristic: if signals in this regime are already accurate, multiplier tuning helps)
        regime_preds = [
            p
            for p in predictions
            if p["vix_regime"] == vix_label and p[f"correct_{horizon}d"] is not None
        ]
        regime_n = len(regime_preds)
        if regime_n < config.ACCURACY_MIN_SAMPLE_SIZE:
            print(f"  {grid_key}: sample too small ({regime_n}) — skipping")
            continue

        # Find multiplier that maximizes accuracy in this regime
        best_mult = current_mult
        best_mult_acc = regime_baseline

        for candidate in PARAM_GRID[grid_key]:
            if candidate == current_mult:
                continue
            # Directional heuristic: higher multiplier amplifies signals in this regime.
            # Accuracy improves if most correct signals in this regime were already strong.
            scale = candidate / current_mult
            # Estimate: shift marginal predictions in/out of threshold
            adjusted = [
                p
                for p in regime_preds
                if p["composite_vix_adj"] is not None
                and abs(p["composite_vix_adj"]) * scale >= config.LLM_SIGNAL_THRESHOLD
            ]
            if len(adjusted) < config.ACCURACY_MIN_SAMPLE_SIZE:
                continue
            adj_acc = sum(1 for p in adjusted if p[f"correct_{horizon}d"]) / len(adjusted)
            if adj_acc > best_mult_acc:
                best_mult = candidate
                best_mult_acc = adj_acc

        delta = best_mult_acc - regime_baseline
        if best_mult != current_mult and delta >= min_delta:
            print(
                f"  Proposing {grid_key}: {current_mult} → {best_mult} (+{delta:.1%}, n={regime_n})"
            )
            rationale = _generate_rationale(grid_key, current_mult, best_mult, delta, regime_n)
            _insert_proposal(
                conn,
                grid_key,
                current_mult,
                best_mult,
                rationale,
                delta,
                regime_n,
                vix_regime_scope=vix_label,
            )
            proposals_generated += 1
        else:
            print(f"  {grid_key}: no improvement found (best delta={delta:+.1%})")

    # --- Signal weight optimization ---
    print(
        f"\nEvaluating signal weight combinations ({len(_generate_weight_combos())} valid combos)..."
    )

    # For weight optimization: approximate by checking if shifting weight toward
    # the most-predictive indicator improves the separation of correct/incorrect signals.
    # Use composite_vix_adj distribution as a proxy: higher |composite_vix_adj| correlates
    # with accuracy, so weights that produce higher scores on correct predictions win.
    correct_preds = [
        p
        for p in predictions
        if p["bias"] == bias
        and p[f"correct_{horizon}d"] is True
        and p["composite_vix_adj"] is not None
    ]
    wrong_preds = [
        p
        for p in predictions
        if p["bias"] == bias
        and p[f"correct_{horizon}d"] is False
        and p["composite_vix_adj"] is not None
    ]

    if len(correct_preds) < 10 or len(wrong_preds) < 10:
        print("  Insufficient correct/wrong predictions for weight optimization — skipping")
    else:
        avg_correct_score = sum(abs(p["composite_vix_adj"]) for p in correct_preds) / len(
            correct_preds
        )
        avg_wrong_score = sum(abs(p["composite_vix_adj"]) for p in wrong_preds) / len(wrong_preds)
        separation = avg_correct_score - avg_wrong_score
        print(
            f"  Score separation (correct={avg_correct_score:.3f}, wrong={avg_wrong_score:.3f}, gap={separation:.3f})"
        )
        print(
            "  Weight grid search requires per-signal re-scoring; using accuracy-by-regime proxy instead."
        )
        print(
            "  (Full weight optimization requires recomputing composite_score per candidate — run after backfill_indicators.)"
        )

    conn.close()
    print()
    if proposals_generated:
        print(f"Done. {proposals_generated} proposal(s) written to parameter_proposals.")
        print(
            "Review with: SELECT * FROM parameter_proposals WHERE status = 'pending' ORDER BY proposed_at DESC;"
        )
        print("Approve by adding the parameter to config/parameter_overrides.json and committing.")
    else:
        print("Done. No proposals met the minimum accuracy delta threshold.")


if __name__ == "__main__":
    main()
