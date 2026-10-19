"""Bind our call intents to the argument names a server actually advertises.

Tool argument names are not assumed. Given a tool's `inputSchema` and a dict of intents (symbol,
start_date, end_date, ...), `bind_args` produces arguments using the schema's own property names
and the date format its descriptions ask for. If a required property cannot be bound the tool is
treated as unsupported for that call.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


class BindError(ValueError):
    pass


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


SYNONYMS: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "ticker", "tradingsymbol", "stock", "scrip", "stocksymbol", "scripcode"),
    "symbols": ("symbols", "tickers", "stocks", "instruments", "scrips"),
    "start_date": ("startdate", "fromdate", "from", "start", "datefrom"),
    "end_date": ("enddate", "todate", "to", "end", "dateto"),
    "date": ("date", "tradedate", "asof", "asofdate", "tradingdate"),
    "days": ("days", "ndays", "period", "window"),
    "months": ("months",),
    "limit": ("limit", "top", "topn", "count", "n"),
    "query": ("query", "q", "keyword", "search", "searchtext", "text", "name"),
    "instrument_token": ("instrumenttoken", "token"),
    "index_name": ("indexname", "index"),
}

_DATE_FORMATS = [
    (r"yyyy-mm-dd\s*hh:mm:ss", "%Y-%m-%d %H:%M:%S"),
    (r"dd-mmm-yyyy", "%d-%b-%Y"),
    (r"dd-mm-yyyy", "%d-%m-%Y"),
    (r"dd/mm/yyyy", "%d/%m/%Y"),
    (r"ddmmyyyy", "%d%m%Y"),
    (r"yyyymmdd", "%Y%m%d"),
    (r"yyyy-mm-dd", "%Y-%m-%d"),
]


def date_format_for(prop: dict[str, Any]) -> str:
    hint = f"{prop.get('description', '')} {prop.get('pattern', '')} {prop.get('format', '')}".lower()
    for pattern, fmt in _DATE_FORMATS:
        if re.search(pattern, hint):
            return fmt
    return "%Y-%m-%d"


def _coerce(value: Any, prop: dict[str, Any], intent: str) -> Any:
    if isinstance(value, date | datetime):
        fmt = date_format_for(prop)
        if "%H" in fmt and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.max.time() if intent == "end_date" else datetime.min.time()).replace(microsecond=0)
        return value.strftime(fmt)
    t = prop.get("type")
    if t == "array" and not isinstance(value, list | tuple):
        return [value]
    if t == "integer":
        return int(value)
    if t == "number" and isinstance(value, str):
        return float(value)
    if t == "string" and not isinstance(value, str):
        return str(value)
    return list(value) if isinstance(value, tuple) else value


def find_property(schema: dict[str, Any], intent: str) -> str | None:
    props = schema.get("properties") or {}
    wanted = SYNONYMS.get(intent, (norm(intent),))
    by_norm = {norm(k): k for k in props}
    for w in wanted:
        if w in by_norm:
            return by_norm[w]
    return by_norm.get(norm(intent))


def documented_default(prop: dict[str, Any]) -> tuple[bool, Any]:
    """Some servers mark an argument required but document a value that means "use the default"
    (for example "Pass 0 for default" or "pass 'today' for latest data"). Use that value only
    when the schema's own description states it."""
    if "default" in prop:
        return True, prop["default"]
    d = str(prop.get("description", "")).lower()
    t = prop.get("type")
    if t in ("integer", "number") and "pass 0 for default" in d:
        return True, 0
    if t == "string" and re.search(r"pass 'today'", d):
        return True, "today"
    if t == "string" and "null or empty for all" in d:
        return True, ""
    return False, None


def bind_args(schema: dict[str, Any] | None, intents: dict[str, Any]) -> dict[str, Any]:
    schema = schema or {}
    props: dict[str, Any] = schema.get("properties") or {}
    out: dict[str, Any] = {}
    for intent, value in intents.items():
        if value is None:
            continue
        name = find_property(schema, intent)
        if name and name not in out:
            out[name] = _coerce(value, props[name], intent)
    missing = []
    for r in schema.get("required") or []:
        if r in out:
            continue
        found, value = documented_default(props.get(r, {}))
        if found:
            out[r] = value
        else:
            missing.append(r)
    if missing:
        raise BindError(f"cannot supply required argument(s) {', '.join(missing)} from intents {sorted(intents)}")
    return out
