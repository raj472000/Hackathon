"""Provider interfaces and result types. Business services depend on these, never on a specific
MCP server or vendor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

import pandas as pd

from app.domain.adjust import CorporateAction
from app.domain.provenance import DataPoint


class ProviderUnavailable(Exception):
    """A provider cannot serve this request. `state` is the degraded-data state to show."""

    def __init__(self, message: str, state: str = "unavailable", provider: str | None = None) -> None:
        super().__init__(message)
        self.state = state  # unavailable | not_supported | login_required | not_configured
        self.provider = provider


@dataclass
class BarsResult:
    symbol: str
    frame: pd.DataFrame  # index: date ascending; columns open, high, low, close, volume [, prev_close, traded_value]
    provider: str
    source_id: str
    retrieved_at: datetime
    adjustment: str  # unadjusted | split_adjusted
    currency: str = "INR"
    is_demo: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class QuoteResult:
    symbol: str
    fields: dict[str, DataPoint]
    provider: str
    retrieved_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


def capability(supported: bool, tool: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {"supported": supported, "tool": tool, "reason": reason}


class MarketDataProvider(Protocol):
    name: str

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def capabilities(self) -> dict[str, dict[str, Any]]: ...
    def health(self) -> dict[str, Any]: ...
    async def get_history(self, symbol: str, start: date, end: date) -> BarsResult: ...
    async def get_quote(self, symbol: str) -> QuoteResult: ...
    async def get_corporate_actions(self, symbol: str) -> list[CorporateAction]: ...
    async def search(self, query: str) -> list[dict[str, Any]]: ...


class BrokerDataProvider(Protocol):
    name: str

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def capabilities(self) -> dict[str, dict[str, Any]]: ...
    def health(self) -> dict[str, Any]: ...
    async def auth_state(self) -> dict[str, Any]: ...
    async def get_history(self, symbol: str, start: date, end: date) -> BarsResult: ...
    async def get_quotes(self, symbols: list[str]) -> dict[str, QuoteResult]: ...
    async def search(self, query: str) -> list[dict[str, Any]]: ...


class FundamentalsProvider(Protocol):
    name: str

    def status(self) -> dict[str, Any]: ...
    def get_statements(self, symbol: str) -> dict[str, Any] | None: ...


class WebSearchProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...
    async def search(self, query: str, max_results: int = 8) -> list[dict[str, Any]]: ...


class LLMProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...
    async def complete_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]: ...
