"""All tunable values for Project Signal. Nothing is hardcoded in DAG or plugin files."""

import json
from pathlib import Path

# --- Parameter overrides (Phase 6) ---
# Approved proposals from parameter_proposals are committed here; never edited manually.
_OVERRIDES_PATH = Path(__file__).parent / "parameter_overrides.json"
_OVERRIDES: dict = {}
if _OVERRIDES_PATH.exists():
    with open(_OVERRIDES_PATH) as _f:
        _OVERRIDES = json.load(_f)


def _get(key: str, default):
    return _OVERRIDES.get(key, default)


# --- Data source ---
# All market data (US, TSX/TSX-V, VIX/VVIX) is sourced from EODHD.
# PolygonClient and YFinanceClient are retained in plugins/ for reference only.

# --- Signal weights (must sum to 1.0) ---
SIGNAL_WEIGHTS = {
    "sma_200": _get("sma_200_weight", 0.30),
    "sma_50": _get("sma_50_weight", 0.25),
    "macd": _get("macd_weight", 0.25),
    "rsi": _get("rsi_weight", 0.20),
}

# --- RSI thresholds ---
RSI_HEALTHY_LOW = 40
RSI_HEALTHY_HIGH = 70
RSI_OVERSOLD = 30

# --- VIX regime bands: (upper_bound, label, multiplier) ---
VIX_REGIMES = [
    (15, "low", 0.85),
    (20, "normal", 1.00),
    (30, "elevated", 1.10),
    (40, "high", 1.20),
    (999, "extreme", 0.70),
]

# --- VIX trend thresholds (ratio of VIX to its 20-day SMA) ---
VIX_TREND_UPPER = 1.20  # above → expanding
VIX_TREND_LOWER = 0.85  # below → contracting

# --- VVIX environment thresholds ---
VVIX_CLEAN_FEAR_MAX = 100
VVIX_CHAOTIC_MIN = 115
VVIX_SPIKE_THRESHOLD = 120
VVIX_COMPLACENT_MAX = 85

# --- Relatedness ---
# NOTE: relatedness_matrix has no wired production reader as of 2026-07-08 — the
# LLM peer-lookup consumer is designed (LLM_PEER_* constants below) but not built.
# The 30d window was dropped and MIN_R raised to 0.50 to trim ~20M weekly rows for
# the AWS RDS move; the trim is behavior-neutral precisely because nothing reads the
# table yet. "Wire or retire the peer lookup" is a Phase 4 feature-selection decision
# (see docs/roadmap.md) — a table with no reader and no decision date is a latent bug.
CORRELATION_WINDOWS = [90, 365]  # 30d dropped 2026-07-08 (AWS trim); migration 011 deletes its rows
PEER_CLUSTER_THRESHOLD = 0.65
BETA_WINDOWS = [90, 365]
RELATEDNESS_MIN_R = 0.50  # pairs below this |pearson_r| are not written; raised 0.20→0.50 (AWS trim, under the 0.6 consumption threshold)
CORRELATION_CHUNK_SIZE = 500  # tickers per block in chunked corr; smaller = less CPU spike

# --- LLM ---
ANTHROPIC_MODEL_ANALYSIS = "claude-sonnet-4-6"
ANTHROPIC_MODEL_CLASSIFICATION = "claude-haiku-4-5-20251001"
LLM_MAX_TOKENS = 1000
LLM_SIGNAL_THRESHOLD = 0.5  # min abs(composite_vix_adj) to queue for analysis
LLM_PEER_CORRELATION_MIN_R = 0.6  # min 90-day pearson_r to include a peer in the prompt
LLM_PEER_COUNT = 5  # top N peers by r to include
LLM_SIGNAL_HISTORY_DAYS = 14  # days of recent signals to include in prompt
LLM_BRIEF_TOP_N = 12  # top N tickers (by confidence) to include in daily brief

# --- Sector ETF proxies ---
SECTOR_ETFS = {
    "tech": "XLK",
    "financials": "XLF",
    "energy": "XLE",
    "healthcare": "XLV",
    "consumer": "XLY",
    "market": "SPY",
    "nasdaq": "QQQ",
}

# --- Indicator windows ---
SMA_SHORT_WINDOW = 50
SMA_LONG_WINDOW = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_WINDOW = 14
BB_WINDOW = 20
BB_STD = 2
VIX_SMA_WINDOW = 20
PRICE_HISTORY_DAYS = 250  # enough for SMA_200 + buffer
INDICATOR_BATCH_SIZE = 500  # equity tickers per batch; smaller = lower peak RSS
RELATEDNESS_HISTORY_DAYS = 400  # enough for 365-day correlation window + buffer

# --- Signal versioning (Phase 6) ---
# Increment on any change to SIGNAL_WEIGHTS or VIX/VVIX thresholds.
# Format: "vMAJOR.MINOR" — major for weight changes, minor for threshold changes.
SIGNAL_VERSION = "v1.0"

# --- Outcome evaluation (Phase 6) ---
PREDICTION_HORIZONS = [5, 10, 20]  # trading days
CORRECT_SIGNAL_THRESHOLD = 0.01  # min return magnitude to count as confirmed (1%)

# --- Accuracy aggregation (Phase 6) ---
ACCURACY_MIN_SAMPLE_SIZE = 30  # min signals required before reporting accuracy

# --- Optimization (Phase 6) ---
OPTIMIZATION_TARGET_HORIZON = 10  # primary horizon for parameter scoring
OPTIMIZATION_TARGET_BIAS = "bullish"  # primary bias to optimize for
OPTIMIZATION_MIN_ACCURACY_DELTA = 0.03  # min projected improvement to generate a proposal

# --- Signal v2 ---
# All v2 tunables live here. v1 config above is untouched until Phase 6 cutover.
V2_PREDICTION_HORIZONS = [5, 10, 20, 21, 42, 63]  # trading days; 5/10/20 kept for v1 comparability
V2_PRIMARY_HORIZON = 42  # primary calibration/optimization target
V2_VOL_LOOKBACK_DAYS = 20  # trailing window for daily vol estimate (pct returns)
V2_THRESHOLD_VOL_MULT = 0.5  # move must exceed k * sigma_daily * sqrt(h)
V2_BENCHMARK_TICKER = "SPY"
V2_BETA_WINDOW = 90  # which sector_beta window to use for beta lookup
V2_EPISODE_GAP_DAYS = 5  # trading-day gap that starts a new episode
V2_MAX_DAILY_RATIO = 3.0  # quarantine guard: 1-day close ratio above this flags a corp-action seam
V2_MIN_PRICE = 1.00  # USD/CAD nominal floor; sub-$1 tickers excluded from v2 eligibility
SIGNAL_VERSION_V2 = "v2.0"

# Phase 2 — continuous feature windows
V2_FEATURE_SMA_SHORT = 50  # dist_sma50
V2_FEATURE_SMA_LONG = 200  # dist_sma200, dist_sma200_z
V2_FEATURE_SMA_LONG_Z_WINDOW = 252  # trailing window for dist_sma200_z z-score
V2_FEATURE_BB_WINDOW = 20  # bb_pctb (reuses BB_STD=2 from v1)
V2_FEATURE_52W_WINDOW = 252  # dist_52w_high, dist_52w_low
V2_FEATURE_VOL_SMA_WINDOW = 20  # vol_ratio denominator
V2_FEATURE_RET_MED_WINDOW = 21  # ret_21d
V2_FEATURE_RET_LONG_WINDOW = 63  # ret_63d

# Phase 3 — regime classification
V2_ADX_WINDOW = 14  # ADX Wilder smoothing window
V2_ADX_TREND_MIN = 25  # ADX >= this → trending
V2_SLOPE_WINDOW = 63  # SMA200 slope look-back (trading days)
V2_SLOPE_TREND_MIN = 0.02  # |sma200_slope| >= this → trending

# Beta shrinkage — applied at usage in outcome resolver, NOT stored in sector_beta.
# beta_used = W * beta_hat + (1 - W) * 1.0; shrinks extreme betas toward market beta.
V2_BETA_SHRINKAGE_W = 0.67

# Tools container batch size for memory-bounded analysis scripts.
# At float32 + ~20 columns, 500 tickers × ~1300 rows ≈ 50 MB per batch.
TOOLS_TICKER_BATCH = 500
