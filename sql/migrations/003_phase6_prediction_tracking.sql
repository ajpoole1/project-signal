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

    PRIMARY KEY (ticker, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_signal_predictions_date
    ON signal_predictions (signal_date);
CREATE INDEX IF NOT EXISTS idx_signal_predictions_unresolved
    ON signal_predictions (signal_date)
    WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_signal_predictions_version
    ON signal_predictions (signal_version);

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
