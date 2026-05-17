-- Migration 004: Add EODHD sector/industry and cap_tier to ticker_metadata
-- These are populated by the nightly fetch_metadata task from the EODHD General filter endpoint.
-- cap_tier is seeded from ticker_universe.json schema v2 and kept current via metadata fetch.

ALTER TABLE ticker_metadata
    ADD COLUMN IF NOT EXISTS eodhd_sector    VARCHAR(64),
    ADD COLUMN IF NOT EXISTS eodhd_industry  VARCHAR(128),
    ADD COLUMN IF NOT EXISTS cap_tier        VARCHAR(8);

CREATE INDEX IF NOT EXISTS idx_ticker_metadata_eodhd_sector
    ON ticker_metadata (eodhd_sector);

CREATE INDEX IF NOT EXISTS idx_ticker_metadata_cap_tier
    ON ticker_metadata (cap_tier);
