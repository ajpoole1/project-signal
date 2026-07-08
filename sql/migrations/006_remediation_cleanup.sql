-- MANUAL
-- ^ Destructive (contains DELETE). deploy.sh skips-and-warns; apply by hand only.
--   Retroactive marker (§7.6): already applied historically — the marker exists so
--   boot-time automation never re-runs its deletes.
-- Remediation cleanup — post Phase 0 data-quality findings
-- Applied: 2026-06-09
--
-- PURPOSE
-- -------
-- Remove junk-ticker rows from all downstream tables and reset the v2
-- prediction_outcomes table (which did not yet exist in the live DB —
-- migration 005 is applied here too).  Also creates prediction_outcomes
-- if it does not already exist (idempotent via CREATE TABLE IF NOT EXISTS).
--
-- JUNK-TICKER LIST PROVENANCE
-- ---------------------------
-- Generated 2026-06-09 by inspecting the live signal DB:
--
--   SELECT ticker FROM raw_prices
--   WHERE ticker IN ('WLDSW','ZWZZT','ZVZZT','ZXZZT')
--   GROUP BY ticker;
--
-- These four tickers were NOT in config/ticker_universe.json at all
-- (neither active nor has_data=False); they entered raw_prices via an
-- older fetch-listings run before the warrant/test-symbol filter existed:
--   WLDSW  — bare-W-suffix US warrant (root ticker WLDS exists in universe)
--   ZWZZT  — Nasdaq test/canary symbol
--   ZVZZT  — Nasdaq test/canary symbol
--   ZXZZT  — Nasdaq test/canary symbol
--
-- The 28 has_data=False US tickers in ticker_universe.json (ABC, ABML,
-- ASAI, AYX, …, WBA) already have zero rows in any DB table — the EODHD
-- re-backfill excluded them — so no DELETE is needed for them.
--
-- NOTE: signal_predictions was already manually TRUNCATED on 2026-06-09
-- before this migration was written.  That table is therefore empty and
-- is not touched here.
--
-- SCHEMA NOTE
-- -----------
-- This migration contains no schema changes.  prediction_outcomes is
-- created by migration 005_v2_prediction_outcomes.sql; the CREATE TABLE
-- IF NOT EXISTS below is a safety guard only (005 may not have been
-- applied to this DB instance yet).

-- -----------------------------------------------------------------------
-- 1. Create prediction_outcomes if it does not exist (idempotent guard
--    for databases where 005 was not yet applied)
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    ticker            VARCHAR(16) NOT NULL,
    signal_date       DATE        NOT NULL,
    horizon_days      INTEGER     NOT NULL,

    price_at_signal   NUMERIC     NOT NULL,
    future_price      NUMERIC,
    raw_return        NUMERIC,
    benchmark_return  NUMERIC,
    beta_used         NUMERIC,
    excess_return     NUMERIC,
    vol_at_signal     NUMERIC,
    threshold_used    NUMERIC,

    direction_correct BOOLEAN,
    threshold_correct BOOLEAN,

    episode_id        VARCHAR(64),
    is_episode_head   BOOLEAN NOT NULL DEFAULT FALSE,

    resolved_at       TIMESTAMPTZ,
    PRIMARY KEY (ticker, signal_date, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_pred_outcomes_unresolved
    ON prediction_outcomes (signal_date) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pred_outcomes_horizon
    ON prediction_outcomes (horizon_days);
CREATE INDEX IF NOT EXISTS idx_pred_outcomes_episode
    ON prediction_outcomes (episode_id);

-- -----------------------------------------------------------------------
-- 2. TRUNCATE prediction_outcomes
--    (empty at this point — 005 was never applied to live DB — but
--    explicit for clarity; also handles any partial future state)
-- -----------------------------------------------------------------------
TRUNCATE TABLE prediction_outcomes;

-- -----------------------------------------------------------------------
-- 3. DELETE junk-ticker rows from downstream tables
--    Order: most-derived first to avoid FK issues (none exist currently,
--    but defensive ordering is good practice)
-- -----------------------------------------------------------------------

DELETE FROM llm_analysis
WHERE ticker IN ('WLDSW', 'ZWZZT', 'ZVZZT', 'ZXZZT');

DELETE FROM stock_signals
WHERE ticker IN ('WLDSW', 'ZWZZT', 'ZVZZT', 'ZXZZT');

DELETE FROM raw_prices
WHERE ticker IN ('WLDSW', 'ZWZZT', 'ZVZZT', 'ZXZZT');
