"""Insert-mapping tests for signal_predictions writes.

Guards the (ticker, signal_date, signal_version) PK coexistence property:
v1.0 and v2.0 rows for the same (ticker, signal_date) must be able to coexist,
so every INSERT must (a) list signal_version, (b) target the 3-column PK in its
ON CONFLICT clause, and (c) keep column-count == placeholder-count == tuple-len.

These are static-source assertions (no DB) — the same class of check the
CLAUDE.md insert-mapping rule requires, adapted to the version-PK widening
introduced in migration 010.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_INSERT_SITES = [
    ROOT / "dag_components" / "outcome_tracker" / "tasks.py",
    ROOT / "scripts" / "backfill_predictions.py",
]


def _extract_prediction_insert(source: str) -> tuple[list[str], int, str]:
    """Return (column_list, placeholder_count, conflict_target) for the
    INSERT INTO signal_predictions statement in the given source."""
    # Column list between 'INSERT INTO signal_predictions (' and ')'
    m_cols = re.search(r"INSERT INTO signal_predictions\s*\(([^)]*)\)", source, re.DOTALL)
    assert m_cols, "No INSERT INTO signal_predictions found"
    cols = [c.strip() for c in m_cols.group(1).split(",") if c.strip()]

    # VALUES (%s, %s, ...) placeholder count
    m_vals = re.search(r"VALUES\s*\(((?:\s*%s\s*,?)+)\)", source)
    assert m_vals, "No VALUES (%s...) found"
    placeholders = m_vals.group(1).count("%s")

    # ON CONFLICT target
    m_conf = re.search(r"ON CONFLICT\s*\(([^)]*)\)", source)
    assert m_conf, "No ON CONFLICT clause found"
    conflict = m_conf.group(1).strip()

    return cols, placeholders, conflict


class TestSignalPredictionsInsertMapping:
    def test_column_count_matches_placeholders(self):
        for path in _INSERT_SITES:
            cols, placeholders, _ = _extract_prediction_insert(path.read_text())
            assert (
                len(cols) == placeholders
            ), f"{path.name}: {len(cols)} columns but {placeholders} placeholders"

    def test_signal_version_in_columns(self):
        for path in _INSERT_SITES:
            cols, _, _ = _extract_prediction_insert(path.read_text())
            assert "signal_version" in cols, f"{path.name}: signal_version missing from INSERT"

    def test_conflict_target_is_three_column_pk(self):
        # The coexistence property: conflict must key on all three PK columns,
        # not the old (ticker, signal_date) — otherwise a v2 insert would
        # clobber the v1 row (or error against the widened unique constraint).
        for path in _INSERT_SITES:
            _, _, conflict = _extract_prediction_insert(path.read_text())
            targets = {t.strip() for t in conflict.split(",")}
            assert targets == {"ticker", "signal_date", "signal_version"}, (
                f"{path.name}: ON CONFLICT target is ({conflict}), "
                "must be (ticker, signal_date, signal_version)"
            )

    def test_no_regime_weight_set_column(self):
        # regime_weight_set was Phase 3.5 (archived, not merged). It must not
        # reappear in any active insert site.
        for path in _INSERT_SITES:
            cols, _, _ = _extract_prediction_insert(path.read_text())
            assert (
                "regime_weight_set" not in cols
            ), f"{path.name}: regime_weight_set is retired Phase 3.5 residue"
