"""Pure calculation functions for dag_outcome_tracker and dag_parameter_review.

No Airflow imports. No DB calls. Operates on plain dicts.
"""

from __future__ import annotations

from collections import defaultdict


def is_correct(bias: str, return_n: float | None, threshold: float) -> bool | None:
    """Determine if a prediction was correct given the realized return.

    Returns None for neutral bias or when return is unavailable.
    A bullish signal requires return > threshold; bearish requires return < -threshold.
    Moves smaller than threshold are treated as False (not confirmed).
    """
    if return_n is None:
        return None
    if bias == "bullish":
        return return_n > threshold
    if bias == "bearish":
        return return_n < -threshold
    return None


def nth_trading_day_price(price_rows: list[dict], n: int) -> float | None:
    """Return the close price at trading day n (1-indexed) from price_rows.

    price_rows must be sorted ascending by date and contain only rows strictly
    after the signal date — i.e. row 0 is trading day 1.
    Returns None if fewer than n rows are available.
    """
    if len(price_rows) < n:
        return None
    return float(price_rows[n - 1]["close"])


def compute_return(price_at_signal: float, future_price: float | None) -> float | None:
    """Fractional return: (future_price - price_at_signal) / price_at_signal.

    Returns None if future_price is None or price_at_signal is zero.
    """
    if future_price is None or price_at_signal == 0:
        return None
    return (future_price - price_at_signal) / price_at_signal


def compute_accuracy_rollup(
    predictions: list[dict],
    horizons: list[int],
    min_sample_size: int,
) -> list[dict]:
    """Aggregate resolved predictions into accuracy summary rows.

    Groups by (signal_version, vix_regime, vol_environment, bias, horizon_days).
    Only returns groups with >= min_sample_size resolved predictions.

    Each prediction dict must have:
        signal_version, vix_regime, vol_environment, bias,
        return_5d, return_10d, return_20d,
        correct_5d, correct_10d, correct_20d
    """
    _return_field = {5: "return_5d", 10: "return_10d", 20: "return_20d"}
    _correct_field = {5: "correct_5d", 10: "correct_10d", 20: "correct_20d"}

    # (signal_version, vix_regime, vol_environment, bias, horizon_days) → [(return, correct)]
    groups: dict[tuple, list[tuple[float, bool]]] = defaultdict(list)

    for pred in predictions:
        base = (
            pred.get("signal_version") or "",
            pred.get("vix_regime") or "",
            pred.get("vol_environment") or "",
            pred.get("bias") or "",
        )
        for h in horizons:
            ret_val = pred.get(_return_field[h])
            correct_val = pred.get(_correct_field[h])
            if ret_val is None or correct_val is None:
                continue
            groups[base + (h,)].append((float(ret_val), bool(correct_val)))

    result = []
    for key, pairs in groups.items():
        if len(pairs) < min_sample_size:
            continue
        signal_version, vix_regime, vol_environment, bias, horizon_days = key
        returns = [r for r, _ in pairs]
        correct_flags = [c for _, c in pairs]
        correct_returns = [r for r, c in pairs if c]
        wrong_returns = [r for r, c in pairs if not c]

        result.append(
            {
                "signal_version": signal_version,
                "vix_regime": vix_regime,
                "vol_environment": vol_environment,
                "bias": bias,
                "horizon_days": horizon_days,
                "total_signals": len(pairs),
                "correct_signals": sum(correct_flags),
                "accuracy_rate": round(sum(correct_flags) / len(pairs), 4),
                "avg_return": round(sum(returns) / len(returns), 4),
                "avg_return_correct": round(sum(correct_returns) / len(correct_returns), 4)
                if correct_returns
                else None,
                "avg_return_wrong": round(sum(wrong_returns) / len(wrong_returns), 4)
                if wrong_returns
                else None,
            }
        )

    return result
