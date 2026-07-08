"""Insert-mapping tests for the stock_signals write.

Guards the transposition-bug class SIGNAL_GUIDE fences against: the 18-column
stock_signals INSERT and the tuple its records comprehension builds must agree
on both count and *order*. This bug has occurred in production here — the math
was right and the columns were transposed — so the mapping is asserted
statically, independent of any live-DB parity run.

The tuple in pipeline.upsert_batch is built positionally from `r["<col>"]`
lookups, so the sequence of those keys IS the tuple field order. We extract the
SQL column list and that key sequence from source and assert they are identical.

Static-source assertions (no DB), per the CLAUDE.md / SIGNAL_GUIDE insert-mapping
rule — the same class of check as tests/test_signal_predictions_insert.py.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PIPELINE = ROOT / "dag_components" / "indicators" / "pipeline.py"


def _sql_columns(source: str) -> list[str]:
    """Column list between 'INSERT INTO stock_signals (' and ') VALUES'."""
    m = re.search(
        r"INSERT INTO stock_signals\s*\((.*?)\)\s*VALUES",
        source,
        re.DOTALL,
    )
    assert m, "No INSERT INTO stock_signals ... VALUES found"
    return [c.strip() for c in m.group(1).split(",") if c.strip()]


def _values_placeholder_count(source: str) -> int:
    """The INSERT uses execute_values, so VALUES is a single '%s' template."""
    m = re.search(r"\)\s*VALUES\s*%s", source)
    assert m, "stock_signals INSERT must use 'VALUES %s' (execute_values form)"
    return 1


def _tuple_keys(source: str) -> list[str]:
    """The ordered r["<key>"] lookups inside upsert_batch's records comprehension.

    Sliced to the `records = [ ( ... ) for r in rows ]` block so unrelated
    r["..."] references elsewhere in the module cannot leak in.
    """
    m = re.search(r"records\s*=\s*\[(.*?)for r in rows", source, re.DOTALL)
    assert m, "No 'records = [ ... for r in rows' comprehension found in upsert_batch"
    return re.findall(r'r\[["\']([^"\']+)["\']\]', m.group(1))


class TestStockSignalsInsertMapping:
    def test_tuple_order_matches_column_list(self):
        source = _PIPELINE.read_text()
        cols = _sql_columns(source)
        keys = _tuple_keys(source)
        assert keys == cols, (
            "stock_signals tuple field order does not match the SQL column list.\n"
            f"  columns: {cols}\n"
            f"  tuple  : {keys}\n"
            "A transposition here writes correct-looking values to the wrong columns."
        )

    def test_column_count_matches_tuple_length(self):
        source = _PIPELINE.read_text()
        cols = _sql_columns(source)
        keys = _tuple_keys(source)
        assert len(cols) == len(keys), (
            f"stock_signals INSERT lists {len(cols)} columns "
            f"but the tuple builds {len(keys)} fields"
        )

    def test_conflict_target_is_ticker_date_pk(self):
        # Time-series PK is (ticker, date); the upsert must key on exactly that
        # so a rerun re-resolves the same row rather than duplicating it.
        source = _PIPELINE.read_text()
        m = re.search(r"ON CONFLICT\s*\(([^)]*)\)", source)
        assert m, "No ON CONFLICT clause found in stock_signals INSERT"
        targets = {t.strip() for t in m.group(1).split(",")}
        assert targets == {
            "ticker",
            "date",
        }, f"ON CONFLICT target is ({m.group(1).strip()}), must be (ticker, date)"

    def test_uses_execute_values_batch_form(self):
        # SQL_STANDARDS: multi-row inserts batch through execute_values, never a
        # row-loop. Pin the batch form so a future edit can't regress to per-row.
        source = _PIPELINE.read_text()
        assert _values_placeholder_count(source) == 1
        assert "execute_values(" in source, "stock_signals write must use execute_values"
