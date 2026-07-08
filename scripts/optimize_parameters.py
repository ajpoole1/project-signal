"""
Per-regime grid search over signal weights using historical prediction accuracy.

Phase 3.5 rewrite: evaluates candidate weight sets by re-running compute_composite
over historical stock_signals rows (exact re-scoring, no proxies) and measuring
real accuracy against resolved outcomes. Run once per regime after backfill_predictions
has produced a sufficient baseline.

Writes proposals to parameter_proposals for human review. Never modifies config files.
All proposals start with status='pending' — human approval required.

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/optimize_parameters.py
    docker compose exec airflow-scheduler python /opt/airflow/scripts/optimize_parameters.py --regime elevated
    docker compose exec airflow-scheduler python /opt/airflow/scripts/optimize_parameters.py --regime all
"""

from __future__ import annotations

import os
import sys
from itertools import product
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from dag_components.indicators.calculations import _rsi_score

# Weight grid — all combinations that sum to 1.0 are evaluated per regime
WEIGHT_GRID = {
    "sma_200": [0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    "sma_50": [0.15, 0.20, 0.25, 0.30],
    "macd": [0.15, 0.20, 0.25, 0.30],
    "rsi": [0.10, 0.15, 0.20, 0.25],
}
_WEIGHT_TOL = 0.001

# VIX multiplier grid (evaluated independently per regime)
VIX_MULT_GRID = {
    "low": [0.70, 0.75, 0.80, 0.85, 0.90],
    "normal": [0.90, 0.95, 1.00, 1.05],
    "elevated": [1.05, 1.08, 1.10, 1.12, 1.15, 1.20],
    "high": [1.10, 1.15, 1.20, 1.25, 1.30],
    "extreme": [0.50, 0.60, 0.70, 0.80],
}

SIGNAL_THRESHOLD_GRID = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]

ALL_REGIMES = ("low", "normal", "elevated", "high", "extreme")


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


def _weight_combos() -> list[dict]:
    """All weight sets from the grid that sum to 1.0."""
    combos = []
    for w200, w50, wmacd, wrsi in product(
        WEIGHT_GRID["sma_200"],
        WEIGHT_GRID["sma_50"],
        WEIGHT_GRID["macd"],
        WEIGHT_GRID["rsi"],
    ):
        if abs(w200 + w50 + wmacd + wrsi - 1.0) <= _WEIGHT_TOL:
            combos.append({"sma_200": w200, "sma_50": w50, "macd": wmacd, "rsi": wrsi})
    return combos


def _load_signal_rows(conn, regime: str) -> list[dict]:
    """Load stock_signals rows for a regime, joined with raw_prices close and
    resolved outcome fields from signal_predictions (v1.0-backfill only)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ss.composite_vix_adj,
                ss.sma_50,   ss.sma_200,
                ss.macd,     ss.macd_signal,
                ss.rsi_14,
                ss.vix_close,
                rp.close     AS price_close,
                sp.bias,
                sp.correct_5d, sp.correct_10d, sp.correct_20d
            FROM stock_signals ss
            JOIN raw_prices rp
                ON rp.ticker = ss.ticker AND rp.date = ss.date
            JOIN signal_predictions sp
                ON sp.ticker = ss.ticker AND sp.signal_date = ss.date
                AND sp.signal_version = 'v1.0-backfill'
            WHERE ss.vix_regime = %s
              AND sp.resolved_at IS NOT NULL
            """,
            (regime,),
        )
        rows = cur.fetchall()

    return [
        {
            "sma_50": float(r[1]) if r[1] is not None else None,
            "sma_200": float(r[2]) if r[2] is not None else None,
            "macd": float(r[3]) if r[3] is not None else None,
            "macd_sig": float(r[4]) if r[4] is not None else None,
            "rsi_14": float(r[5]) if r[5] is not None else None,
            "vix_close": float(r[6]) if r[6] is not None else None,
            "close": float(r[7]),
            "bias": r[8],
            "correct_5d": r[9],
            "correct_10d": r[10],
            "correct_20d": r[11],
        }
        for r in rows
    ]


def _rescore_with_weights(
    rows: list[dict], weights: dict, vix_mult: float, threshold: float
) -> list[dict]:
    """Re-score using explicit weights (bypasses config lookup)."""
    out = []
    for r in rows:
        scores: dict[str, float] = {}
        if r["sma_200"] is not None:
            scores["sma_200"] = 1.0 if r["close"] > r["sma_200"] else -1.0
        if r["sma_50"] is not None:
            scores["sma_50"] = 1.0 if r["close"] > r["sma_50"] else -1.0
        if r["macd"] is not None and r["macd_sig"] is not None:
            scores["macd"] = 1.0 if r["macd"] > r["macd_sig"] else -1.0
        if r["rsi_14"] is not None:
            scores["rsi"] = _rsi_score(r["rsi_14"])
        if not scores:
            continue
        total_w = sum(weights[k] for k in scores)
        raw = sum(scores[k] * weights[k] for k in scores) / total_w
        adj = round(raw * vix_mult, 4)
        if abs(adj) < threshold:
            continue
        out.append({**r, "rescored_adj": adj})
    return out


def _accuracy(rows: list[dict], horizon: int) -> float | None:
    field = f"correct_{horizon}d"
    valid = [r for r in rows if r[field] is not None]
    if len(valid) < config.ACCURACY_MIN_SAMPLE_SIZE:
        return None
    return sum(1 for r in valid if r[field]) / len(valid)


def _current_vix_mult(regime: str) -> float:
    return next((m for _, lbl, m in config.VIX_REGIMES if lbl == regime), 1.0)


def _generate_rationale(
    param_name: str,
    current: float,
    proposed: float,
    accuracy_delta: float,
    sample_size: int,
    regime: str,
) -> str:
    import anthropic

    prompt = (
        f"A stock signal system parameter is being tuned for the '{regime}' VIX regime. "
        f"Parameter: {param_name}. Current value: {current}. Proposed value: {proposed}. "
        f"Projected accuracy improvement: {accuracy_delta:+.1%} on {sample_size} resolved predictions. "
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
    regime: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO parameter_proposals
                (param_name, current_value, proposed_value, rationale,
                 accuracy_delta, sample_size, vix_regime_scope)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (param_name, current, proposed, rationale, accuracy_delta, sample_size, regime),
        )
    conn.commit()


def _optimize_regime(conn, regime: str, horizon: int, min_delta: float) -> int:
    """Grid search weights + multiplier for one regime. Returns proposals generated."""
    rows = _load_signal_rows(conn, regime)
    if not rows:
        print(f"  [{regime}] no resolved predictions — skipping")
        return 0

    current_mult = _current_vix_mult(regime)
    current_weights = config.get_regime_weights(regime)
    current_threshold = config.LLM_SIGNAL_THRESHOLD

    # Baseline: current weights + current multiplier + current threshold
    baseline_rows = _rescore_with_weights(rows, current_weights, current_mult, current_threshold)
    baseline_acc = _accuracy(baseline_rows, horizon)

    if baseline_acc is None:
        print(
            f"  [{regime}] insufficient sample after threshold filter "
            f"({len(baseline_rows)} rows) — skipping"
        )
        return 0

    print(
        f"  [{regime}] baseline accuracy ({horizon}d): {baseline_acc:.1%}  "
        f"n={len(baseline_rows)}  raw_rows={len(rows)}"
    )

    proposals = 0
    combos = _weight_combos()

    # --- Weight grid search ---
    best_acc = baseline_acc
    best_weights = current_weights
    best_n = len(baseline_rows)

    for weights in combos:
        candidate_rows = _rescore_with_weights(rows, weights, current_mult, current_threshold)
        acc = _accuracy(candidate_rows, horizon)
        if acc is None:
            continue
        if acc > best_acc:
            best_acc = acc
            best_weights = weights
            best_n = len(candidate_rows)

    weight_delta = best_acc - baseline_acc
    if weight_delta >= min_delta and best_weights is not current_weights:
        print(f"  [{regime}] weight improvement: +{weight_delta:.1%}")
        for signal, proposed_val in best_weights.items():
            current_val = current_weights[signal]
            if abs(proposed_val - current_val) < 0.001:
                continue
            param_name = f"{regime}.{signal}_weight"
            rationale = _generate_rationale(
                param_name, current_val, proposed_val, weight_delta, best_n, regime
            )
            _insert_proposal(
                conn, param_name, current_val, proposed_val, rationale, weight_delta, best_n, regime
            )
            proposals += 1
            print(f"    proposed {param_name}: {current_val} → {proposed_val}")
    else:
        print(f"  [{regime}] weights: no improvement (best delta={weight_delta:+.1%})")

    # --- VIX multiplier search ---
    if regime in VIX_MULT_GRID:
        best_mult_acc = baseline_acc
        best_mult = current_mult
        best_mult_n = len(baseline_rows)

        for candidate_mult in VIX_MULT_GRID[regime]:
            if abs(candidate_mult - current_mult) < 0.001:
                continue
            candidate_rows = _rescore_with_weights(
                rows, current_weights, candidate_mult, current_threshold
            )
            acc = _accuracy(candidate_rows, horizon)
            if acc is None:
                continue
            if acc > best_mult_acc:
                best_mult_acc = acc
                best_mult = candidate_mult
                best_mult_n = len(candidate_rows)

        mult_delta = best_mult_acc - baseline_acc
        if mult_delta >= min_delta and abs(best_mult - current_mult) >= 0.001:
            param_name = f"vix_{regime}_mult"
            rationale = _generate_rationale(
                param_name, current_mult, best_mult, mult_delta, best_mult_n, regime
            )
            _insert_proposal(
                conn,
                param_name,
                current_mult,
                best_mult,
                rationale,
                mult_delta,
                best_mult_n,
                regime,
            )
            proposals += 1
            print(
                f"  [{regime}] proposed {param_name}: {current_mult} → {best_mult} "
                f"(+{mult_delta:.1%}, n={best_mult_n})"
            )
        else:
            print(f"  [{regime}] multiplier: no improvement (best delta={mult_delta:+.1%})")

    # --- Signal threshold search ---
    best_thr_acc = baseline_acc
    best_thr = current_threshold
    best_thr_n = len(baseline_rows)

    for thr in SIGNAL_THRESHOLD_GRID:
        if abs(thr - current_threshold) < 0.001:
            continue
        candidate_rows = _rescore_with_weights(rows, current_weights, current_mult, thr)
        acc = _accuracy(candidate_rows, horizon)
        if acc is None:
            continue
        if acc > best_thr_acc:
            best_thr_acc = acc
            best_thr = thr
            best_thr_n = len(candidate_rows)

    thr_delta = best_thr_acc - baseline_acc
    if thr_delta >= min_delta and abs(best_thr - current_threshold) >= 0.001:
        param_name = f"{regime}.signal_threshold"
        rationale = _generate_rationale(
            param_name, current_threshold, best_thr, thr_delta, best_thr_n, regime
        )
        _insert_proposal(
            conn, param_name, current_threshold, best_thr, rationale, thr_delta, best_thr_n, regime
        )
        proposals += 1
        print(
            f"  [{regime}] proposed threshold: {current_threshold} → {best_thr} "
            f"(+{thr_delta:.1%}, n={best_thr_n})"
        )
    else:
        print(f"  [{regime}] threshold: no improvement (best delta={thr_delta:+.1%})")

    return proposals


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Per-regime grid search over signal weights")
    parser.add_argument(
        "--regime",
        default="elevated",
        choices=[*ALL_REGIMES, "all"],
        help="VIX regime to optimise (default: elevated). Use 'all' for every regime.",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=config.OPTIMIZATION_TARGET_HORIZON,
        help=f"Prediction horizon in trading days (default: {config.OPTIMIZATION_TARGET_HORIZON})",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    _load_env(root / ".env")

    conn = _get_connection()
    min_delta = config.OPTIMIZATION_MIN_ACCURACY_DELTA

    regimes = list(ALL_REGIMES) if args.regime == "all" else [args.regime]
    combos = _weight_combos()

    print(f"optimize_parameters: {len(combos)} valid weight combos in grid")
    print(f"Optimising regime(s): {regimes}  horizon: {args.horizon}d  min_delta: {min_delta:.0%}")
    print()

    total_proposals = 0
    for regime in regimes:
        total_proposals += _optimize_regime(conn, regime, args.horizon, min_delta)
        print()

    conn.close()

    if total_proposals:
        print(f"Done. {total_proposals} proposal(s) written to parameter_proposals.")
        print("Review with:")
        print(
            "  SELECT param_name, current_value, proposed_value, accuracy_delta, "
            "sample_size, vix_regime_scope"
        )
        print("  FROM parameter_proposals WHERE status='pending' ORDER BY accuracy_delta DESC;")
        print("Approve by adding to config/parameter_overrides.json and committing.")
    else:
        print("Done. No proposals met the minimum accuracy delta threshold.")


if __name__ == "__main__":
    main()
