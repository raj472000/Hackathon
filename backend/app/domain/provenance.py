"""Provenance carried by every important data point."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

# Degraded-data states exposed to the user instead of a fabricated value.
FIELD_STATES = ("ok", "missing", "not_supported", "delayed", "stale", "unavailable", "conflict")


@dataclass(frozen=True)
class DataPoint:
    field: str
    value: float | None
    provider: str
    retrieved_at: datetime
    as_of: datetime | None = None
    currency: str | None = None
    unit: str | None = None
    adjustment: str = "unadjusted"
    source_id: str | None = None  # e.g. MCP tool name
    status: str = "ok"
    reason: str | None = None
    is_demo: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["retrieved_at"] = self.retrieved_at.isoformat()
        d["as_of"] = self.as_of.isoformat() if self.as_of else None
        return d
