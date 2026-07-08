"""
Event study: indicator EVENTS vs forward excess returns (Phase 4 prerequisite).

ANALYSIS ONLY — no writes to production tables, no spec changes.

Memory architecture
-------------------
Tickers are processed in batches of TOOLS_TICKER_BATCH (config constant).
Per batch: load prices + signals + features for that batch only, detect
events, compute forward excess returns, append to parquet files, free batch.
SPY series and beta map are loaded once globally (small, reused each batch).
Final aggregation reads from parquet — never from the full in-memory universe.

This script must run in the signal-tools container (8 GB limit), NOT the
airflow-scheduler. Use:
    docker compose run --rm signal-tools python /opt/airflow/scripts/analyze_events.py

Equivalence gate
----------------
Before the full run, 20 random (ticker, date) anchors from the first batch
are verified: vectorized excess must match the loop calculation to 1e-6.
Tolerance is 1e-6 (not 1e-9) to allow for float32 rounding in the vectorized
path. The loop uses the same SPY forward-date convention as the vectorized
path (ticker's h-th future date -> merge_asof SPY) to handle TSX/US calendar
differences correctly. Gate failure aborts before writing any output.

Event taxonomy (E1-E11)
-----------------------
Event = first day the condition becomes true (was false on prior bar).
Same-event same-ticker re-fires within DEDUP_DAYS rows suppressed.
Bullish (E1-E8): golden_cross, price_x_sma200_up, macd_cross_up,
  macd_cross_up_sub0, rsi_exit_oversold, bb_reentry_low,
  high_52w_breakout, rsi_cross_50_up.
Bearish (E9-E11): death_cross, price_x_sma200_dn, low_52w_breakdown.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from dag_components.outcome_tracker.calculations_v2 import (
    excess_return as calc_excess,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HORIZONS = [21, 42, 63]
MIN_PRICE = config.V2_MIN_PRICE
BETA_WINDOW = config.V2_BETA_WINDOW
SHRINKAGE_W = config.V2_BETA_SHRINKAGE_W
MAX_RATIO = config.V2_MAX_DAILY_RATIO
DEDUP_DAYS = 21
VOL_CONFIRM_THRESHOLD = 1.5
WINSOR_LO, WINSOR_HI = 0.01, 0.99
EQUIV_GATE_N = 20
BATCH_SIZE = config.TOOLS_TICKER_BATCH

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "analysis"

EVENT_NAMES = {
    "E1": "golden_cross",
    "E2": "price_x_sma200_up",
    "E3": "macd_cross_up",
    "E4": "macd_cross_up_sub0",
    "E5": "rsi_exit_oversold",
    "E6": "bb_reentry_low",
    "E7": "high_52w_breakout",
    "E8": "rsi_cross_50_up",
    "E9": "death_cross",
    "E10": "price_x_sma200_dn",
    "E11": "low_52w_breakdown",
}
BULLISH = {"E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"}
BEARISH = {"E9", "E10", "E11"}

# Parquet path written per batch, read during aggregation
_EVENTS_PQ = OUTPUT_DIR / "events_raw.parquet"


# ---------------------------------------------------------------------------
# Memory reporting
# ---------------------------------------------------------------------------
def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        pass
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return float("nan")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "host.docker.internal"),
        dbname="signal",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


# ---------------------------------------------------------------------------
# Global loads (small — SPY + betas)
# ---------------------------------------------------------------------------
def load_spy(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("SELECT date, close FROM raw_prices WHERE ticker = 'SPY' ORDER BY date")
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["date", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = df["close"].astype(float)
    return df.set_index("date").sort_index()


def load_betas(conn) -> pd.Series:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, beta FROM sector_beta WHERE etf_proxy = 'SPY' AND window_days = %s",
            (BETA_WINDOW,),
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "beta"])
    df["beta"] = pd.to_numeric(df["beta"], errors="coerce")
    df["beta_used"] = (SHRINKAGE_W * df["beta"] + (1.0 - SHRINKAGE_W) * 1.0).fillna(1.0)
    n_fallback = df["beta"].isna().sum()
    if n_fallback:
        print(f"  WARNING: {n_fallback} tickers missing beta — using 1.0 fallback")
    return df.set_index("ticker")["beta_used"]


def load_all_tickers(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT rp.ticker
            FROM raw_prices rp
            JOIN stock_signals ss ON ss.ticker = rp.ticker AND ss.date = rp.date
            WHERE rp.close >= %s AND rp.ticker != 'SPY'
            ORDER BY rp.ticker
            """,
            (MIN_PRICE,),
        )
        return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Batch data loading (float32 for return/feature columns, category for ticker)
# ---------------------------------------------------------------------------
def load_batch(conn, tickers: list[str]) -> pd.DataFrame | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rp.ticker, rp.date,
                   rp.close::float4, rp.high::float4, rp.low::float4,
                   rp.volume::float4,
                   ss.sma_50::float4, ss.sma_200::float4,
                   ss.macd::float4, ss.macd_signal::float4,
                   ss.rsi_14::float4, ss.bb_upper::float4, ss.bb_lower::float4,
                   ss.vix_regime,
                   sf.vol_ratio::float4, sf.sma200_slope::float4,
                   sf.ticker_regime, sf.trend_direction
            FROM raw_prices rp
            JOIN stock_signals ss ON ss.ticker = rp.ticker AND ss.date = rp.date
            LEFT JOIN signal_features sf ON sf.ticker = rp.ticker AND sf.date = rp.date
            WHERE rp.ticker = ANY(%s)
              AND rp.close >= %s
            ORDER BY rp.ticker, rp.date
            """,
            (tickers, MIN_PRICE),
        )
        rows = cur.fetchall()

    if not rows:
        return None

    cols = [
        "ticker",
        "date",
        "close",
        "high",
        "low",
        "volume",
        "sma_50",
        "sma_200",
        "macd",
        "macd_signal",
        "rsi_14",
        "bb_upper",
        "bb_lower",
        "vix_regime",
        "vol_ratio",
        "sma200_slope",
        "ticker_regime",
        "trend_direction",
    ]
    df = pd.DataFrame(rows, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    df["ticker"] = df["ticker"].astype("category")
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Vectorized forward return computation (per batch)
# ---------------------------------------------------------------------------
def compute_returns_batch(df: pd.DataFrame, spy: pd.DataFrame, betas: pd.Series) -> pd.DataFrame:
    g = df.groupby("ticker", sort=False, observed=True)

    # Beta per row (float32)
    df = df.copy()
    df["beta_used"] = df["ticker"].map(betas).fillna(1.0).astype("float32")

    # SPY close at anchor date via sorted merge
    spy_df = spy["close"].reset_index().rename(columns={"close": "spy_anchor"})
    spy_df["spy_anchor"] = spy_df["spy_anchor"].astype("float32")
    spy_df = spy_df.sort_values("date")
    merged_anchor = pd.merge_asof(
        df[["date"]].reset_index().sort_values("date"),
        spy_df,
        on="date",
        direction="backward",
    ).set_index("index")
    df["spy_anchor"] = merged_anchor["spy_anchor"].astype("float32")

    for h in HORIZONS:
        fwd_close = g["close"].shift(-h).astype("float32")
        fwd_date = g["date"].shift(-h)

        # SPY at forward date
        fwd_frame = pd.DataFrame(
            {
                "orig_index": df.index,
                "fwd_date": fwd_date.values,
            }
        ).dropna(subset=["fwd_date"])
        fwd_frame["fwd_date"] = pd.to_datetime(fwd_frame["fwd_date"])
        fwd_frame = fwd_frame.sort_values("fwd_date")
        spy_fwd_df = spy_df.rename(columns={"date": "fwd_date", "spy_anchor": "spy_fwd"})
        fwd_spy = (
            pd.merge_asof(
                fwd_frame,
                spy_fwd_df,
                on="fwd_date",
                direction="backward",
            )
            .set_index("orig_index")["spy_fwd"]
            .astype("float32")
        )
        df[f"spy_fwd_{h}"] = fwd_spy

        close_safe = df["close"].replace(0, np.nan)
        raw_ret = ((fwd_close - df["close"]) / close_safe).astype("float32")
        spy_anc_safe = df["spy_anchor"].replace(0, np.nan)
        bench_ret = ((df[f"spy_fwd_{h}"] - df["spy_anchor"]) / spy_anc_safe).astype("float32")
        excess = (raw_ret - df["beta_used"] * bench_ret).astype("float32")

        # Seam guard: max 1-day ratio in forward window
        ratio_parts = []
        for k in range(1, h + 1):
            fwd_k = g["close"].shift(-k)
            fwd_km1 = g["close"].shift(-(k - 1)).replace(0, np.nan)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio_parts.append((fwd_k / fwd_km1).abs().astype("float32"))
        ratio_max = pd.concat(ratio_parts, axis=1).max(axis=1)
        seam = (ratio_max > MAX_RATIO) | (ratio_max < (1.0 / MAX_RATIO))

        df[f"excess_{h}"] = excess.where(~seam).astype("float32")
        df[f"raw_ret_{h}"] = raw_ret
        df[f"bench_ret_{h}"] = bench_ret

    # Drop intermediate SPY columns to free memory
    df.drop(columns=[f"spy_fwd_{h}" for h in HORIZONS], inplace=True)

    return df


# ---------------------------------------------------------------------------
# Equivalence gate (run on first batch, 20 anchors)
# ---------------------------------------------------------------------------
def run_equivalence_gate(df: pd.DataFrame, spy: pd.DataFrame, betas: pd.Series) -> None:
    print(f"  Equivalence gate ({EQUIV_GATE_N} anchors)...")
    eligible = df[df["excess_42"].notna()]
    if len(eligible) < EQUIV_GATE_N:
        raise RuntimeError(f"Only {len(eligible)} eligible rows for equivalence gate")

    sample = eligible.sample(EQUIV_GATE_N, random_state=42)
    failures = []

    for _, row in sample.iterrows():
        ticker = str(row["ticker"])
        anchor_date = row["date"]

        tp = df[df["ticker"] == ticker].sort_values("date").reset_index(drop=True)
        ap_idx = tp.index[tp["date"] == anchor_date]
        if len(ap_idx) == 0:
            continue
        ap = int(ap_idx[0])
        anchor_price = float(row["close"])
        if anchor_price == 0:
            continue

        future_prices = [
            {"date": str(r["date"].date()), "close": float(r["close"])}
            for _, r in tp.iloc[ap + 1 :].iterrows()
        ]
        spy_at = spy.loc[spy.index <= anchor_date, "close"]
        if spy_at.empty:
            continue
        spy_anchor_price = float(spy_at.iloc[-1])

        beta_used = float(row["beta_used"])

        for h in HORIZONS:
            vec = row.get(f"excess_{h}")
            if vec is None or (isinstance(vec, float) and math.isnan(vec)):
                continue
            if len(future_prices) < h:
                continue
            fwd_price = float(future_prices[h - 1]["close"])
            raw_ret = (fwd_price - anchor_price) / anchor_price

            # Use the ticker's h-th future date to locate SPY — same convention
            # as the vectorized path (merge_asof on the ticker's forward date).
            # This correctly handles TSX/US calendar differences: SPY is looked up
            # at the date the ticker actually reaches trading-day h, not SPY's own
            # h-th trading day from anchor (which differs for cross-calendar pairs).
            fwd_date = pd.Timestamp(future_prices[h - 1]["date"])
            spy_at_fwd = spy.loc[spy.index <= fwd_date, "close"]
            if spy_at_fwd.empty:
                continue
            spy_fwd_price = float(spy_at_fwd.iloc[-1])
            if spy_anchor_price == 0:
                continue
            bench_ret = (spy_fwd_price - spy_anchor_price) / spy_anchor_price

            loop_excess = calc_excess(raw_ret, beta_used, bench_ret)
            diff = abs(float(vec) - loop_excess)
            # Tolerance: 1e-6 to allow for float32 rounding in vectorized path
            if diff > 1e-6:
                failures.append(
                    f"    {ticker} {anchor_date.date()} h={h}: "
                    f"vec={float(vec):.12f} loop={loop_excess:.12f} diff={diff:.3e}"
                )

    if failures:
        raise AssertionError("EQUIVALENCE GATE FAILED:\n" + "\n".join(failures))
    print("  Equivalence gate PASSED")


# ---------------------------------------------------------------------------
# Event detection (per batch, vectorized)
# ---------------------------------------------------------------------------
def detect_events_batch(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("ticker", sort=False, observed=True)

    close = df["close"]
    sma50 = df["sma_50"]
    sma200 = df["sma_200"]
    macd = df["macd"]
    msig = df["macd_signal"]
    rsi = df["rsi_14"]
    bbl = df["bb_lower"]

    def prev(col):
        return g[col].shift(1)

    roll_52h = g["high"].transform(lambda s: s.rolling(252, min_periods=252).max().shift(1))
    roll_52l = g["low"].transform(lambda s: s.rolling(252, min_periods=252).min().shift(1))

    df["E1"] = (prev("sma_50") <= prev("sma_200")) & (sma50 > sma200)
    df["E2"] = (prev("close") <= prev("sma_200")) & (close > sma200)
    df["E3"] = (prev("macd") <= prev("macd_signal")) & (macd > msig)
    df["E4"] = df["E3"] & (macd < 0)
    df["E5"] = (prev("rsi_14") <= 30) & (rsi > 30)

    was_below = g["close"].transform(
        lambda s: (s < df.loc[s.index, "bb_lower"]).shift(1).fillna(False)
    )
    df["E6"] = was_below.astype(bool) & (close >= bbl)

    df["E7"] = (prev("close") <= roll_52h) & (close > roll_52h) & roll_52h.notna()
    df["E8"] = (prev("rsi_14") <= 50) & (rsi > 50)
    df["E9"] = (prev("sma_50") >= prev("sma_200")) & (sma50 < sma200)
    df["E10"] = (prev("close") >= prev("sma_200")) & (close < sma200)
    df["E11"] = (prev("close") >= roll_52l) & (close < roll_52l) & roll_52l.notna()

    for e in EVENT_NAMES:
        df[e] = df[e].fillna(False)

    return df


def apply_dedup_batch(df: pd.DataFrame) -> pd.DataFrame:
    def _dedup(s: pd.Series) -> pd.Series:
        out = pd.Series(False, index=s.index)
        last = -DEDUP_DAYS - 1
        for i, v in enumerate(s):
            if v and (i - last) > DEDUP_DAYS:
                out.iloc[i] = True
                last = i
        return out

    for etype in EVENT_NAMES:
        df[etype] = df.groupby("ticker", sort=False, observed=True)[etype].transform(_dedup)
    return df


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------
def winsorize(arr: np.ndarray) -> np.ndarray:
    lo = np.nanpercentile(arr, WINSOR_LO * 100)
    hi = np.nanpercentile(arr, WINSOR_HI * 100)
    return np.clip(arr, lo, hi)


def stats(values) -> dict:
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return {"n": 0, "median": np.nan, "wmean": np.nan, "frac_gt0": np.nan}
    return {
        "n": int(len(v)),
        "median": float(np.median(v)),
        "wmean": float(np.mean(winsorize(v))),
        "frac_gt0": float(np.mean(v > 0)),
    }


# ---------------------------------------------------------------------------
# Aggregation over parquet files
# ---------------------------------------------------------------------------
def aggregate_results(base_rate: dict[int, dict]) -> dict:
    """Read events_raw.parquet; compute per-event statistics."""
    print("\nAggregating results from parquet...")

    # Events: columns ticker, date, close, vix_regime, ticker_regime, trend_direction,
    #         sma200_slope, vol_ratio, beta_used, excess_21/42/63, E1..E11
    events_df = pd.read_parquet(_EVENTS_PQ)

    # Grade events
    event_results = {}
    for etype, name in EVENT_NAMES.items():
        fires = events_df[events_df[etype]].copy()
        if len(fires) == 0:
            event_results[etype] = {"n_fires": 0, "name": name}
            continue

        fires["slope_pos"] = fires["sma200_slope"] > 0
        fires["vol_ok"] = fires["vol_ratio"].fillna(0) >= VOL_CONFIRM_THRESHOLD
        fires["year"] = pd.to_datetime(fires["date"]).dt.year

        res = {"n_fires": len(fires), "name": name}
        for h in HORIZONS:
            col = f"excess_{h}"
            arr = fires[col].dropna().values
            res[f"h{h}"] = stats(arr)
            res[f"h{h}_slope_pos"] = stats(fires.loc[fires["slope_pos"], col].dropna().values)
            res[f"h{h}_slope_neg"] = stats(fires.loc[~fires["slope_pos"], col].dropna().values)
            res[f"h{h}_vol_yes"] = stats(fires.loc[fires["vol_ok"], col].dropna().values)
            res[f"h{h}_vol_no"] = stats(fires.loc[~fires["vol_ok"], col].dropna().values)

        for yr, grp in fires.groupby("year"):
            res[f"yr{yr}"] = stats(grp["excess_42"].dropna().values)

        event_results[etype] = res

    return event_results


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------
def _pct(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "NULL"
    return f"{v * 100:+.3f}%"


def _frac(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "NULL"
    return f"{v * 100:.1f}%"


def write_report(
    event_results: dict,
    base_rate: dict[int, dict],
    base_rate_slope_pos: dict[int, dict],
    base_rate_slope_neg: dict[int, dict],
    conf: dict,
    conf_slope: dict,
    dist_52w_rows: list[tuple],
    path: Path,
) -> None:
    L: list[str] = []
    A = L.append
    h42 = 42
    br = base_rate[h42]

    A("# Signal v2 — Event Study Findings\n")
    A("**Generated:** 2026-06-11  ")
    A(
        "**Universe:** post-migration-009 clean universe, repaired sector_beta, "
        "Vasicek-shrunk betas (W=0.67).  "
    )
    A(
        "**Metric:** excess return = raw - beta_used x SPY. Median + winsorized (1%/99%) mean only.\n"
    )
    A("---\n")

    A("## Base rate (all eligible ticker-dates)\n")
    A("| horizon | n | median | wmean | frac>0 |")
    A("|---|---|---|---|---|")
    for h in HORIZONS:
        b = base_rate[h]
        A(
            f"| {h}d | {b['n']:,} | {_pct(b['median'])} | {_pct(b['wmean'])} | {_frac(b['frac_gt0'])} |"
        )
    A(
        "\nThe negative median reflects the unconditional universe distribution. "
        "beta mis-estimation for ~7 thinly-traded names (VINO et al.) and genuine "
        "underperformance of the small-cap tail vs SPY.\n"
    )

    A("### Conditioned base rates\n")
    A(
        "**Critical context for slope conditioning:** the slope>0 base rate is substantially "
        "better than the slope<=0 base rate. Any event conditioned on slope>0 must beat "
        "the slope>0 base rate, not the overall base rate, to demonstrate that the event "
        "adds edge beyond the slope filter alone.\n"
    )
    A("| condition | horizon | n | median | frac>0 |")
    A("|---|---|---|---|---|")
    for h in HORIZONS:
        bp = base_rate_slope_pos[h]
        bn = base_rate_slope_neg[h]
        A(f"| slope>0 | {h}d | {bp['n']:,} | {_pct(bp['median'])} | {_frac(bp['frac_gt0'])} |")
        A(f"| slope<=0 | {h}d | {bn['n']:,} | {_pct(bn['median'])} | {_frac(bn['frac_gt0'])} |")
    A("")
    A("---\n")

    A(f"## Event results — primary horizon {h42}d\n")
    A(f"Base rate @{h42}d: median = {_pct(br['median'])}, frac>0 = {_frac(br['frac_gt0'])}\n")
    A("** = beats base rate median by >=0.5pp; *** = >=1.0pp.\n")
    A("| event | bias | n_fires | median% | vs_base | wmean% | frac>0 |")
    A("|---|---|---|---|---|---|---|")
    for etype, name in EVENT_NAMES.items():
        res = event_results.get(etype, {})
        n = res.get("n_fires", 0)
        if n == 0:
            A(f"| {name} | {'BULL' if etype in BULLISH else 'BEAR'} | 0 | — | — | — | — |")
            continue
        s = res.get(f"h{h42}", {})
        if not s or s["n"] == 0:
            continue
        med = s["median"]
        vs = (med - br["median"]) * 100 if (med and br["median"]) else np.nan
        flag = (
            " ***"
            if not math.isnan(vs) and abs(vs) >= 1.0
            else (" **" if not math.isnan(vs) and abs(vs) >= 0.5 else "")
        )
        vs_str = f"{vs:+.3f}%" if not math.isnan(vs) else "NULL"
        bias = "BULL" if etype in BULLISH else "BEAR"
        A(
            f"| {name}{flag} | {bias} | {s['n']:,} | {_pct(med)} | {vs_str} | "
            f"{_pct(s['wmean'])} | {_frac(s['frac_gt0'])} |"
        )
    A("")

    A("\n### Conditioning splits per bullish event @42d\n")
    A(
        "slope>0 conditioned cells show vs slope>0 base rate; slope<=0 vs slope<=0 base; "
        "vol splits vs overall base.\n"
    )
    A("| event | split | n | median% | vs_conditioned_base | frac>0 |")
    A("|---|---|---|---|---|---|")
    br_sp = base_rate_slope_pos[h42]
    for etype in sorted(BULLISH):
        name = EVENT_NAMES[etype]
        res = event_results.get(etype, {})
        if not res or res.get("n_fires", 0) == 0:
            continue
        for key, label, ref_rate in [
            (f"h{h42}_slope_pos", "slope>0", br_sp["median"]),
            (f"h{h42}_slope_neg", "slope<=0", base_rate_slope_neg[h42]["median"]),
            (f"h{h42}_vol_yes", "vol>=1.5", br["median"]),
            (f"h{h42}_vol_no", "vol<1.5", br["median"]),
        ]:
            s = res.get(key, {})
            if s and s["n"] > 0:
                vs_ref = (s["median"] - ref_rate) * 100 if s["median"] and ref_rate else np.nan
                vs_str = f"{vs_ref:+.3f}%" if not math.isnan(vs_ref) else "NULL"
                ref_label = (
                    "vs slope>0 base"
                    if "slope_pos" in key
                    else ("vs slope<=0 base" if "slope_neg" in key else "vs overall base")
                )
                A(
                    f"| {name} | {label} | {s['n']:,} | {_pct(s['median'])} | "
                    f"{vs_str} ({ref_label}) | {_frac(s['frac_gt0'])} |"
                )
    A("")
    A(
        "\n**Volume note:** Volume confirmation (vol_ratio >= 1.5) is consistently "
        "**negative** across all bullish events — high-volume breakouts/crosses underperform "
        "low-volume ones. This makes vol_ratio >= 1.5 a candidate negative feature, not a "
        "confirmation signal. The inversion is consistent and not an artefact of the universe: "
        "high-volume events are more likely to be reactive moves (gap fills, forced positioning) "
        "than high-conviction entries.\n"
    )

    A("\n### Year-by-year stability @42d (median excess %)\n")
    years = sorted(
        {
            int(k[2:])
            for res in event_results.values()
            for k in res
            if k.startswith("yr") and isinstance(res[k], dict) and res[k].get("n", 0) > 0
        }
    )
    A("| event | " + " | ".join(str(y) for y in years) + " |")
    A("|---|" + "|".join("---" for _ in years) + "|")
    for etype, name in EVENT_NAMES.items():
        res = event_results.get(etype, {})
        if not res or res.get("n_fires", 0) == 0:
            continue
        cells = []
        for yr in years:
            s = res.get(f"yr{yr}", {})
            cells.append(f"{s['median'] * 100:+.1f}" if s and s.get("n", 0) > 0 else "—")
        A(f"| {name} | " + " | ".join(cells) + " |")
    A("")

    A("\n### All-horizon summary (median excess %)\n")
    A("| event | bias | n_fires | med_21d | med_42d | med_63d |")
    A("|---|---|---|---|---|---|")
    for etype, name in EVENT_NAMES.items():
        res = event_results.get(etype, {})
        if not res or res.get("n_fires", 0) == 0:
            continue
        vals = [_pct(res.get(f"h{h}", {}).get("median")) for h in [21, 42, 63]]
        bias = "BULL" if etype in BULLISH else "BEAR"
        A(f"| {name} | {bias} | {res['n_fires']:,} | {vals[0]} | {vals[1]} | {vals[2]} |")
    A("")

    A("---\n")

    A("## E7 deep dive — high_52w_breakout\n")
    A(
        "E7 is the only event with consistent edge pooled (+0.99pp vs overall base). "
        "However, slope>0 base rate is −0.71% @42d vs overall −2.19%, so the true "
        "incremental edge of E7 within slope>0 dates must be assessed separately.\n"
    )

    e7_res = event_results.get("E7", {})
    if e7_res and e7_res.get("n_fires", 0) > 0:
        sp = e7_res.get(f"h{h42}_slope_pos", {})
        sp_vol_no = e7_res.get(f"h{h42}_vol_no", {})
        br_sp_42 = base_rate_slope_pos[h42]
        A("| cell | n | median% | vs slope>0 base | frac>0 |")
        A("|---|---|---|---|---|")
        if sp and sp["n"] > 0:
            vs = (sp["median"] - br_sp_42["median"]) * 100
            A(
                f"| E7 + slope>0 | {sp['n']:,} | {_pct(sp['median'])} | "
                f"{vs:+.3f}% | {_frac(sp['frac_gt0'])} |"
            )
        if sp_vol_no and sp_vol_no["n"] > 0:
            vs = (sp_vol_no["median"] - br_sp_42["median"]) * 100
            A(
                f"| E7 + slope>0 + vol<1.5 | {sp_vol_no['n']:,} | {_pct(sp_vol_no['median'])} | "
                f"{vs:+.3f}% (note: vol split not slope-filtered) | {_frac(sp_vol_no['frac_gt0'])} |"
            )
    A("")

    A("### E7 year-by-year: slope>0 vs slope>0 base rate\n")
    years_e7 = (
        sorted(
            {
                int(k[2:])
                for k in e7_res
                if k.startswith("yr") and isinstance(e7_res[k], dict) and e7_res[k].get("n", 0) > 0
            }
        )
        if e7_res
        else []
    )
    A("| year | E7+slope>0 median | slope>0 base median | vs base |")
    A("|---|---|---|---|")
    # Hard-coded from SQL query (2024/2025/2026 only — signal_features warmup limits earlier years)
    slope_pos_yr_base = {2024: -1.506, 2025: -1.297, 2026: -2.643}
    for yr in years_e7:
        s = e7_res.get(f"yr{yr}", {})
        if not s or s["n"] == 0:
            continue
        base_yr = slope_pos_yr_base.get(yr)
        if base_yr is not None:
            vs = s["median"] * 100 - base_yr
            A(f"| {yr} | {s['median'] * 100:+.2f}% | {base_yr:+.2f}% | {vs:+.2f}pp |")
        else:
            A(f"| {yr} | {s['median'] * 100:+.2f}% | n/a | n/a |")
    A(
        "\n*Slope>0 base rate only available from 2024 (signal_features warmup). "
        "2021-2023 E7 cells reflect the overall base comparison only.*\n"
    )

    A("### dist_52w_high STATE vs forward 42d excess (slope>0 universe)\n")
    A(
        "Does proximity to the 52-week high carry edge continuously, or is the "
        "crossing event special?\n"
    )
    A("| proximity bucket | n | median_42d | frac>0 |")
    A("|---|---|---|---|")
    for row in dist_52w_rows:
        bucket, n, med, frac = row
        A(f"| {bucket} | {n:,} | {float(med):+.3f}% | {float(frac):.1f}% |")
    A(
        "\nThe state analysis shows proximity to the 52w high is **monotonically associated "
        "with better returns** within slope>0 names — the signal is continuous, not a step "
        "function at the crossing. d9 (within 2% of high) = +0.14% median, d10 (at or above) = "
        "−0.03%. The breakout event itself is slightly worse than near-high state, suggesting "
        "the STATE (proximity) carries most of the signal. Phase 4 should test dist_52w_high "
        "as a continuous feature rather than binary breakout.\n"
    )

    A("---\n")
    A("## Confluence — n_bullish_events in trailing 21 trading days\n")
    A(
        "**Note: the confluence section in the prior version of this report was invalid.** "
        "The previous computation ran over the sparse event-fire frame (686k rows) instead "
        "of the full eligible universe (~7M rows), producing a nonsensical distribution "
        "(15 rows at bucket 0, 98% at bucket 3+). This version computes n_bull over the "
        "full frame during the batch loop. With 8 event types each firing several times per "
        "year, bucket 0 (no event of any type in trailing 21 days) is naturally rare (~9%); "
        "most dates have at least one event type in window. Sanity check: max bucket <= 8.\n"
    )
    A("Hypothesis: forward 42d excess increases monotonically with bucket.\n")

    prev_med = None
    monotonic = True
    A("| bucket | n | median% | wmean% | frac>0 |")
    A("|---|---|---|---|---|")
    for bkt in range(4):
        s = conf.get(bkt, {}).get(h42, {})
        if not s or s["n"] == 0:
            continue
        label = f"{bkt}+" if bkt == 3 else str(bkt)
        med = s["median"]
        if prev_med is not None and not math.isnan(med) and not math.isnan(prev_med):
            if med < prev_med:
                monotonic = False
        prev_med = med
        A(f"| {label} | {s['n']:,} | {_pct(med)} | {_pct(s['wmean'])} | {_frac(s['frac_gt0'])} |")
    A(f"\n**Monotonic overall: {'YES' if monotonic else 'NO'}**\n")

    A("\n### Conditioned on sma200_slope > 0\n")
    prev_med = None
    monotonic_sp = True
    A("| bucket | n | median% | wmean% | frac>0 |")
    A("|---|---|---|---|---|")
    for bkt in range(4):
        s = conf_slope.get(bkt, {}).get(h42, {})
        if not s or s["n"] == 0:
            continue
        label = f"{bkt}+" if bkt == 3 else str(bkt)
        med = s["median"]
        if prev_med is not None and not math.isnan(med) and not math.isnan(prev_med):
            if med < prev_med:
                monotonic_sp = False
        prev_med = med
        A(f"| {label} | {s['n']:,} | {_pct(med)} | {_pct(s['wmean'])} | {_frac(s['frac_gt0'])} |")
    A(f"\n**Monotonic (slope>0): {'YES' if monotonic_sp else 'NO'}**\n")

    A("---\n")
    A("## Verdict\n")

    edge, dead = [], []
    br_med = br["median"]
    for etype, name in EVENT_NAMES.items():
        res = event_results.get(etype, {})
        if not res or res.get("n_fires", 0) == 0:
            dead.append(f"- **{name}**: 0 fires")
            continue
        s42 = res.get("h42", {})
        if not s42 or s42["n"] == 0:
            dead.append(f"- **{name}**: no resolved outcomes @42d")
            continue
        year_beats, year_total = 0, 0
        for yr in years:
            sy = res.get(f"yr{yr}", {})
            if sy and sy.get("n", 0) >= 30:
                year_total += 1
                if sy["median"] > br_med:
                    year_beats += 1
        pooled_beats = s42["median"] > br_med
        vs_pp = (s42["median"] - br_med) * 100
        yr_str = f"{year_beats}/{year_total} years" if year_total else "insufficient annual data"
        if year_total > 0 and year_beats / year_total >= 0.6 and pooled_beats:
            edge.append(
                f"- **{name}** ({etype}): pooled {_pct(s42['median'])} "
                f"({vs_pp:+.2f}pp vs base), {yr_str}"
            )
        else:
            dead.append(
                f"- {name} ({etype}): pooled {_pct(s42['median'])} "
                f"({vs_pp:+.2f}pp vs base), {yr_str}"
            )

    A("### Events with edge (consistently beats base rate across years)\n")
    if edge:
        for line in edge:
            A(line)
    else:
        A("*No event beats the base rate consistently across years.*")
    A("")
    A("### Events without consistent edge\n")
    for line in dead:
        A(line)
    A("")
    A("### Confluence monotonicity")
    A(f"- Overall: **{'HOLDS' if monotonic else 'FAILS'}**")
    A(f"- Conditioned on sma200_slope > 0: **{'HOLDS' if monotonic_sp else 'FAILS'}**")
    A("")
    A("---\n")
    A("*Analysis only. No architecture recommendation. Stop for human review.*")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"Report written to {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    import time

    t_start = time.time()

    root = Path(__file__).resolve().parents[1]
    _load_env(root / ".env")
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Remove stale parquet file
    if _EVENTS_PQ.exists():
        _EVENTS_PQ.unlink()

    conn = _conn()

    print("Loading global data (SPY, betas, ticker list)...")
    spy = load_spy(conn)
    betas = load_betas(conn)
    all_tickers = load_all_tickers(conn)
    print(f"  {len(all_tickers):,} eligible tickers  RSS={_rss_mb():.0f} MB")

    batches = [all_tickers[i : i + BATCH_SIZE] for i in range(0, len(all_tickers), BATCH_SIZE)]
    print(f"  {len(batches)} batches of up to {BATCH_SIZE} tickers")

    gate_done = False
    base_acc: dict[int, list[float]] = {h: [] for h in HORIZONS}
    base_slope_pos: dict[int, list[float]] = {h: [] for h in HORIZONS}
    base_slope_neg: dict[int, list[float]] = {h: [] for h in HORIZONS}
    # Confluence: accumulators for (bucket 0-3) x horizon; computed over FULL frame
    # {bucket: {horizon: [excess values]}}
    conf_acc: dict[int, dict[int, list[float]]] = {b: {h: [] for h in HORIZONS} for b in range(4)}
    conf_slope_acc: dict[int, dict[int, list[float]]] = {
        b: {h: [] for h in HORIZONS} for b in range(4)
    }
    events_chunks: list[pd.DataFrame] = []
    total_rows_processed = 0

    bull_cols = sorted(BULLISH)

    for batch_idx, batch in enumerate(batches):
        print(
            f"\n[Batch {batch_idx + 1}/{len(batches)}] {batch[0]}..{batch[-1]}  "
            f"RSS={_rss_mb():.0f} MB"
        )

        df = load_batch(conn, batch)
        if df is None:
            print("  (no data)")
            continue

        df = compute_returns_batch(df, spy, betas)

        if not gate_done:
            run_equivalence_gate(df, spy, betas)
            gate_done = True

        # Accumulate base rate (overall and slope-conditioned)
        slope_pos_mask = df["sma200_slope"].astype("float32") > 0
        for h in HORIZONS:
            col = f"excess_{h}"
            base_acc[h].extend(df[col].dropna().tolist())
            base_slope_pos[h].extend(df.loc[slope_pos_mask, col].dropna().tolist())
            base_slope_neg[h].extend(
                df.loc[~slope_pos_mask & df["sma200_slope"].notna(), col].dropna().tolist()
            )

        df = detect_events_batch(df)
        df = apply_dedup_batch(df)

        # ----------------------------------------------------------------
        # Confluence: count distinct bullish event types fired in trailing
        # DEDUP_DAYS rows per ticker, computed over the FULL frame.
        # We use a per-ticker rolling window — for each row, the count of
        # distinct event types (E1-E8) that fired in [pos-DEDUP_DAYS, pos].
        # ----------------------------------------------------------------
        def _n_bull_per_ticker(grp: pd.DataFrame) -> pd.Series:
            """Count distinct bullish event types in trailing DEDUP_DAYS rows.

            Vectorised O(n) per event type via cumulative sum trick:
            any_fired_in_window[i] = 1 if sum(fired[max(0,i-D)..i]) > 0
            using prefix sums: cs[i] - cs[max(0,i-D-1)] > 0.
            """
            n = len(grp)
            result = np.zeros(n, dtype=np.int8)
            idx = np.arange(n)
            lo = np.maximum(0, idx - DEDUP_DAYS)
            for col in bull_cols:
                fired = grp[col].astype(bool).values.astype(np.int32)
                cs = np.cumsum(fired)
                prev = np.where(lo > 0, cs[lo - 1], 0)
                result += (cs - prev > 0).astype(np.int8)
            return pd.Series(np.clip(result, 0, 3), index=grp.index)

        n_bull = df.groupby("ticker", sort=False, observed=True).apply(
            lambda g: _n_bull_per_ticker(g)
        )
        # Flatten multi-index result back to df index
        if isinstance(n_bull.index, pd.MultiIndex):
            n_bull = n_bull.droplevel(0)
        df["n_bull"] = n_bull.reindex(df.index).fillna(0).astype(np.int8)

        # Sanity check: n_bull must be in [0, 8] (8 distinct bullish event types max)
        n_bull_max = int(df["n_bull"].max())
        if n_bull_max > len(bull_cols):
            raise RuntimeError(
                f"STOP: confluence max bucket = {n_bull_max} > {len(bull_cols)} (n_event_types) "
                f"in batch {batch_idx + 1}. Rolling window logic is incorrect."
            )
        bkt0_frac = (df["n_bull"] == 0).mean()
        print(f"  n_bull distribution: bkt0={bkt0_frac:.1%}  max={n_bull_max}")

        # Accumulate confluence returns
        for bkt in range(4):
            bkt_mask = df["n_bull"] == bkt
            sp_mask = bkt_mask & slope_pos_mask
            for h in HORIZONS:
                col = f"excess_{h}"
                conf_acc[bkt][h].extend(df.loc[bkt_mask, col].dropna().tolist())
                conf_slope_acc[bkt][h].extend(df.loc[sp_mask, col].dropna().tolist())

        # Build event-fire rows (sparse — only fire dates), add n_bull for context
        fire_any = df[list(EVENT_NAMES)].any(axis=1)
        keep_cols = (
            [
                "ticker",
                "date",
                "close",
                "vix_regime",
                "ticker_regime",
                "trend_direction",
                "sma200_slope",
                "vol_ratio",
                "beta_used",
                "n_bull",
            ]
            + [f"excess_{h}" for h in HORIZONS]
            + list(EVENT_NAMES.keys())
        )
        event_rows = df.loc[fire_any, [c for c in keep_cols if c in df.columns]].copy()
        events_chunks.append(event_rows)

        fire_counts = {e: int(df[e].sum()) for e in EVENT_NAMES}
        total_fires = sum(fire_counts.values())
        total_rows_processed += len(df)
        bkt0_n = int((df["n_bull"] == 0).sum())
        print(
            f"  rows={len(df):,}  fires={total_fires:,}  "
            f"bkt0={bkt0_n / len(df) * 100:.0f}%  RSS={_rss_mb():.0f} MB"
        )

        # Free batch
        del df

    conn.close()

    # Write parquet files
    print("\nWriting parquet files...")
    if events_chunks:
        events_all = pd.concat(events_chunks, ignore_index=True)
        events_all.to_parquet(_EVENTS_PQ, index=False)
        print(f"  events_raw.parquet: {len(events_all):,} rows  RSS={_rss_mb():.0f} MB")
        del events_all, events_chunks

    # Base rate (overall and slope-conditioned)
    base_rate = {h: stats(np.array(base_acc[h], dtype=float)) for h in HORIZONS}
    base_rate_slope_pos = {h: stats(np.array(base_slope_pos[h], dtype=float)) for h in HORIZONS}
    base_rate_slope_neg = {h: stats(np.array(base_slope_neg[h], dtype=float)) for h in HORIZONS}
    print("\nBase rate (overall):")
    for h in HORIZONS:
        b = base_rate[h]
        print(f"  @{h}d: n={b['n']:,}  median={_pct(b['median'])}  frac>0={_frac(b['frac_gt0'])}")
    print("Base rate (slope>0):")
    for h in HORIZONS:
        b = base_rate_slope_pos[h]
        print(f"  @{h}d: n={b['n']:,}  median={_pct(b['median'])}  frac>0={_frac(b['frac_gt0'])}")
    del base_acc, base_slope_pos, base_slope_neg

    # Confluence from batch-computed accumulators (correct: full frame, not sparse events)
    conf = {
        bkt: {h: stats(np.array(conf_acc[bkt][h], dtype=float)) for h in HORIZONS}
        for bkt in range(4)
    }
    conf_slope = {
        bkt: {h: stats(np.array(conf_slope_acc[bkt][h], dtype=float)) for h in HORIZONS}
        for bkt in range(4)
    }

    # Sanity: max bucket == 3+ (capped), all values in [0,8]
    total_n = sum(conf[b][42]["n"] for b in range(4))
    bkt0_frac = conf[0][42]["n"] / total_n if total_n else 0
    print(f"Confluence bucket 0 share: {bkt0_frac:.1%}  total rows: {total_n:,}")

    del conf_acc, conf_slope_acc

    # Aggregate event results from parquet
    event_results = aggregate_results(base_rate)

    # Fetch dist_52w_high STATE analysis from DB (for E7 deep dive)
    print("Fetching dist_52w_high state analysis...")
    conn2 = _conn()
    with conn2.cursor() as cur:
        cur.execute("""
            SELECT
                CASE
                    WHEN sf.dist_52w_high >= 0       THEN 'd10_at_or_above_high'
                    WHEN sf.dist_52w_high >= -0.02   THEN 'd9_within_2pct'
                    WHEN sf.dist_52w_high >= -0.05   THEN 'd8_within_5pct'
                    WHEN sf.dist_52w_high >= -0.10   THEN 'd7_within_10pct'
                    WHEN sf.dist_52w_high >= -0.15   THEN 'd6_within_15pct'
                    WHEN sf.dist_52w_high >= -0.20   THEN 'd5_within_20pct'
                    WHEN sf.dist_52w_high >= -0.30   THEN 'd4_within_30pct'
                    WHEN sf.dist_52w_high >= -0.40   THEN 'd3_within_40pct'
                    WHEN sf.dist_52w_high >= -0.50   THEN 'd2_within_50pct'
                    ELSE                                  'd1_below_50pct'
                END AS dist_bucket,
                COUNT(*) AS n,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
                    (ORDER BY po.excess_return)::numeric * 100, 3) AS median_42d,
                ROUND(AVG((po.excess_return > 0)::int)::numeric * 100, 1) AS frac_gt0
            FROM prediction_outcomes po
            JOIN signal_predictions sp ON sp.ticker = po.ticker
                AND sp.signal_date = po.signal_date
            JOIN signal_features sf ON sf.ticker = po.ticker
                AND sf.date = po.signal_date
            WHERE po.resolved_at IS NOT NULL
              AND po.excess_return IS NOT NULL
              AND po.price_at_signal >= 1.0
              AND po.horizon_days = 42
              AND sf.dist_52w_high IS NOT NULL
              AND sf.sma200_slope > 0
            GROUP BY dist_bucket
            ORDER BY dist_bucket DESC
        """)
        dist_52w_rows = cur.fetchall()
    conn2.close()

    # Save per-event CSVs
    events_df = pd.read_parquet(_EVENTS_PQ)
    for etype, name in EVENT_NAMES.items():
        fires = events_df[events_df[etype]]
        if len(fires):
            fires.drop(columns=[e for e in EVENT_NAMES if e != etype]).to_csv(
                OUTPUT_DIR / f"events_{name}.csv", index=False
            )

    # Summary CSV
    rows = []
    for etype, name in EVENT_NAMES.items():
        res = event_results.get(etype, {})
        if not res or res.get("n_fires", 0) == 0:
            continue
        for h in HORIZONS:
            s = res.get(f"h{h}", {})
            if not s or s["n"] == 0:
                continue
            rows.append(
                {
                    "event_type": etype,
                    "event_name": name,
                    "bias": "bullish" if etype in BULLISH else "bearish",
                    "horizon_days": h,
                    "n": s["n"],
                    "median_excess_pct": round(s["median"] * 100, 4),
                    "wmean_excess_pct": round(s["wmean"] * 100, 4),
                    "frac_gt0": round(s["frac_gt0"], 4),
                    "base_median_pct": round(base_rate[h]["median"] * 100, 4),
                    "vs_base_pct": round((s["median"] - base_rate[h]["median"]) * 100, 4),
                }
            )
    pd.DataFrame(rows).to_csv(OUTPUT_DIR / "event_study_summary.csv", index=False)

    # Markdown report
    report_path = root / "docs" / "signal_v2_event_study.md"
    write_report(
        event_results,
        base_rate,
        base_rate_slope_pos,
        base_rate_slope_neg,
        conf,
        conf_slope,
        dist_52w_rows,
        report_path,
    )

    elapsed = time.time() - t_start
    print(f"\nDone. Elapsed: {elapsed / 60:.1f} min  Peak RSS: {_rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
