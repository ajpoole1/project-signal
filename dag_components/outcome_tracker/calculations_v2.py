"""Pure calculation functions for Signal v2 prediction outcomes.

No Airflow imports. No DB calls. Operates on plain Python types.

Row counting for forward prices uses list offsets (row 0 = trading day 1 after
signal_date), consistent with the existing nth_trading_day_price convention.
No calendar arithmetic is used anywhere in this module.
"""

from __future__ import annotations

import math


def trailing_daily_vol(closes: list[float], lookback: int) -> float | None:
    """Standard deviation of pct returns over the trailing lookback window.

    Uses percentage returns: (c[i] - c[i-1]) / c[i-1].
    Requires at least 2 prices within the window to compute a return.
    Returns None if there are fewer than 2 usable prices.
    """
    if len(closes) < 2:
        return None
    window = closes[-lookback - 1 :] if len(closes) > lookback else closes
    if len(window) < 2:
        return None
    returns = []
    for i in range(1, len(window)):
        if window[i - 1] == 0:
            continue
        returns.append((window[i] - window[i - 1]) / window[i - 1])
    if len(returns) < 2:
        return None
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(variance)


def scaled_threshold(vol_daily: float, horizon: int, k: float) -> float:
    """Volatility-scaled threshold: k * vol_daily * sqrt(horizon).

    This is the minimum |excess_return| required to count as threshold_correct.
    """
    return k * vol_daily * math.sqrt(horizon)


def benchmark_window_return(
    bench_prices_after: list[dict],
    horizon: int,
    bench_price_at_signal: float,
) -> float | None:
    """Return SPY's fractional return from signal date to trading day h.

    bench_prices_after must be sorted ascending by date with rows strictly
    after the signal date (row 0 = trading day 1). Same convention as
    nth_trading_day_price. Returns None if fewer than h rows are available
    or if bench_price_at_signal is zero.
    """
    if bench_price_at_signal == 0 or len(bench_prices_after) < horizon:
        return None
    future_price = float(bench_prices_after[horizon - 1]["close"])
    return (future_price - bench_price_at_signal) / bench_price_at_signal


def excess_return(raw: float, beta: float, bench: float) -> float:
    """Benchmark-adjusted return: raw - beta * bench."""
    return raw - beta * bench


def outcome_flags(
    bias: str,
    excess: float | None,
    threshold: float | None,
) -> tuple[bool | None, bool | None]:
    """Compute (direction_correct, threshold_correct) for a resolved outcome.

    - direction_correct: sign(excess_return) agrees with bias direction.
    - threshold_correct: |excess_return| >= threshold AND direction_correct.
    - neutral bias → (None, None).
    - excess or threshold None → (None, None).
    """
    if bias == "neutral" or excess is None or threshold is None:
        return None, None
    if bias == "bullish":
        direction = excess > 0
    elif bias == "bearish":
        direction = excess < 0
    else:
        return None, None
    threshold_hit = direction and abs(excess) >= threshold
    return direction, threshold_hit


def assign_episodes(rows: list[dict], gap_days: int) -> list[dict]:
    """Stamp episode_id and is_episode_head on a list of signal rows.

    rows must be sorted ascending by (ticker, signal_date) and contain at
    minimum the keys: 'ticker', 'signal_date' (date or str), 'bias'.

    A new episode starts when:
      - It is the first signal for that ticker+bias combination, OR
      - The gap since the previous same-bias signal on that ticker exceeds
        gap_days trading days (measured by row offset within the ticker's
        per-bias sequence, not calendar days).

    episode_id format: '<ticker>:<first_signal_date_of_episode>'

    Returns a new list of dicts with 'episode_id' and 'is_episode_head' added.
    Rows with bias 'neutral' receive episode_id=None, is_episode_head=False.
    """
    # Group rows by (ticker, bias) preserving original order
    from collections import defaultdict

    by_ticker_bias: dict[tuple, list[int]] = defaultdict(list)
    for idx, row in enumerate(rows):
        key = (row["ticker"], row["bias"])
        by_ticker_bias[key].append(idx)

    result = [dict(r) for r in rows]

    for (ticker, bias), indices in by_ticker_bias.items():
        if bias == "neutral":
            for idx in indices:
                result[idx]["episode_id"] = None
                result[idx]["is_episode_head"] = False
            continue

        episode_start_date: str | None = None
        last_head_seq_pos: int = 0  # seq_pos of the most recent episode head

        for seq_pos, idx in enumerate(indices):
            signal_date = str(result[idx]["signal_date"])

            if seq_pos == 0:
                # First signal for this ticker+bias — always a new episode
                episode_start_date = signal_date
                result[idx]["episode_id"] = f"{ticker}:{episode_start_date}"
                result[idx]["is_episode_head"] = True
                last_head_seq_pos = 0
            else:
                # Gap = number of sequence positions since the last episode head
                row_gap = seq_pos - last_head_seq_pos
                if row_gap > gap_days:
                    episode_start_date = signal_date
                    result[idx]["is_episode_head"] = True
                    last_head_seq_pos = seq_pos
                else:
                    result[idx]["is_episode_head"] = False
                result[idx]["episode_id"] = f"{ticker}:{episode_start_date}"

    return result


def has_price_seam(price_rows: list[dict], max_daily_ratio: float) -> bool:
    """Return True if any consecutive pair of closes has a ratio > max_daily_ratio.

    Used to quarantine predictions whose forward window contains a corporate-action
    seam (reverse split / adjustment epoch mismatch).
    price_rows must be sorted ascending and contain 'close' keys.
    """
    for i in range(1, len(price_rows)):
        prev = float(price_rows[i - 1]["close"])
        curr = float(price_rows[i]["close"])
        if prev == 0:
            return True
        ratio = curr / prev
        if ratio > max_daily_ratio or ratio < (1.0 / max_daily_ratio):
            return True
    return False
