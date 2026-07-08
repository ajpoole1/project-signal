"""Tests for scripts/fetch_exchange_listings.py — is_quality_common_stock filter."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.fetch_exchange_listings import is_quality_common_stock

_US_CONFIG = {"internal": "us", "currency": "USD", "exchanges": {"NYSE", "NASDAQ"}}
_TSX_CONFIG = {"internal": "tsx", "currency": "CAD", "exchanges": None}


def _sym(
    code: str,
    exchange: str = "NASDAQ",
    currency: str = "USD",
    sym_type: str = "Common Stock",
    name: str = "Test Corp",
) -> dict:
    return {
        "Code": code,
        "Exchange": exchange,
        "Currency": currency,
        "Type": sym_type,
        "Name": name,
    }


# ---------------------------------------------------------------------------
# Rejection fixtures (the three classes that slipped through historically)
# ---------------------------------------------------------------------------


class TestRejectedSymbols:
    def test_nasdaq_test_symbol_zjzzt_rejected(self):
        assert not is_quality_common_stock(_sym("ZJZZT"), _US_CONFIG, set())

    def test_nasdaq_test_symbol_pattern_all_variants(self):
        for code in ("ZVZZT", "ZWZZT", "ZXZZT", "ZBZZT", "ZAZZT", "ZCZZT"):
            assert not is_quality_common_stock(_sym(code), _US_CONFIG, set()), code

    def test_hyphenated_wt_rejected(self):
        assert not is_quality_common_stock(_sym("OPFI-WT"), _US_CONFIG, set())

    def test_hyphenated_ws_rejected(self):
        assert not is_quality_common_stock(_sym("JOBY-WS"), _US_CONFIG, set())

    def test_hyphenated_un_us_rejected(self):
        assert not is_quality_common_stock(_sym("BEBE-UN"), _US_CONFIG, set())

    def test_nasdaq5_warrant_rejected_when_root_exists(self):
        existing = {"SOUN"}  # root of SOUNW
        assert not is_quality_common_stock(_sym("SOUNW"), _US_CONFIG, existing)

    def test_nasdaq5_rights_rejected_when_root_exists(self):
        existing = {"AFJK"}
        assert not is_quality_common_stock(_sym("AFJKR"), _US_CONFIG, existing)

    def test_nasdaq5_unit_rejected_when_root_exists(self):
        existing = {"DYNC"}
        assert not is_quality_common_stock(_sym("DYNCU"), _US_CONFIG, existing)

    def test_nasdaq5_not_rejected_when_root_absent(self):
        # If the root isn't in the universe yet, don't reject — the 5th-char
        # heuristic only fires when we have evidence it's a derivative.
        assert is_quality_common_stock(_sym("ABCDW"), _US_CONFIG, set())


# ---------------------------------------------------------------------------
# Acceptance fixtures (must NOT be rejected)
# ---------------------------------------------------------------------------


class TestAcceptedSymbols:
    def test_rei_un_to_accepted(self):
        # TSX REIT unit trust — legitimate, must not be touched by -UN rule
        sym = _sym("REI-UN", exchange="TSX", currency="CAD")
        assert is_quality_common_stock(sym, _TSX_CONFIG, set())

    def test_itw_accepted(self):
        # Illinois Tool Works — 3-char root, len==3 not len==5, never a Nasdaq5 hit
        assert is_quality_common_stock(_sym("ITW"), _US_CONFIG, {"IT"})

    def test_aciw_accepted(self):
        # ACI Worldwide — len==4, not len==5
        assert is_quality_common_stock(_sym("ACIW"), _US_CONFIG, {"ACI"})

    def test_glw_accepted(self):
        # Corning — len==3
        assert is_quality_common_stock(_sym("GLW"), _US_CONFIG, {"GL"})

    def test_normal_us_stock_accepted(self):
        assert is_quality_common_stock(_sym("AAPL", exchange="NASDAQ"), _US_CONFIG, set())

    def test_normal_tsx_stock_accepted(self):
        sym = _sym("RY", exchange="TSX", currency="CAD")
        assert is_quality_common_stock(sym, _TSX_CONFIG, set())

    def test_nasdaq5_w_no_root_accepted(self):
        # ABCDW where ABCD doesn't exist — should pass through
        assert is_quality_common_stock(_sym("ABCDW"), _US_CONFIG, set())
