-- Phase 2 + 3 (Signal v2): continuous feature layer and regime classification
-- Phases 2 and 3 are folded into a single migration per spec preference.
-- Applied: 2026-06-10

CREATE TABLE IF NOT EXISTS signal_features (
    ticker              VARCHAR(16) NOT NULL,
    date                DATE        NOT NULL,

    -- Phase 2: continuous displacement / momentum features
    dist_sma50          NUMERIC,    -- (close - SMA50) / SMA50
    dist_sma200         NUMERIC,    -- (close - SMA200) / SMA200
    dist_sma200_z       NUMERIC,    -- z-score of dist_sma200 vs trailing 252d distribution
    rsi_centered        NUMERIC,    -- (RSI14 - 50) / 50  → [-1, 1]
    bb_pctb             NUMERIC,    -- (close - bb_lower) / (bb_upper - bb_lower)
    macd_hist_norm      NUMERIC,    -- macd_hist / close
    dist_52w_high       NUMERIC,    -- close / 252d max(high) - 1  (<=0)
    dist_52w_low        NUMERIC,    -- close / 252d min(low)  - 1  (>=0)
    vol_ratio           NUMERIC,    -- volume / SMA20(volume)
    ret_21d             NUMERIC,    -- 21d trailing pct return
    ret_63d             NUMERIC,    -- 63d trailing pct return

    -- Phase 3: regime classification
    adx_14              NUMERIC,    -- Average Directional Index (Wilder, 14-period)
    sma200_slope        NUMERIC,    -- 63d pct change of SMA200 series
    ticker_regime       VARCHAR(16),-- 'trending' | 'ranging'
    trend_direction     VARCHAR(8), -- 'up' | 'down' | NULL (only set when trending)

    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_signal_features_date   ON signal_features (date);
CREATE INDEX IF NOT EXISTS idx_signal_features_regime ON signal_features (ticker_regime);
