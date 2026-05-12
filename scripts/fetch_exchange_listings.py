#!/usr/bin/env python3
"""
fetch_exchange_listings.py — Expand ticker_universe.json via EODHD exchange symbol lists.

Calls EODHD's exchange-symbol-list endpoint for US (NYSE/NASDAQ), TSX, and TSX Venture.
Filters to common stocks only. New entries are added with has_data=True and null
sector/industry/cap_tier — the nightly fetch_metadata task populates those over time.
The backfill_eodhd.py script marks has_data=False for any ticker EODHD cannot serve.

Usage:
    # Dry-run — prints counts, writes nothing
    EODHD_API_KEY=<key> python scripts/fetch_exchange_listings.py

    # Apply to all three exchanges
    EODHD_API_KEY=<key> python scripts/fetch_exchange_listings.py --write --backup

    # Specific exchanges only
    EODHD_API_KEY=<key> python scripts/fetch_exchange_listings.py --write --exchanges TO V

Inside Docker (recommended):
    docker compose exec airflow-scheduler bash -c \
        'python /opt/airflow/scripts/fetch_exchange_listings.py --write --backup'
"""

import argparse
import json
import os
import shutil
from datetime import date
from pathlib import Path

import requests

UNIVERSE_PATH = Path(__file__).resolve().parents[1] / "config" / "ticker_universe.json"
EODHD_BASE = "https://eodhd.com/api"

# Exchange configurations
# exchange_code  : EODHD exchange code used in the API URL
# internal       : value written to ticker["exchange"] in ticker_universe.json
# suffix         : appended to each Code to form the universe ticker key
# currency       : filter — only include symbols trading in this currency
# exchanges      : if set, only include symbols whose "Exchange" field is in this set
EXCHANGE_CONFIG = {
    "US": {
        "exchange_code": "US",
        "internal": "us",
        "suffix": "",
        "currency": "USD",
        "exchanges": {"NYSE", "NASDAQ"},
    },
    "TO": {
        "exchange_code": "TO",
        "internal": "tsx",
        "suffix": ".TO",
        "currency": "CAD",
        "exchanges": None,
    },
    "V": {
        "exchange_code": "V",
        "internal": "tsx_venture",
        "suffix": ".V",
        "currency": "CAD",
        "exchanges": None,
    },
}

# Code suffixes that indicate non-equity instruments — skip these
_SKIP_SUFFIXES = (
    ".WT", ".WA", ".WB", ".WS",   # warrants
    ".RT", ".RH",                  # rights
    ".PR", ".PRA", ".PRB", ".PRC", ".PRD", ".PRE",  # preferred shares
    ".U",                          # USD-denominated unit
)

# EODHD Type values that are equity-like and should be included
_EQUITY_TYPES = {"Common Stock", "Ordinary Shares"}


def fetch_exchange_symbols(exchange_code: str, api_key: str) -> list[dict]:
    url = f"{EODHD_BASE}/exchange-symbol-list/{exchange_code}"
    resp = requests.get(
        url,
        params={"api_token": api_key, "fmt": "json", "type": "cs"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def is_quality_common_stock(symbol: dict, config: dict) -> bool:
    """Return True if this symbol should be included in the universe."""
    sym_type = symbol.get("Type", "")
    if sym_type not in _EQUITY_TYPES:
        return False

    if symbol.get("Currency") != config["currency"]:
        return False

    if config["exchanges"] and symbol.get("Exchange") not in config["exchanges"]:
        return False

    code = symbol.get("Code", "")
    if not code:
        return False

    # Skip warrants, rights, preferred shares by suffix
    for skip in _SKIP_SUFFIXES:
        if code.upper().endswith(skip.upper()):
            return False

    return True


def build_new_entry(internal_exchange: str) -> dict:
    return {
        "exchange": internal_exchange,
        "has_data": True,
        "cap_tier": None,
        "eodhd_sector": None,
        "eodhd_industry": None,
        "signal_categories": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expand ticker_universe.json via EODHD exchange symbol lists."
    )
    parser.add_argument("--write", action="store_true", help="Write changes to disk")
    parser.add_argument("--backup", action="store_true", help="Backup file before writing")
    parser.add_argument(
        "--exchanges",
        nargs="+",
        default=["US", "TO", "V"],
        choices=list(EXCHANGE_CONFIG.keys()),
        help="Exchanges to fetch (default: all three)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("EODHD_API_KEY")
    if not api_key:
        raise SystemExit("EODHD_API_KEY environment variable is not set.")

    with open(UNIVERSE_PATH) as f:
        universe = json.load(f)

    tickers: dict = universe["tickers"]

    added: dict[str, list[str]] = {"us": [], "tsx": [], "tsx_venture": []}
    already_present: int = 0

    for exchange_key in args.exchanges:
        config = EXCHANGE_CONFIG[exchange_key]
        print(f"Fetching {exchange_key} listings from EODHD...", flush=True)

        symbols = fetch_exchange_symbols(config["exchange_code"], api_key)
        print(f"  {len(symbols):,} symbols returned")

        quality = [s for s in symbols if is_quality_common_stock(s, config)]
        print(f"  {len(quality):,} common stocks after filtering")

        internal = config["internal"]
        suffix = config["suffix"]
        exchange_added = 0

        for sym in quality:
            ticker = sym["Code"] + suffix
            if ticker in tickers:
                already_present += 1
                continue
            tickers[ticker] = build_new_entry(internal)
            added[internal].append(ticker)
            exchange_added += 1

        print(f"  {exchange_added:,} new tickers added from {exchange_key}")

    us_count = sum(1 for t in tickers.values() if t["exchange"] == "us")
    tsx_count = sum(1 for t in tickers.values() if t["exchange"] == "tsx")
    tsxv_count = sum(1 for t in tickers.values() if t["exchange"] == "tsx_venture")
    no_data = sum(1 for t in tickers.values() if not t["has_data"])

    universe["meta"].update(
        {
            "generated_at": str(date.today()),
            "schema_version": "2.0",
            "total_unique_tickers": len(tickers),
            "us_listed": us_count,
            "tsx_listed": tsx_count,
            "tsx_venture_listed": tsxv_count,
            "no_data_tickers": no_data,
        }
    )

    print(f"\n{'─' * 40}")
    print(f"US added:          {len(added['us']):>6,}")
    print(f"TSX added:         {len(added['tsx']):>6,}")
    print(f"TSX-V added:       {len(added['tsx_venture']):>6,}")
    print(f"Already present:   {already_present:>6,}")
    print(f"Total tickers:     {len(tickers):>6,}")
    print(f"  US: {us_count}  TSX: {tsx_count}  TSX-V: {tsxv_count}")
    print(f"  no_data flags: {no_data}")

    if args.write:
        if args.backup:
            backup = UNIVERSE_PATH.with_suffix(".json.bak")
            shutil.copy(UNIVERSE_PATH, backup)
            print(f"\nBackup written to {backup}")
        with open(UNIVERSE_PATH, "w") as f:
            json.dump(universe, f, indent=2)
        print(f"Written to {UNIVERSE_PATH}")
    else:
        print("\nDry run — pass --write to apply changes.")


if __name__ == "__main__":
    main()
