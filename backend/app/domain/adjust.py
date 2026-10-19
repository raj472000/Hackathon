"""Corporate-action price adjustment.

Methodology (version `adjust-v1`): back-adjustment for splits and bonus issues only. For an action
with share multiplier `ratio` effective on `ex_date`, every bar before `ex_date` has its prices
divided by `ratio` and its volume multiplied by `ratio`. Cash dividends are not adjusted, so the
series is a split-adjusted price series, not a total-return series.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

VERSION = "adjust-v1"
PRICE_COLS = ("open", "high", "low", "close", "prev_close")


@dataclass(frozen=True)
class CorporateAction:
    ex_date: date
    kind: str  # split | bonus | dividend | other
    ratio: float | None = None  # share multiplier for split/bonus; amount per share for dividend
    description: str = ""

    @classmethod
    def bonus(cls, ex_date: date, bonus_shares: float, held_shares: float, description: str = "") -> CorporateAction:
        return cls(ex_date, "bonus", (bonus_shares + held_shares) / held_shares, description)


def adjust_bars(df: pd.DataFrame, actions: Sequence[CorporateAction]) -> pd.DataFrame:
    out = df.copy()
    cols = [c for c in PRICE_COLS if c in out.columns]
    out[cols] = out[cols].astype(float)
    if "volume" in out.columns:
        out["volume"] = out["volume"].astype(float)
    for a in actions:
        if a.kind not in ("split", "bonus") or not a.ratio or a.ratio <= 0 or a.ratio == 1:
            continue
        before = out.index < pd.Timestamp(a.ex_date)
        out.loc[before, cols] = out.loc[before, cols] / a.ratio
        if "volume" in out.columns:
            out.loc[before, "volume"] = out.loc[before, "volume"] * a.ratio
    return out


def detect_unexplained_gaps(df: pd.DataFrame, actions: Sequence[CorporateAction], threshold_pct: float = 40.0) -> list[dict[str, Any]]:
    """Close-to-close moves beyond the threshold with no split/bonus on that date.

    Such a gap usually means a corporate action is missing from the source, so derived returns
    and moving averages across it are unreliable.
    """
    explained = {a.ex_date for a in actions if a.kind in ("split", "bonus")}
    chg = df["close"].astype(float).pct_change() * 100
    out = []
    for ts, pct in chg.items():
        if pct == pct and abs(pct) >= threshold_pct and ts.date() not in explained:
            out.append({"date": ts.date().isoformat(), "change_pct": round(float(pct), 2),
                        "warning": "price gap not explained by a known split or bonus"})
    return out


_SPLIT = re.compile(r"from\s+(?:rs\.?|re\.?|inr)?\s*([\d.]+).*?to\s+(?:rs\.?|re\.?|inr)?\s*([\d.]+)", re.I)
_BONUS = re.compile(r"bonus\D*?(\d+)\s*:\s*(\d+)", re.I)
_DIVIDEND = re.compile(r"dividend.*?(?:rs\.?|re\.?|inr)\s*([\d.]+)", re.I)


def parse_action(text: str) -> tuple[str, float] | None:
    """Parse an exchange 'purpose' string into (kind, ratio). Returns None when it is not a
    split, bonus or cash dividend, or when the numbers cannot be read."""
    t = text or ""
    if re.search(r"split|sub-?division", t, re.I):
        m = _SPLIT.search(t)
        if m and float(m.group(2)) > 0:
            return ("split", float(m.group(1).rstrip(".")) / float(m.group(2).rstrip(".")))
        return None
    m = _BONUS.search(t)
    if m and int(m.group(2)) > 0:
        return ("bonus", (int(m.group(1)) + int(m.group(2))) / int(m.group(2)))
    m = _DIVIDEND.search(t)
    if m:
        return ("dividend", float(m.group(1).rstrip(".")))
    return None
