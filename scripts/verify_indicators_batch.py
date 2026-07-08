#!/usr/bin/env python3
"""Parity + peak-RSS gate for the batched indicator refactor.

Discharges three acceptance criteria for feature/indicators-batch-streaming:

1. PARITY (teeth, not vibes). For one recent full-data date, compute every
   ticker's signal row two ways —
     (a) LEGACY: load the whole universe's price history into one dict (the
         pre-batching shape), then run the per-ticker math; the resulting rows
         are round-tripped through JSON to reproduce the exact lossy XCom
         serialization the old DAG path applied between tasks;
     (b) BATCHED: stream the universe in INDICATOR_BATCH_SIZE blocks, computing
         rows via the SAME compute_ticker_row the task now uses (no JSON round
         trip — values never leave the process).
   Assert equal ticker sets, equal row counts, and per-column agreement within
   ABS_TOL (1e-9) for every ticker. Report the diff count. EXPECTED: 0.

   CAVEAT — read before "fixing" a near-zero diff: legacy floats round-trip
   through XCom's JSON (de)serialization; batched floats never leave the
   process. So discrepancies at the ~1e-12 level are the BATCHED path being
   *more* correct (no lossy serialization), NOT a regression. ABS_TOL=1e-9 sits
   above that noise floor and below any real algorithmic divergence: a handful
   of ~1e-12 diffs → pass (batched is more correct); anything > 1e-9 → real bug,
   stop. Do not tighten ABS_TOL toward 1e-12 to chase them — you'd be flagging
   correctness as failure.

2. PEAK RSS at full universe (migration plan §6.1 / open question 7). The
   batched path logs per-batch RSS; the peak is printed for infra reconciliation.

3. STREAMING (flat, not ramping). Per-batch RSS is reported so the profile can
   be eyeballed: flat across batch index == both the price-history blob AND the
   output rows stream (nothing universe-sized accumulates). A monotonic climb
   across batches is the tell that something is being retained — a bug even if
   parity passes. The script asserts the RSS *slope* is not materially rising.

Run against a populated signal DB (needs POSTGRES_* env / .env). Read-only:
computes into memory and compares; never writes to stock_signals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import config  # noqa: E402
from dag_components.indicators.pipeline import (  # noqa: E402
    build_vix_context,
    compute_ticker_row,
    read_prices,
    rss_mb,
)
from plugins.routing import resolve_vix_tickers  # noqa: E402
from scripts._db import get_connection, load_env  # noqa: E402

ABS_TOL = 1e-9
NUMERIC_COLS = [
    "sma_50",
    "sma_200",
    "macd",
    "macd_signal",
    "macd_hist",
    "rsi_14",
    "bb_upper",
    "bb_lower",
    "vix_close",
    "vvix_close",
    "composite_score",
    "composite_vix_adj",
]
STR_COLS = ["ticker", "date", "vix_source", "vix_regime", "vix_trend", "vol_environment"]


def _target_date(conn) -> str:
    """Most recent date that has stock_signals rows — guaranteed full-data."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM stock_signals")
        row = cur.fetchone()
    if not row or row[0] is None:
        sys.exit("No rows in stock_signals — populate the DB before running the gate.")
    return str(row[0])


def _equity_tickers(conn, target_date: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker FROM stock_signals WHERE date = %s ORDER BY ticker",
            (target_date,),
        )
        return [r[0] for r in cur.fetchall()]


def _round_trip_json(rows: list[dict]) -> list[dict]:
    """Reproduce the lossy JSON serialization the old XCom path applied."""
    return json.loads(json.dumps(rows))


def main() -> int:
    load_env()
    conn = get_connection()

    target_date = _target_date(conn)
    start_dt = None  # computed per path from config window below
    from datetime import date, timedelta

    tdt = date.fromisoformat(target_date)
    start_dt = tdt - timedelta(days=config.PRICE_HISTORY_DAYS + 30)

    vix_ticker, vvix_ticker = resolve_vix_tickers()
    equity = _equity_tickers(conn, target_date)
    batch_size = config.INDICATOR_BATCH_SIZE

    print(f"Target date: {target_date}   universe: {len(equity)} tickers   batch: {batch_size}")

    vix = build_vix_context(conn, vix_ticker, vvix_ticker, start_dt, tdt, target_date)

    # ---- LEGACY path: whole-universe dict, then JSON round-trip (old XCom) ----
    legacy_hist = read_prices(conn, equity, start_dt, tdt)
    legacy_rows = []
    for t in equity:
        r = compute_ticker_row(t, legacy_hist.get(t, []), target_date, vix)
        if r is not None:
            legacy_rows.append(r)
    legacy_rows = _round_trip_json(legacy_rows)
    legacy = {r["ticker"]: r for r in legacy_rows}
    del legacy_hist

    # ---- BATCHED path: stream, same math, no round-trip; log per-batch RSS ----
    batched = {}
    rss_profile = []
    n_batches = (len(equity) + batch_size - 1) // batch_size
    for b, i in enumerate(range(0, len(equity), batch_size), start=1):
        batch = equity[i : i + batch_size]
        hist = read_prices(conn, batch, start_dt, tdt)
        rows = []
        for t in batch:
            r = compute_ticker_row(t, hist.get(t, []), target_date, vix)
            if r is not None:
                rows.append(r)
        for r in rows:
            batched[r["ticker"]] = r
        rss = rss_mb()
        rss_profile.append(rss)
        print(
            f"  batch {b}/{n_batches} ({len(batch)} tickers) -> {len(rows)} rows | RSS {rss:.0f} MiB"
        )
        del hist, rows

    conn.close()

    # ---- PARITY ----
    diffs = 0
    only_legacy = set(legacy) - set(batched)
    only_batched = set(batched) - set(legacy)
    if only_legacy or only_batched:
        print(
            f"TICKER SET MISMATCH: legacy-only={len(only_legacy)} batched-only={len(only_batched)}"
        )
        for t in list(only_legacy)[:10]:
            print(f"    legacy-only: {t}")
        for t in list(only_batched)[:10]:
            print(f"    batched-only: {t}")
        diffs += len(only_legacy) + len(only_batched)

    max_abs = 0.0
    max_at = None
    for t in set(legacy) & set(batched):
        a, b = legacy[t], batched[t]
        for c in STR_COLS:
            if a.get(c) != b.get(c):
                print(f"    {t}.{c}: legacy={a.get(c)!r} batched={b.get(c)!r}")
                diffs += 1
        for c in NUMERIC_COLS:
            av, bv = a.get(c), b.get(c)
            if av is None or bv is None:
                if av is not bv and (av is None) != (bv is None):
                    print(f"    {t}.{c}: legacy={av!r} batched={bv!r} (None mismatch)")
                    diffs += 1
                continue
            d = abs(float(av) - float(bv))
            if d > max_abs:
                max_abs, max_at = d, f"{t}.{c}"
            if d > ABS_TOL:
                print(f"    {t}.{c}: legacy={av!r} batched={bv!r} |Δ|={d:.2e} > {ABS_TOL:.0e}")
                diffs += 1

    print()
    print(f"Row counts: legacy={len(legacy)} batched={len(batched)}")
    print(f"Max per-column |Δ| within tolerance: {max_abs:.2e} at {max_at}")
    print(f"PARITY diff count: {diffs}   (EXPECTED: 0)")
    if 0 < max_abs <= ABS_TOL:
        print(
            "  note: nonzero max Δ at/under tolerance is the batched path being MORE correct "
            "(no XCom JSON round-trip) — not a regression."
        )

    # ---- STREAMING: RSS must not ramp across batches ----
    # An accumulation leak (price blob or output rows retained) makes RSS climb
    # with batch index; correct streaming keeps it flat. Compare the mean of the
    # last window against the mean of the first window (same window size for
    # both, so the comparison is symmetric) and allow only modest allocator
    # drift — a real leak grows ~linearly with the universe, far past this band.
    peak = max(rss_profile) if rss_profile else float("nan")
    ramp_ok = True
    if len(rss_profile) >= 4:
        q = max(1, len(rss_profile) // 4)
        first_win = rss_profile[:q]
        last_win = rss_profile[-q:]
        first_avg = sum(first_win) / len(first_win)
        last_avg = sum(last_win) / len(last_win)
        ramp_ok = (last_avg - first_avg) <= max(50.0, 0.25 * first_avg)
        print(
            f"RSS first-window≈{first_avg:.0f} MiB  last-window≈{last_avg:.0f} MiB  "
            f"(window={q} batches)  flat={ramp_ok}"
        )
    print(f"PEAK RSS at full universe: {peak:.0f} MiB  (report to migration §6.1 / open Q7)")

    ok = diffs == 0 and ramp_ok
    print()
    print("GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
