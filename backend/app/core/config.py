"""Application settings. All values come from the environment (or `.env`); nothing secret has a
real default and nothing here is ever sent to the browser."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
_INLINE_COMMENT = re.compile(r"\s+#")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"), extra="ignore")

    app_env: Literal["local", "test", "staging", "production"] = "local"
    # demo: connectors point at the local mock MCP servers and every record is labelled demo.
    data_mode: Literal["demo", "live"] = "demo"
    log_level: str = "INFO"
    # PostgreSQL through psycopg 3. Any postgres:// / postgresql:// / +asyncpg URL is rewritten to this driver.
    # No built-in default: set DATABASE_URL in .env (see .env.example).
    database_url: str | None = None
    redis_url: str | None = None
    api_auth_token: SecretStr | None = None
    cors_origins: str = ""  # comma-separated; empty = same-origin only (the web app proxies /api)
    default_market_timezone: str = "Asia/Kolkata"
    scheduler_enabled: bool = True
    # The schema comes from Alembic (`alembic upgrade head`, run by scripts/dev.sh and the Compose
    # `migrate` service). True creates tables straight from the models, which tests use on SQLite.
    auto_create_schema: bool = False
    auto_initial_refresh: bool = True

    # --- MCP connectors
    nse_bhavcopy_mcp_url: str | None = None
    nse_cm_market_mcp_url: str | None = None
    broker_mcp_url: str | None = None
    mcp_connect_timeout_seconds: float = 10.0
    mcp_tool_timeout_seconds: float = 30.0
    mcp_max_concurrency: int = 4
    mcp_max_retries: int = 2
    nse_mcp_min_interval_seconds: float = 0.2
    broker_mcp_min_interval_seconds: float = 0.35
    # Kite binds the account holder's approval to the MCP session. Keeping the session id on disk (server side only,
    # owner-only permissions, never logged or sent to the browser) lets an approval survive API restarts. Set
    # KITE_PERSIST_SESSION=false to forget it on every restart. Live mode only.
    kite_persist_session: bool = True
    kite_session_file: Path = BACKEND_DIR / "var" / "kite_mcp_session.json"
    # While authorised, confirm the session this often (get_profile), so an idle session stays in use and a session
    # Kite has ended is noticed within minutes. 0 disables.
    kite_keepalive_seconds: float = 300.0
    breaker_failure_threshold: int = 5
    breaker_reset_seconds: float = 60.0

    # --- Fundamentals (no MCP source supplies statements)
    fundamentals_dir: Path = BACKEND_DIR / "var" / "fundamentals"
    # NSE corporate filings (financial results XBRL, con-call transcripts). Unset = disabled.
    nse_filings_base_url: str | None = None  # e.g. https://www.nseindia.com/api
    nse_filings_document_hosts: str = "nsearchives.nseindia.com"  # comma-separated hosts documents may be fetched from
    nse_filings_user_agent: str = "Mozilla/5.0 (compatible; MarketsIntelligence/1.0; research)"
    nse_filings_min_interval_seconds: float = 1.0
    nse_filings_timeout_seconds: float = 30.0
    nse_filings_cache_dir: Path = BACKEND_DIR / "var" / "nse_filings"
    nse_filings_max_companies_per_run: int = 400  # statements are fetched for this many companies per refresh, missing first
    # Whole-market universe and prices from NSE's published files (see analysis/HOW_TO_RUN.md). Unset = disabled.
    universe_scope: Literal["reference", "all"] = "reference"  # all = every NSE-listed equity in UNIVERSE_SERIES
    universe_series: str = "EQ,BE"
    nse_equity_list_url: str | None = None  # e.g. https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
    nse_industry_list_url: str | None = None  # e.g. .../content/indices/ind_niftytotalmarket_list.csv
    nse_bhavcopy_url_template: str | None = None  # e.g. .../content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip

    # --- LLM: any OpenAI-compatible chat-completions provider (OpenRouter, OpenAI, Together, Groq,
    # a vLLM/Ollama server, ...). Everything provider-specific is configuration; nothing is built in.
    llm_enabled: bool = False
    llm_provider_name: str = "LLM"  # display label only, e.g. "OpenRouter"
    llm_base_url: str | None = None  # e.g. https://openrouter.ai/api/v1 (with or without /chat/completions)
    llm_api_key: SecretStr | None = None
    llm_model_id: str | None = None  # exact id as the provider lists it
    # JSON objects: extra HTTP headers and extra request-body fields sent with every call, e.g.
    # LLM_EXTRA_HEADERS={"HTTP-Referer": "https://example.com", "X-Title": "Markets Intelligence"}
    # LLM_EXTRA_BODY={"usage": {"include": true}}
    llm_extra_headers: dict[str, str] | None = None
    llm_extra_body: dict[str, Any] | None = None
    llm_verify_model_list: bool = True  # check LLM_MODEL_ID against GET {LLM_BASE_URL}/models before a paid call
    llm_response_format: Literal["json_schema", "json_object", "none"] = "json_schema"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 1
    llm_max_output_tokens: int = 4000  # reasoning models count thinking tokens against this
    llm_temperature: float = 0.1
    llm_daily_budget: int = 200  # research calls per UTC day
    llm_daily_token_budget: int = 2_000_000

    # --- Web research
    # ddgs needs no key. In demo mode web research is switched off: demo symbols are fictional.
    # An unsupported value does not stop start-up; web research is then reported as unavailable.
    web_search_provider: str = "ddgs"
    web_search_api_key: SecretStr | None = None
    web_search_base_url: str | None = None  # endpoint for HTTP search providers, e.g. https://api.search.brave.com/res/v1/news/search
    web_search_max_results: int = 8
    web_search_timeout_seconds: float = 15.0

    # --- Scheduling (cron, in DEFAULT_MARKET_TIMEZONE)
    theme_refresh_cron: str = "30 6 1 * *"  # monthly
    rrg_refresh_cron: str = "0 */12 * * *"  # every 12 hours
    price_refresh_cron: str = "30 19 * * 1-5"  # after NSE close
    instrument_refresh_cron: str = "0 7 * * 1-5"
    alerts_cron: str = "*/15 * * * *"

    # --- Universe and benchmark
    universe_file: Path = BACKEND_DIR / "data" / "reference" / "nse_universe.csv"
    benchmark_symbol: str = "NIFTY 50"
    history_years: int = 3
    ingest_max_concurrency: int = 4
    # Live mode: symbols that failed because a source was unavailable are retried once, one at a time,
    # after this many seconds (enough for an open circuit breaker to half-open). 0 disables the retry.
    ingest_retry_delay_seconds: float = 65.0

    # --- Cache TTL (seconds) and rate limits
    cache_ttl_quote: int = 60
    cache_ttl_history: int = 6 * 3600
    cache_ttl_instrument: int = 24 * 3600
    rate_limit_research_per_minute: int = 6
    rate_limit_refresh_per_minute: int = 3
    rate_limit_default_per_minute: int = 300
    max_request_bytes: int = 5_000_000

    mock_mcp_host: str = "127.0.0.1"
    mock_mcp_port: int = Field(default=9100, description="Base port of the local mock MCP servers used in demo mode")

    @model_validator(mode="before")
    @classmethod
    def _clean_env_values(cls, data: Any) -> Any:
        """Make values from .env files and Docker Compose behave the same way.

        - `KEY=value   # note`: Compose keeps the note as part of the value. A `#` preceded by
          whitespace starts a comment and is removed; a `#` inside a value (a URL fragment) is kept.
        - Leading and trailing spaces are removed.
        - `KEY=` means "not set" for optional settings, so a blank API_AUTH_TOKEN does not switch
          authentication on with an empty token and a blank URL does not count as configured.
        """
        if not isinstance(data, dict):
            return data
        optional = {n for n, f in cls.model_fields.items() if f.default is None}
        out: dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, str):
                v = _INLINE_COMMENT.split(v, maxsplit=1)[0].strip()
                if not v and k in optional:
                    v = None
            out[k] = v
        return out

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str | None) -> str | None:
        """Use psycopg 3 for PostgreSQL whatever form the URL was written in."""
        if v is None:
            return None
        m = re.match(r"^(postgres|postgresql)(\+[a-z0-9_]+)?://", v)
        if not m:
            return v
        v = "postgresql+psycopg://" + v[m.end():]
        # asyncpg spells TLS as ?ssl=…; libpq/psycopg uses ?sslmode=…
        return re.sub(r"([?&])ssl=", r"\1sslmode=", v)

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not set. Add it to .env, e.g. "
                               "DATABASE_URL=postgresql+psycopg://markets:<password>@localhost:5432/markets (see .env.example).")
        return self.database_url

    @property
    def is_demo(self) -> bool:
        return self.data_mode == "demo"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def resolved_mcp_urls(self) -> dict[str, str | None]:
        """In demo mode the three connectors use the local mock servers unless a URL is set."""
        if self.is_demo:
            # Demo mode always uses the local mock servers, whatever live URLs are present in .env.
            p, h = self.mock_mcp_port, self.mock_mcp_host
            return {"nse_bhavcopy": f"http://{h}:{p}/mcp", "nse_cm_market": f"http://{h}:{p + 1}/mcp", "broker": f"http://{h}:{p + 2}/mcp"}
        return {"nse_bhavcopy": self.nse_bhavcopy_mcp_url, "nse_cm_market": self.nse_cm_market_mcp_url,
                "broker": self.broker_mcp_url}


@lru_cache
def get_settings() -> Settings:
    return Settings()
