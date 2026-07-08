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
    currency    VARCHAR(8)      NOT NULL DEFAULT 'USD',
    source      VARCHAR(32)     NOT NULL DEFAULT 'eodhd',
    fetched_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_raw_prices_date   ON raw_prices (date);
CREATE INDEX IF NOT EXISTS idx_raw_prices_ticker ON raw_prices (ticker);

CREATE TABLE IF NOT EXISTS ticker_metadata (
    ticker          VARCHAR(16)     NOT NULL PRIMARY KEY,
    name            VARCHAR(256),
    sector          VARCHAR(128),
    industry        VARCHAR(128),
    market_cap      BIGINT,
    exchange        VARCHAR(16),
    eodhd_sector    VARCHAR(64),
    eodhd_industry  VARCHAR(128),
    cap_tier        VARCHAR(8),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticker_metadata_eodhd_sector ON ticker_metadata (eodhd_sector);
CREATE INDEX IF NOT EXISTS idx_ticker_metadata_cap_tier     ON ticker_metadata (cap_tier);

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
    vix_source          VARCHAR(16),
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

-- Phase 6: prediction tracking, accuracy rollup, parameter proposals
CREATE TABLE IF NOT EXISTS signal_predictions (
    ticker              VARCHAR(16)     NOT NULL,
    signal_date         DATE            NOT NULL,

    -- Signal state snapshot at prediction time
    bias                VARCHAR(16),
    composite_vix_adj   NUMERIC,
    confidence          NUMERIC,
    vix_regime          VARCHAR(16),
    vix_trend           VARCHAR(16),
    vol_environment     VARCHAR(24),
    volume_ratio        NUMERIC,
    earnings_flag       VARCHAR(16),
    signal_trend        VARCHAR(16),

    -- Price at signal time
    price_at_signal     NUMERIC         NOT NULL,

    -- Config version that generated this signal
    signal_version      VARCHAR(32)     NOT NULL,

    -- Outcome fields — NULL until resolved by dag_outcome_tracker
    actual_price_5d     NUMERIC,
    actual_price_10d    NUMERIC,
    actual_price_20d    NUMERIC,
    return_5d           NUMERIC,
    return_10d          NUMERIC,
    return_20d          NUMERIC,
    correct_5d          BOOLEAN,
    correct_10d         BOOLEAN,
    correct_20d         BOOLEAN,

    -- Lifecycle
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,

    PRIMARY KEY (ticker, signal_date, signal_version)
);

CREATE INDEX IF NOT EXISTS idx_signal_predictions_date
    ON signal_predictions (signal_date);
CREATE INDEX IF NOT EXISTS idx_signal_predictions_unresolved
    ON signal_predictions (signal_date)
    WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_signal_predictions_version
    ON signal_predictions (signal_version);
CREATE INDEX IF NOT EXISTS idx_signal_predictions_version_regime
    ON signal_predictions (signal_version, vix_regime);

CREATE TABLE IF NOT EXISTS signal_accuracy (
    signal_version      VARCHAR(32)     NOT NULL,
    vix_regime          VARCHAR(16)     NOT NULL,
    vol_environment     VARCHAR(24)     NOT NULL,
    bias                VARCHAR(16)     NOT NULL,
    horizon_days        INTEGER         NOT NULL,

    total_signals       INTEGER         NOT NULL,
    correct_signals     INTEGER         NOT NULL,
    accuracy_rate       NUMERIC         NOT NULL,
    avg_return          NUMERIC,
    avg_return_correct  NUMERIC,
    avg_return_wrong    NUMERIC,

    computed_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    PRIMARY KEY (signal_version, vix_regime, vol_environment, bias, horizon_days)
);

CREATE TABLE IF NOT EXISTS parameter_proposals (
    id                  SERIAL          PRIMARY KEY,
    proposed_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    param_name          VARCHAR(64)     NOT NULL,
    current_value       NUMERIC         NOT NULL,
    proposed_value      NUMERIC         NOT NULL,
    rationale           TEXT            NOT NULL,
    accuracy_delta      NUMERIC,
    sample_size         INTEGER,
    vix_regime_scope    VARCHAR(16),
    vol_env_scope       VARCHAR(24),
    status              VARCHAR(16)     NOT NULL DEFAULT 'pending',
    reviewed_at         TIMESTAMPTZ,
    review_note         TEXT
);

-- Phase 1 (Signal v2): long-format prediction outcomes
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

-- Phase 2 + 3 (Signal v2): continuous features and regime classification
CREATE TABLE IF NOT EXISTS signal_features (
    ticker              VARCHAR(16) NOT NULL,
    date                DATE        NOT NULL,
    dist_sma50          NUMERIC,
    dist_sma200         NUMERIC,
    dist_sma200_z       NUMERIC,
    rsi_centered        NUMERIC,
    bb_pctb             NUMERIC,
    macd_hist_norm      NUMERIC,
    dist_52w_high       NUMERIC,
    dist_52w_low        NUMERIC,
    vol_ratio           NUMERIC,
    ret_21d             NUMERIC,
    ret_63d             NUMERIC,
    adx_14              NUMERIC,
    sma200_slope        NUMERIC,
    ticker_regime       VARCHAR(16),
    trend_direction     VARCHAR(8),
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_signal_features_date   ON signal_features (date);
CREATE INDEX IF NOT EXISTS idx_signal_features_regime ON signal_features (ticker_regime);
