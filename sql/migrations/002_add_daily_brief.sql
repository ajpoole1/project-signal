-- Migration 002: add daily_brief table for Sonnet-generated market brief
CREATE TABLE IF NOT EXISTS daily_brief (
    date        DATE PRIMARY KEY,
    brief_text  TEXT         NOT NULL,
    ticker_count INT,
    model       VARCHAR(100),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);
