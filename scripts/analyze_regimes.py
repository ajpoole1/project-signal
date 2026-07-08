"""
Phase 3 validation: regime distribution, persistence, and falling-knife hypothesis.

Prints only — no DB writes, no file output. Safe to run anytime.

Three checks:
  1. Regime distribution overall and by year.
  2. Average regime persistence (consecutive trading days in same regime per ticker).
     Acceptance: regimes last weeks, not days. If median < 5 days, thresholds are too loose.
  3. Forward-return behaviour of deep-below-SMA200 names split by regime.
     Hypothesis: mean-revert in 'ranging', keep falling in 'trending-down'.
     This is the empirical go/no-go for the two-channel design.

Usage (run inside Docker):
    docker compose exec airflow-scheduler python /opt/airflow/scripts/analyze_regimes.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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


def _run(conn, sql: str, params=None) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    _load_env(root / ".env")
    conn = _get_connection()

    # ------------------------------------------------------------------
    # 1. Regime distribution — overall and by year
    # ------------------------------------------------------------------
    section("1. Regime distribution")

    rows = _run(
        conn,
        """
        SELECT
            ticker_regime,
            COUNT(*)                                       AS rows,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 1) AS pct
        FROM signal_features
        WHERE ticker_regime IS NOT NULL
        GROUP BY ticker_regime
        ORDER BY ticker_regime
    """,
    )
    print(f"\n{'regime':<12} {'rows':>10} {'pct':>6}")
    print("-" * 32)
    for regime, n, pct in rows:
        print(f"{regime:<12} {n:>10,} {float(pct):>5.1f}%")

    rows = _run(
        conn,
        """
        SELECT
            yr,
            ticker_regime,
            rows,
            ROUND(rows * 100.0 / SUM(rows) OVER (PARTITION BY yr), 1) AS pct
        FROM (
            SELECT
                EXTRACT(year FROM date)::int AS yr,
                ticker_regime,
                COUNT(*) AS rows
            FROM signal_features
            WHERE ticker_regime IS NOT NULL
            GROUP BY EXTRACT(year FROM date)::int, ticker_regime
        ) sub
        ORDER BY yr, ticker_regime
    """,
    )
    print(f"\n{'year':<6} {'regime':<12} {'rows':>9} {'pct':>6}")
    print("-" * 38)
    for yr, regime, n, pct in rows:
        print(f"{yr:<6} {regime:<12} {n:>9,} {float(pct):>5.1f}%")

    # ------------------------------------------------------------------
    # 2. Regime persistence
    # ------------------------------------------------------------------
    section("2. Regime persistence (consecutive days in same regime per ticker)")

    rows = _run(
        conn,
        """
        WITH streaks AS (
            SELECT
                ticker, date, ticker_regime,
                ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date)
                - ROW_NUMBER() OVER (PARTITION BY ticker, ticker_regime ORDER BY date)
                    AS grp
            FROM signal_features
            WHERE ticker_regime IS NOT NULL
        ),
        run_lengths AS (
            SELECT ticker, ticker_regime, grp, COUNT(*) AS run_len
            FROM streaks
            GROUP BY ticker, ticker_regime, grp
        )
        SELECT
            ticker_regime,
            COUNT(*)                                              AS n_streaks,
            ROUND(AVG(run_len)::numeric, 1)                       AS mean_days,
            ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY run_len)::numeric, 0) AS p25,
            ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY run_len)::numeric, 0) AS median_days,
            ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY run_len)::numeric, 0) AS p75,
            MAX(run_len)                                          AS max_days
        FROM run_lengths
        GROUP BY ticker_regime
        ORDER BY ticker_regime
    """,
    )
    print(
        f"\n{'regime':<12} {'n_streaks':>10} {'mean':>6} {'p25':>5} {'median':>7} {'p75':>5} {'max':>6}"
    )
    print("-" * 58)
    for regime, n, mean, p25, med, p75, mx in rows:
        flag = "  *** TOO SHORT — raise thresholds ***" if float(med) < 5 else ""
        print(
            f"{regime:<12} {n:>10,} {float(mean):>6.1f} {float(p25):>5.0f} {float(med):>7.0f} {float(p75):>5.0f} {int(mx):>6}{flag}"
        )

    # ------------------------------------------------------------------
    # 3. Falling-knife hypothesis: deep-below-SMA200 × regime
    # ------------------------------------------------------------------
    section("3. Falling-knife hypothesis: deep-below-SMA200 (>15%) × regime")
    print("   Hypothesis: mean-revert in 'ranging', keep falling in 'trending-down'")
    print("   Metric: 21d and 63d forward excess return (raw return - SPY return)")

    rows = _run(
        conn,
        """
        WITH deep_below AS (
            SELECT
                sf.ticker,
                sf.date,
                sf.ticker_regime,
                sf.trend_direction,
                sf.dist_sma200
            FROM signal_features sf
            WHERE sf.dist_sma200 < -0.15
              AND sf.ticker_regime IS NOT NULL
        ),
        with_returns AS (
            SELECT
                db.ticker_regime,
                db.trend_direction,
                db.dist_sma200,
                po21.excess_return  AS excess_21d,
                po63.excess_return  AS excess_63d,
                po21.direction_correct AS dir_correct_21d,
                po63.direction_correct AS dir_correct_63d
            FROM deep_below db
            -- join to prediction_outcomes for excess returns
            -- use signal_predictions as the bridge (same ticker/date)
            JOIN signal_predictions sp
                ON sp.ticker = db.ticker AND sp.signal_date = db.date
            LEFT JOIN prediction_outcomes po21
                ON po21.ticker = db.ticker AND po21.signal_date = db.date
                AND po21.horizon_days = 21 AND po21.resolved_at IS NOT NULL
            LEFT JOIN prediction_outcomes po63
                ON po63.ticker = db.ticker AND po63.signal_date = db.date
                AND po63.horizon_days = 63 AND po63.resolved_at IS NOT NULL
            WHERE po21.excess_return IS NOT NULL OR po63.excess_return IS NOT NULL
        )
        SELECT
            ticker_regime,
            trend_direction,
            COUNT(*)                                                    AS n,
            ROUND(AVG(excess_21d)::numeric * 100, 2)                    AS mean_excess_21d_pct,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
                (ORDER BY excess_21d)::numeric * 100, 2)                AS median_excess_21d_pct,
            ROUND(AVG(excess_63d)::numeric * 100, 2)                    AS mean_excess_63d_pct,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
                (ORDER BY excess_63d)::numeric * 100, 2)                AS median_excess_63d_pct,
            ROUND(AVG((dir_correct_21d)::int)::numeric * 100, 1)        AS dir_acc_21d_pct
        FROM with_returns
        GROUP BY ticker_regime, trend_direction
        ORDER BY ticker_regime, trend_direction NULLS LAST
    """,
    )

    if not rows:
        print("\n  No data — signal_predictions may not overlap with signal_features dates.")
        print("  Re-run after tonight's pipeline populates today's predictions.")
    else:
        print(
            f"\n{'regime':<10} {'direction':<10} {'n':>7} {'exc_21d_med':>12} {'exc_63d_med':>12} {'dir_acc_21d':>12}"
        )
        print("-" * 68)
        for regime, direction, n, _mean21, med21, _mean63, med63, acc21 in rows:
            d = direction if direction else "—"
            m21 = f"{float(med21):>+.2f}%" if med21 is not None else "  NULL"
            m63 = f"{float(med63):>+.2f}%" if med63 is not None else "  NULL"
            a21 = f"{float(acc21):>5.1f}%" if acc21 is not None else "  NULL"
            print(f"{regime:<10} {d:<10} {n:>7,} {m21:>12} {m63:>12} {a21:>12}")

    # ------------------------------------------------------------------
    # 4. Base rate for comparison (all signal_predictions × prediction_outcomes)
    # ------------------------------------------------------------------
    section("4. Base rate: all resolved predictions at 21d and 63d (for comparison)")

    rows = _run(
        conn,
        """
        SELECT
            po.horizon_days,
            COUNT(*)                                                   AS n,
            ROUND(AVG(po.excess_return)::numeric * 100, 3)             AS mean_excess_pct,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
                (ORDER BY po.excess_return)::numeric * 100, 3)         AS median_excess_pct,
            ROUND(AVG((po.direction_correct)::int)::numeric * 100, 1)  AS dir_acc_pct
        FROM prediction_outcomes po
        WHERE po.resolved_at IS NOT NULL
          AND po.excess_return IS NOT NULL
          AND po.horizon_days IN (21, 63)
        GROUP BY po.horizon_days
        ORDER BY po.horizon_days
    """,
    )
    print(f"\n{'horizon':>8} {'n':>9} {'mean_exc':>10} {'median_exc':>11} {'dir_acc':>9}")
    print("-" * 52)
    for h, n, mean, med, acc in rows:
        print(f"{h:>8}d {n:>9,} {float(mean):>+9.3f}% {float(med):>+10.3f}% {float(acc):>8.1f}%")

    # ------------------------------------------------------------------
    # 5. Threshold sensitivity: what changes if we tighten ADX threshold?
    # ------------------------------------------------------------------
    section("5. Threshold sensitivity: regime split at different ADX cutoffs")

    rows = _run(
        conn,
        """
        SELECT
            threshold,
            SUM(CASE WHEN adx_14 >= threshold OR ABS(sma200_slope) >= 0.02 THEN 1 ELSE 0 END) AS trending_n,
            SUM(CASE WHEN adx_14 < threshold AND ABS(sma200_slope) < 0.02 THEN 1 ELSE 0 END) AS ranging_n,
            ROUND(
                SUM(CASE WHEN adx_14 >= threshold OR ABS(sma200_slope) >= 0.02 THEN 1 ELSE 0 END)
                * 100.0 / COUNT(*), 1
            ) AS trending_pct
        FROM signal_features,
        LATERAL (VALUES (20), (25), (30), (35), (40)) AS t(threshold)
        WHERE adx_14 IS NOT NULL AND sma200_slope IS NOT NULL
        GROUP BY threshold
        ORDER BY threshold
    """,
    )
    print(f"\n{'ADX_cutoff':>10} {'trending_n':>12} {'ranging_n':>11} {'trending_pct':>13}")
    print("-" * 50)
    for threshold, t_n, r_n, t_pct in rows:
        flag = "  ← current" if threshold == 25 else ""
        print(f"{threshold:>10} {t_n:>12,} {r_n:>11,} {float(t_pct):>12.1f}%{flag}")

    conn.close()
    print()
    print("VERDICT GUIDE")
    print("  Persistence median < 5d  → regime flips daily, thresholds too loose")
    print("  ranging excess_21d > 0   → mean-reversion hypothesis supported")
    print("  trending-down excess_21d < 0 → falling-knife hypothesis supported")
    print("  If both hold: two-channel design GO")
    print("  If neither: single-channel (ranging/reversion only) or revisit thresholds")


if __name__ == "__main__":
    main()
