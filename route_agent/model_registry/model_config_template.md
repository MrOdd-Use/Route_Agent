{
  "model_id": "openai:gpt-4o",
  "display_name": "GPT-4o",
  "provider": "openai",
  "api_model_name": "gpt-4o",

  "endpoint": {
    "base_url": "https://api.openai.com/v1"
  },

  "auth": {
    "auth_configured": true,
    "auth_type": "api_key",
    "key_fingerprint": "sha256:7fa3"
  },

  "capabilities": {
    "text": 0.95,
    "code": 0.90,
    "search": 0.70,
    "math": 0.88,
    "instruction_following": 0.94,
    "creative_writing": 0.92,
    "other": 0.60
  },

  "pricing": {
    "currency": "USD",
    "unit": "per_1k_tokens",
    "input": 0.005,
    "output": 0.015
  },

  "limits": {
    "context_length": 128000,
    "max_output_tokens": 4096,
    "timeout_ms": 60000,
    "max_concurrency": 20,
    "max_requests_per_minute": 600,
    "max_tokens_per_minute": 120000
  },

  "status": {
    "availability": "healthy",
    "latency_ms_p95": 820,
    "success_rate_5m": 0.997,
    "last_checked_at": "2026-02-07T21:00:00Z"
  },

  "routing": {
    "recommended_for": ["qa","analysis","coding"],
    "tags": ["fast","high_quality"]
  }
}
