"""
Universe audit: flag Nasdaq test symbols and warrant/right/unit tickers
in ticker_universe.json that slipped through the existing suffix filter.

Dry-run by default — prints the flagged list and stops. Pass --write to
apply has_data=False to ticker_universe.json.

Rules (class invariants — not name lists):
  a. Nasdaq test symbols: US tickers matching ^Z[A-Z]ZZT$ (pattern covers
     ZVZZT, ZWZZT, ZXZZT, ZJZZT, ZBZZT, ZAZZT, ZCZZT and any future member).
  b. Warrant/right/unit suffixes — MULTIPLE formats, US-exchange tickers only:
       hyphenated:  -WT  -WS  -RT  -UN
       dotted:      .WT  .WS  .RT
       Nasdaq 5th-char W: len==5, ends W (no hyphen), 4-char root exists in universe
       Nasdaq 5th-char R: len==5, ends R (no hyphen), 4-char root exists in universe
       Nasdaq 5th-char U: len==5, ends U (no hyphen), 4-char root exists in universe
     CRITICAL EXCLUSION: exchange == "tsx" or "tsx_venture" are NEVER touched by
     suffix rules. REI-UN.TO is a legitimate TSX REIT unit trust. Only
     exchange == "us" is eligible for suffix-based flagging.
     NOTE: bare trailing W on tickers of ANY length is NOT used — too many
     false positives (ITW=Illinois Tool Works, GLW=Corning, CDW, EW, etc.).
     The len==5 constraint is the safe discriminator.

Dry-run output: full list with (ticker, exchange, reason).
--write: sets has_data=False for all flagged tickers; also rewrites
         meta.no_data_tickers count.

Usage:
    python scripts/audit_universe.py               # dry run
    python scripts/audit_universe.py --write       # apply
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "config" / "ticker_universe.json"

# Pattern (a): Nasdaq test symbols — Z<single letter>ZZT
_TEST_SYMBOL_RE = re.compile(r"^Z[A-Z]ZZT$")

# Pattern (b): US-only hyphenated and dotted warrant/right/unit suffixes
_US_HYPHEN_SUFFIXES = ("-WT", "-WS", "-RT", "-UN")
_US_DOT_SUFFIXES = (".WT", ".WS", ".RT")

# Pattern (b): Nasdaq 5th-character convention — len==5, no hyphen,
# 4-char root exists in universe. W=warrant, R=rights, U=units.
_NASDAQ5_WARRANT_CHARS = {"W", "R", "U"}


def _is_us(entry: dict) -> bool:
    return entry.get("exchange") == "us"


def classify(ticker: str, entry: dict, us_tickers: set[str]) -> str | None:
    """Return a reason string if the ticker should be flagged, else None."""
    if not _is_us(entry):
        # TSX/TSX-V: never touch on suffix rules; test-symbol pattern is US-only too.
        return None

    # (a) Nasdaq test symbols
    if _TEST_SYMBOL_RE.match(ticker):
        return "nasdaq_test_symbol (matches ^Z[A-Z]ZZT$)"

    # (b-i) Hyphenated suffixes (US only)
    for suf in _US_HYPHEN_SUFFIXES:
        if ticker.endswith(suf):
            return f"us_warrant_or_unit (hyphenated suffix {suf})"

    # (b-ii) Dotted suffixes (US only)
    for suf in _US_DOT_SUFFIXES:
        if ticker.endswith(suf):
            return f"us_warrant_or_unit (dotted suffix {suf})"

    # (b-iii) Nasdaq 5th-character convention: len==5, no hyphen,
    # last char is W/R/U, and the 4-char root exists in the universe.
    # This safely catches XXXW/XXXR/XXXU warrant/right/unit tickers without
    # touching legitimate 3-char + W names (ITW, GLW, CDW, EW, etc.)
    if len(ticker) == 5 and "-" not in ticker and ticker[-1] in _NASDAQ5_WARRANT_CHARS:
        root = ticker[:4]
        if root in us_tickers:
            suffix_type = {"W": "warrant", "R": "rights", "U": "unit"}[ticker[-1]]
            return f"nasdaq5_{suffix_type} (len==5, last char {ticker[-1]!r}, root {root!r} in universe)"

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit ticker_universe.json for junk tickers")
    parser.add_argument(
        "--write", action="store_true", help="Apply has_data=False (default: dry run)"
    )
    args = parser.parse_args()

    with open(UNIVERSE_PATH) as f:
        universe = json.load(f)

    tickers: dict[str, dict] = universe["tickers"]

    flagged: list[tuple[str, str, str]] = []  # (ticker, exchange, reason)

    us_ticker_set = {t for t, e in tickers.items() if e.get("exchange") == "us"}

    for ticker, entry in sorted(tickers.items()):
        reason = classify(ticker, entry, us_ticker_set)
        if reason is None:
            continue
        # Only flag if currently has_data=True — already-flagged entries are noise
        if entry.get("has_data", False):
            flagged.append((ticker, entry.get("exchange", "?"), reason))

    print(f"Universe audit: {len(tickers):,} tickers checked")
    print(f"Flagged (has_data=True -> should be False): {len(flagged)}")
    print()

    if not flagged:
        print("No tickers to flag. Universe is clean.")
        return

    # Group by reason category for readability
    by_reason: dict[str, list[str]] = {}
    for ticker, _exchange, reason in flagged:
        by_reason.setdefault(reason, []).append(ticker)

    for reason, tkrs in sorted(by_reason.items()):
        print(f"  [{reason}]  n={len(tkrs)}")
        for t in sorted(tkrs):
            print(f"    {t}")
        print()

    print(f"Total: {len(flagged)} tickers would be flagged has_data=False")

    if not args.write:
        print("\nDry run — pass --write to apply. Review the list above first.")
        return

    # Apply
    for ticker, _exchange, _reason in flagged:
        tickers[ticker]["has_data"] = False

    no_data = sum(1 for e in tickers.values() if not e["has_data"])
    universe["meta"]["no_data_tickers"] = no_data

    with open(UNIVERSE_PATH, "w") as f:
        json.dump(universe, f, indent=2)

    print(f"\nApplied: {len(flagged)} tickers set has_data=False")
    print(f"Total no_data in universe: {no_data}")


if __name__ == "__main__":
    main()
