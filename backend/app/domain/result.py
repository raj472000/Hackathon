"""Value object returned by every deterministic calculation.

A metric is never silently zero: when it cannot be computed, `value` is None and `status` plus
`reason` say why. `formula` and `inputs` make each number traceable on demand.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    OK = "ok"
    MISSING = "missing"
    NOT_MEANINGFUL = "not_meaningful"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_HISTORY = "insufficient_history"


@dataclass(frozen=True)
class MetricValue:
    name: str
    value: float | None
    status: Status = Status.OK
    reason: str | None = None
    unit: str = "ratio"  # ratio | pct | x | days | currency | price | count | score
    formula: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def ok(name: str, value: float, unit: str, formula: str, inputs: dict[str, Any], reason: str | None = None) -> MetricValue:
    return MetricValue(name, float(value), Status.OK, reason, unit, formula, inputs)


def bad(name: str, status: Status, reason: str, unit: str, formula: str, inputs: dict[str, Any] | None = None) -> MetricValue:
    return MetricValue(name, None, status, reason, unit, formula, inputs or {})


def first_missing(inputs: dict[str, Any]) -> str | None:
    """Name of the first input that is None or NaN, if any."""
    for k, v in inputs.items():
        if v is None or (isinstance(v, float) and v != v):
            return k
    return None
