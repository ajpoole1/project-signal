"""All tunable values for Project Signal. Nothing is hardcoded in DAG or plugin files."""

# --- Data source ---
# All market data (US, TSX/TSX-V, VIX/VVIX) is sourced from EODHD.
# PolygonClient and YFinanceClient are retained in plugins/ for reference only.

# --- Signal weights (must sum to 1.0) ---
SIGNAL_WEIGHTS = {
    "sma_200": 0.30,
    "sma_50": 0.25,
    "macd": 0.25,
    "rsi": 0.20,
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
CORRELATION_WINDOWS = [30, 90, 365]
PEER_CLUSTER_THRESHOLD = 0.65
BETA_WINDOWS = [90, 365]

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
RELATEDNESS_HISTORY_DAYS = 400  # enough for 365-day correlation window + buffer
