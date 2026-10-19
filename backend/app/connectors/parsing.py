"""Tolerant normalisation of provider payloads.

Upstream field names are matched through synonym tables on normalised keys, because the exact
response shapes of a server can only be confirmed against the live server. A field that cannot be
found stays missing (NaN / None); nothing is defaulted to zero.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.connectors.binding import norm

BAR_KEYS: dict[str, tuple[str, ...]] = {
    "date": ("date", "tradedate", "tradingdate", "timestamp", "mktdate", "chtimestamp", "traddt", "bizdt"),
    "open": ("open", "openprice", "opnpric", "chopeningprice"),
    "high": ("high", "highprice", "hghpric", "chtradehighprice", "dayhigh"),
    "low": ("low", "lowprice", "lwpric", "chtradelowprice", "daylow"),
    "close": ("close", "closeprice", "clspric", "chclosingprice", "lastclose"),
    "prev_close": ("prevclose", "previousclose", "precloseprice", "prvsclsgpric", "chpreviousclsprice", "prevcloseprice"),
    "volume": ("volume", "totaltradedquantity", "tottrdqty", "ttltradgvol", "tradedqty", "totaltradedvolume", "chtottradedqty", "qty"),
    "traded_value": ("tradedvalue", "turnover", "tottrdval", "ttltrfval", "totaltradedvalue", "chtottradedval", "value"),
}
QUOTE_KEYS: dict[str, tuple[str, ...]] = {
    "last_price": ("lastprice", "ltp", "last", "lasttradedprice"),
    "open": BAR_KEYS["open"], "high": BAR_KEYS["high"], "low": BAR_KEYS["low"],
    "prev_close": BAR_KEYS["prev_close"],
    "volume": BAR_KEYS["volume"],
    "change_pct": ("pchange", "changepct", "percentchange", "perchange", "percchange", "changepercent"),
}
QUOTE_TIME_KEYS = ("latesttimestamp", "lastupdatetime", "nsetimestamp", "timeval", "timestamp", "lasttradetime", "asof", "updatedat", "time")
_ROW_CONTAINERS = ("data", "rows", "history", "records", "candles", "result", "results", "items", "stocks")


def to_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return None if v != v else float(v)
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "NA", "N/A", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%Y%m%d", "%d%m%Y"):
        try:
            return datetime.strptime(s[:11].strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(v: Any) -> datetime | None:
    if v is None:
        return None
    try:
        ts = pd.Timestamp(str(v)).floor("s")
    except (ValueError, TypeError):
        return None
    if ts is pd.NaT:
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("Asia/Kolkata")
    return ts.to_pydatetime()


def find_rows(payload: Any) -> list[dict[str, Any]]:
    """Locate the list of row objects in a tool payload."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for k, v in payload.items():
            if norm(k) in _ROW_CONTAINERS and isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        for v in payload.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def pick(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    by = {norm(k): v for k, v in row.items()}
    for n in names:
        if n in by and by[n] is not None:
            return by[n]
    return None


def get_ci(payload: Any, *names: str) -> Any:
    """Case/format-insensitive key lookup on a dict payload."""
    return pick(payload, tuple(norm(n) for n in names)) if isinstance(payload, dict) else None


def bars_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    recs = []
    for r in rows:
        d = parse_date(pick(r, BAR_KEYS["date"]))
        close = to_float(pick(r, BAR_KEYS["close"]))
        if d is None or close is None:
            continue
        rec: dict[str, Any] = {"date": pd.Timestamp(d), "close": close}
        for f in ("open", "high", "low", "prev_close", "volume", "traded_value"):
            rec[f] = to_float(pick(r, BAR_KEYS[f]))
        recs.append(rec)
    cols = ["open", "high", "low", "close", "prev_close", "volume", "traded_value"]
    if not recs:
        return pd.DataFrame(columns=cols, dtype=float)
    df = pd.DataFrame(recs).drop_duplicates("date", keep="last").set_index("date").sort_index()
    df.index.name = None
    return df[cols].astype(float)


def unwrap_quote(payload: Any) -> dict[str, Any]:
    """Return the object that holds the price fields. Servers nest it differently (top level,
    under `stock`, under `index`, or as the first row of a list)."""
    if isinstance(payload, dict):
        if pick(payload, QUOTE_KEYS["last_price"]) is not None:
            return payload
        for v in payload.values():
            if isinstance(v, dict) and pick(v, QUOTE_KEYS["last_price"]) is not None:
                return {**{k: x for k, x in payload.items() if not isinstance(x, dict | list)}, **v}
    rows = find_rows(payload)
    return rows[0] if rows else (payload if isinstance(payload, dict) else {})


def payload_error(payload: Any) -> str | None:
    """Some tools report failure inside a normal result, e.g. {"error": "Symbol not found"}."""
    if isinstance(payload, dict) and isinstance(payload.get("error"), str) and payload["error"].strip():
        return payload["error"].strip()[:300]
    return None
