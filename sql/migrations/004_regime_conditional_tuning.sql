-- Phase 3.5: regime-conditional signal tuning
--
-- 1. signal_version enters the primary key so multiple signal versions
--    (v1.0 baseline vs v1.1 candidates) can coexist for the same ticker+date,
--    enabling clean A/B accuracy comparison.
-- 2. regime_weight_set records which named weight set scored each prediction,
--    for audit and per-weight-set accuracy attribution.
--
-- Idempotent: safe to re-run.

-- --- Primary key: add signal_version ---
-- The old PK (ticker, signal_date) blocks holding two versions of the same row.
ALTER TABLE signal_predictions
    DROP CONSTRAINT IF EXISTS signal_predictions_pkey;

ALTER TABLE signal_predictions
    ADD CONSTRAINT signal_predictions_pkey
    PRIMARY KEY (ticker, signal_date, signal_version);

-- --- Audit column: which regime weight set produced this score ---
ALTER TABLE signal_predictions
    ADD COLUMN IF NOT EXISTS regime_weight_set VARCHAR(16);

-- Backfill existing rows: pre-3.5 predictions all used the global "default" set.
UPDATE signal_predictions
    SET regime_weight_set = 'default'
    WHERE regime_weight_set IS NULL;

-- Index to support per-(version, regime) accuracy queries in Step 6.
CREATE INDEX IF NOT EXISTS idx_signal_predictions_version_regime
    ON signal_predictions (signal_version, vix_regime);
