-- Migration 001: add currency/source to raw_prices, exchange to ticker_metadata,
--                vix_source to stock_signals
-- Apply once against the live dev Postgres before the next DAG run.
-- All statements are additive and non-destructive.

-- raw_prices: currency was missing, source was missing
ALTER TABLE raw_prices
    ADD COLUMN IF NOT EXISTS currency   VARCHAR(8)  NOT NULL DEFAULT 'USD',
    ADD COLUMN IF NOT EXISTS source     VARCHAR(32) NOT NULL DEFAULT 'polygon';

-- ticker_metadata: exchange was missing
ALTER TABLE ticker_metadata
    ADD COLUMN IF NOT EXISTS exchange   VARCHAR(16);

-- stock_signals: vix_source was missing
ALTER TABLE stock_signals
    ADD COLUMN IF NOT EXISTS vix_source VARCHAR(16);
