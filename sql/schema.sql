-- Project Signal schema
-- All writes use INSERT ... ON CONFLICT DO UPDATE (idempotent)

CREATE TABLE IF NOT EXISTS raw_prices (
    ticker      VARCHAR(16)     NOT NULL,
    date        DATE            NOT NULL,
    open        NUMERIC         NOT NULL,
    high        NUMERIC         NOT NULL,
    low         NUMERIC         NOT NULL,
    close       NUMERIC         NOT NULL,
    volume      BIGINT          NOT NULL,
    source      VARCHAR(32)     NOT NULL DEFAULT 'polygon',
    fetched_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_raw_prices_date   ON raw_prices (date);
CREATE INDEX IF NOT EXISTS idx_raw_prices_ticker ON raw_prices (ticker);

CREATE TABLE IF NOT EXISTS ticker_metadata (
    ticker      VARCHAR(16)     NOT NULL PRIMARY KEY,
    name        VARCHAR(256),
    sector      VARCHAR(128),
    industry    VARCHAR(128),
    market_cap  BIGINT,
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stock_signals (
    ticker              VARCHAR(16)     NOT NULL,
    date                DATE            NOT NULL,
    sma_50              NUMERIC,
    sma_200             NUMERIC,
    macd                NUMERIC,
    macd_signal         NUMERIC,
    macd_hist           NUMERIC,
    rsi_14              NUMERIC,
    bb_upper            NUMERIC,
    bb_lower            NUMERIC,
    vix_close           NUMERIC,
    vvix_close          NUMERIC,
    vix_regime          VARCHAR(16),
    vix_trend           VARCHAR(16),
    vol_environment     VARCHAR(24),
    composite_score     NUMERIC,
    composite_vix_adj   NUMERIC,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_stock_signals_date   ON stock_signals (date);
CREATE INDEX IF NOT EXISTS idx_stock_signals_ticker ON stock_signals (ticker);

CREATE TABLE IF NOT EXISTS relatedness_matrix (
    ticker_a    VARCHAR(16)     NOT NULL,
    ticker_b    VARCHAR(16)     NOT NULL,
    window_days INTEGER         NOT NULL,
    pearson_r   NUMERIC         NOT NULL,
    computed_at TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker_a, ticker_b, window_days)
);

CREATE INDEX IF NOT EXISTS idx_relatedness_ticker_a ON relatedness_matrix (ticker_a);
CREATE INDEX IF NOT EXISTS idx_relatedness_ticker_b ON relatedness_matrix (ticker_b);

CREATE TABLE IF NOT EXISTS sector_beta (
    ticker      VARCHAR(16)     NOT NULL,
    etf_proxy   VARCHAR(16)     NOT NULL,
    beta        NUMERIC         NOT NULL,
    window_days INTEGER         NOT NULL,
    computed_at TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, etf_proxy, window_days)
);

CREATE INDEX IF NOT EXISTS idx_sector_beta_ticker ON sector_beta (ticker);

CREATE TABLE IF NOT EXISTS llm_analysis (
    ticker          VARCHAR(16)     NOT NULL,
    date            DATE            NOT NULL,
    bias            VARCHAR(16),
    confidence      NUMERIC,
    key_levels      JSONB,
    reasoning       TEXT,
    model           VARCHAR(64),
    prompt_tokens   INTEGER,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_llm_analysis_date   ON llm_analysis (date);
CREATE INDEX IF NOT EXISTS idx_llm_analysis_ticker ON llm_analysis (ticker);
