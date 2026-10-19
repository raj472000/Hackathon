"""File-backed fundamentals provider.

Neither NSE MCP nor Kite supplies financial statements, so statements enter the system as
normalised JSON files (one per symbol) written by an upload endpoint or an external pipeline.
Each file records its source, currency and unit. Demo files are accepted only in demo mode.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-_. ]{0,29}$")
UNITS = {"crore": 1.0, "lakh": 0.01, "million": 0.1, "billion": 100.0, "absolute": 1e-7}  # multiplier to INR crore

NUMERIC_FIELDS = (
    "revenue", "cogs", "ebitda", "depreciation", "ebit", "interest", "pbt", "tax", "pat", "eps", "diluted_eps",
    "exceptional_items", "equity", "total_debt", "short_term_debt", "long_term_debt", "lease_liabilities", "cash",
    "short_term_investments", "total_assets", "current_assets", "current_liabilities", "total_liabilities", "inventory",
    "receivables", "payables", "goodwill", "minority_interest", "cfo", "capex", "cfi", "cff", "dividends_paid",
    "buybacks", "shares_outstanding", "dividend_per_share",
)
# Per-share and share-count fields are not scaled by the monetary unit.
UNSCALED = {"eps", "diluted_eps", "shares_outstanding", "dividend_per_share"}


class FundamentalsValidationError(ValueError):
    pass


class Period(BaseModel):
    model_config = ConfigDict(extra="allow")
    period_end: date
    period_type: Literal["annual", "quarterly"] = "annual"
    fiscal_year: int | None = None
    published_at: date | None = None

    @model_validator(mode="before")
    @classmethod
    def _numbers(cls, d: Any) -> Any:
        if isinstance(d, dict):
            for k in NUMERIC_FIELDS:
                v = d.get(k)
                if v is not None and (isinstance(v, bool) or not isinstance(v, int | float)):
                    raise ValueError(f"{k} must be a number or null, got {v!r}")
        return d


class StatementFile(BaseModel):
    model_config = ConfigDict(extra="allow")
    symbol: str
    exchange: str = "NSE"
    currency: str = Field(default="INR", min_length=3, max_length=3)
    unit: str = "crore"
    source: str = Field(min_length=1, max_length=200)
    source_url: str | None = None
    source_note: str | None = None
    is_demo: bool = False
    accounting_standard: str | None = None
    consolidated: bool | None = None
    periods: list[Period]

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not SYMBOL_RE.match(v):
            raise ValueError("symbol has invalid characters")
        return v

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str) -> str:
        if v not in UNITS:
            raise ValueError(f"unit must be one of {sorted(UNITS)}")
        return v

    @model_validator(mode="after")
    def _periods(self) -> StatementFile:
        if not self.periods:
            raise ValueError("at least one period is required")
        keys = [(p.period_end, p.period_type) for p in self.periods]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate period_end within the same period_type")
        return self


class FileFundamentalsProvider:
    name = "fundamentals_file"

    def __init__(self, directory: Path, allow_demo: bool = False) -> None:
        self.dir = Path(directory)
        self.allow_demo = allow_demo

    def _path(self, symbol: str) -> Path:
        sym = symbol.strip().upper()
        if not SYMBOL_RE.match(sym):
            raise FundamentalsValidationError("symbol has invalid characters")
        return self.dir / f"{sym.replace(' ', '_')}.json"

    def validate(self, payload: dict[str, Any]) -> StatementFile:
        try:
            doc = StatementFile.model_validate(payload)
        except ValidationError as e:
            raise FundamentalsValidationError("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])) from e
        if doc.is_demo and not self.allow_demo:
            raise FundamentalsValidationError("demo fundamentals are not accepted in live mode")
        return doc

    def save(self, symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
        doc = self.validate(payload)
        if doc.symbol != symbol.strip().upper():
            raise FundamentalsValidationError(f"symbol in file ({doc.symbol}) does not match the target symbol ({symbol.upper()})")
        out = doc.model_dump(mode="json")
        out["ingested_at"] = datetime.now(UTC).isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(doc.symbol).write_text(json.dumps(out, indent=1))
        return out

    def get_statements(self, symbol: str) -> dict[str, Any] | None:
        """Validated statements with monetary values normalised to INR crore, or None."""
        try:
            path = self._path(symbol)
        except FundamentalsValidationError:
            return None
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text())
            doc = self.validate(raw)
        except (json.JSONDecodeError, FundamentalsValidationError):
            return None
        out = doc.model_dump(mode="json")
        mult = UNITS[doc.unit]
        for p in out["periods"]:
            for k in NUMERIC_FIELDS:
                if k not in UNSCALED and isinstance(p.get(k), int | float):
                    p[k] = p[k] * mult
        out["reported_unit"], out["unit"] = doc.unit, "crore"
        out["ingested_at"] = raw.get("ingested_at") or datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        return out

    def status(self) -> dict[str, Any]:
        files = sorted(self.dir.glob("*.json")) if self.dir.exists() else []
        return {"name": self.name, "state": "ok" if files else "no_data", "instruments_with_data": len(files),
                "directory": str(self.dir), "allow_demo": self.allow_demo,
                "note": "Statements are loaded from uploaded files; no MCP source provides financial statements."}
