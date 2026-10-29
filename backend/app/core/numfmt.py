"""Display precision for numbers that reach a language model or its readers.

Metrics are stored at full precision; nobody needs 15 digits after the decimal point. Two layers keep
text to at most two decimals:

1. `round_floats` on evidence before it is serialised into a prompt, so the model never sees long decimals.
2. `tidy_decimals` on model-written text, as a backstop when the model computes or echoes one anyway.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

DISPLAY_DECIMALS = 2

# A number with three or more decimals that is not part of a timestamp (16:00:27.123), a version (1.2.345)
# or an identifier (ABC1.234).
_LONG_DECIMAL = re.compile(r"(?<![\w.:])(-?\d+)\.(\d{3,})(?![\w.])")


def round_floats(obj: Any, ndigits: int = DISPLAY_DECIMALS) -> Any:
    """Copy of `obj` with every float (and Decimal) rounded; other values are returned unchanged."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, Decimal):
        return obj.quantize(Decimal(1).scaleb(-ndigits), rounding=ROUND_HALF_UP) if obj.is_finite() else obj
    if isinstance(obj, dict):
        return {k: round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return type(obj)(round_floats(v, ndigits) for v in obj)
    return obj


def tidy_decimals(text: str, ndigits: int = DISPLAY_DECIMALS) -> str:
    """Round every long decimal in free text to `ndigits` places (half up), e.g. 13.152988660388237 -> 13.15."""
    q = Decimal(1).scaleb(-ndigits)

    def fix(m: re.Match[str]) -> str:
        return str(Decimal(m.group(0)).quantize(q, rounding=ROUND_HALF_UP))

    return _LONG_DECIMAL.sub(fix, text)
