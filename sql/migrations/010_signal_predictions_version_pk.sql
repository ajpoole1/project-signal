-- Signal v2 (Phase 4.3): widen signal_predictions PK to include signal_version
-- so v1 and v2 predictions coexist for the same (ticker, signal_date) during the
-- parallel run before cutover.
--
-- Previously introduced as part of the (retired, unmerged) Phase 3.5 changeset;
-- extracted and re-attributed to the v2 parallel-run requirement. The Phase 3.5
-- regime_weight_set column is intentionally NOT included here — that work is
-- archived on branch archive/phase-3-5-regime-weights and not merged.
--
-- Applied: 2026-06-11
-- Idempotent: safe to re-run.

-- The old PK (ticker, signal_date) blocks holding two versions of the same row.
ALTER TABLE signal_predictions
    DROP CONSTRAINT IF EXISTS signal_predictions_pkey;

ALTER TABLE signal_predictions
    ADD CONSTRAINT signal_predictions_pkey
    PRIMARY KEY (ticker, signal_date, signal_version);

-- Index to support per-(version, regime) accuracy queries in the parallel-run
-- comparison. vix_regime is legitimate signal state, not a v1-scoring artifact.
CREATE INDEX IF NOT EXISTS idx_signal_predictions_version_regime
    ON signal_predictions (signal_version, vix_regime);
