-- Phase 1 (Signal v2): long-format prediction outcomes table
-- signal_predictions remains the registry of what was predicted (v1 + v2).
-- prediction_outcomes holds per-horizon resolved metrics: excess return,
-- volatility-scaled threshold, episode membership.

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    ticker            VARCHAR(16) NOT NULL,
    signal_date       DATE        NOT NULL,
    horizon_days      INTEGER     NOT NULL,

    price_at_signal   NUMERIC     NOT NULL,
    future_price      NUMERIC,
    raw_return        NUMERIC,
    benchmark_return  NUMERIC,        -- SPY return over same trading-day window
    beta_used         NUMERIC,        -- from sector_beta (SPY proxy) or 1.0 fallback
    excess_return     NUMERIC,        -- raw_return - beta_used * benchmark_return
    vol_at_signal     NUMERIC,        -- trailing daily sigma at signal date (pct returns)
    threshold_used    NUMERIC,        -- k * vol_at_signal * sqrt(horizon_days)

    direction_correct BOOLEAN,        -- sign(excess_return) matches bias
    threshold_correct BOOLEAN,        -- |excess_return| >= threshold_used, correct direction

    episode_id        VARCHAR(64),    -- '<ticker>:<first_signal_date_of_episode>'
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
