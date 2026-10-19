"""Prometheus metrics exposed at /metrics."""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("api_requests_total", "API requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("api_request_seconds", "API latency", ["route"])
RATE_LIMITED = Counter("api_rate_limited_total", "Requests rejected by rate limits", ["bucket"])
JOB_RUNS = Counter("job_runs_total", "Background job runs", ["kind", "status"])
JOB_DURATION = Histogram("job_duration_seconds", "Background job duration", ["kind"],
                         buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900))
CONNECTOR_STATE = Gauge("connector_up", "1 when the connector is healthy", ["connector"])
LLM_TOKENS = Gauge("llm_tokens_today", "LLM tokens used today (UTC)")
LLM_CALLS = Gauge("llm_calls_today", "LLM calls today (UTC)")
STALE_INSTRUMENTS = Gauge("stale_instruments", "Instruments whose latest price bar is stale")
DATA_COVERAGE = Gauge("score_complete_ratio", "Share of instruments with a complete five-pillar score")
CACHE_HIT_RATIO = Gauge("cache_hit_ratio", "Cache hit ratio since start")
