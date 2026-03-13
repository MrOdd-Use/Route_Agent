"""Centralized constants for model registry module.

This module contains all hardcoded values used across the model_registry
package to improve maintainability and consistency.
"""

# ==================== Network & Timeout ====================
DEFAULT_REQUEST_TIMEOUT_SECONDS: int = 20

# ==================== Pricing Constants ====================
PRICING_FLASH_BASE: float = 0.20
PRICING_PRO_BASE: float = 0.30

# DeepSeek pricing (lower than flash/pro)
PRICING_DEEPSEEK_CHAT_BASE: float = 0.10
PRICING_DEEPSEEK_REASONER_BASE: float = 0.20

# ==================== Rate Limits ====================
DEFAULT_TOKENS_PER_MINUTE: int = 250000
DEFAULT_REQUESTS_PER_MINUTE: int = 10
DEFAULT_REQUESTS_PER_DAY: int = 60
DEFAULT_MAX_CONCURRENCY: int = 5

# ==================== Cache TTL ====================
DEFAULT_CACHE_TTL_SECONDS: int = 21600  # 6 hours

# ==================== Dynamic Pricing ====================
DEFAULT_PRICING_SNIPPET_RADIUS: int = 220  # Characters around price match for keyword search

# ==================== Sentinel Values ====================
PRICE_UNAVAILABLE_SENTINEL: float = 1e12

# ==================== Error Messages ====================
ERROR_PROVIDER_NOT_CONFIGURED: str = "Provider '{provider}' is not configured (possibly missing API key or not enabled)."
ERROR_NO_SYNC_OUTPUT: str = "Provider '{provider}' was requested but produced no output."
ERROR_ZERO_MODELS: str = "Latest provider refresh returned zero models; fallback to previous successful local snapshot."

# ==================== Arena Leaderboard ====================
ARENA_LEADERBOARD_URL: str = "https://arena.ai/zh/leaderboard/"
ARENA_RANK_WEIGHT: float = 0.7  # rank vs ELO weight in hybrid normalization
ARENA_CACHE_DB_PATH: str = "data/arena_cache.sqlite3"
