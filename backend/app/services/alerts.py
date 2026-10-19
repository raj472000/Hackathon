"""Watchlist alerts. Evaluation is deterministic and idempotent: each trigger has a dedupe key, so
re-running the job never records the same notification twice. Delivery is the in-app history
only; no external channel is claimed."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.db.models import (
    Alert,
    AlertEvent,
    FinancialPeriod,
    Instrument,
    MetricSnapshot,
    SectorRotationSnapshot,
    ThemeSnapshot,
)
from app.domain.theme_dsl import METRIC_CATALOG

KINDS = ("price_move", "indicator_cross", "theme_membership", "sector_quadrant", "data_quality", "filing")
MAX_ALERTS_PER_OWNER = 50
WINDOW_METRIC = {"1W": "return_1w", "1M": "return_1m", "3M": "return_3m"}


class AlertIn(BaseModel):
    kind: str
    instrument_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=80)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        return v


def validate_params(a: AlertIn) -> dict[str, Any]:
    p = a.params
    need_inst = a.kind in ("price_move", "indicator_cross", "data_quality", "filing")
    if need_inst and a.instrument_id is None:
        raise ValueError(f"{a.kind} alerts need an instrument")
    if a.kind == "price_move":
        if p.get("window", "1W") not in WINDOW_METRIC or not 0 < float(p.get("pct", 0)) <= 100:
            raise ValueError("price_move needs window in 1W/1M/3M and 0 < pct <= 100")
        return {"window": p.get("window", "1W"), "pct": float(p["pct"])}
    if a.kind == "indicator_cross":
        if p.get("metric") not in METRIC_CATALOG or p.get("op") not in ("gt", "lt"):
            raise ValueError("indicator_cross needs a known metric and op gt/lt")
        return {"metric": p["metric"], "op": p["op"], "value": float(p["value"])}
    if a.kind == "theme_membership":
        return {"theme_id": int(p["theme_id"])}
    if a.kind == "sector_quadrant":
        return {"sector": str(p["sector"])[:80]}
    return {}


async def create(s: AsyncSession, owner: str, a: AlertIn) -> tuple[Alert, bool]:
    existing = await s.scalar(select(Alert).where(Alert.owner == owner, Alert.idempotency_key == a.idempotency_key))
    if existing:
        return existing, False
    if (await s.scalar(select(func.count()).select_from(Alert).where(Alert.owner == owner)) or 0) >= MAX_ALERTS_PER_OWNER:
        raise ValueError(f"alert limit of {MAX_ALERTS_PER_OWNER} reached")
    params = validate_params(a)
    if a.instrument_id is not None and await s.get(Instrument, a.instrument_id) is None:
        raise ValueError("instrument not found")
    alert = Alert(owner=owner, instrument_id=a.instrument_id, kind=a.kind, params=params, idempotency_key=a.idempotency_key, state={})
    s.add(alert)
    await s.commit()
    return alert, True


async def _fire(s: AsyncSession, c: Container, alert: Alert, dedupe: str, message: str) -> bool:
    if await s.scalar(select(AlertEvent.id).where(AlertEvent.alert_id == alert.id, AlertEvent.dedupe_key == dedupe)):
        return False
    try:
        async with s.begin_nested():
            s.add(AlertEvent(alert_id=alert.id, dedupe_key=dedupe, message=message[:500], triggered_at=c.clock.now()))
    except IntegrityError:
        return False
    alert.last_triggered_at = c.clock.now()
    return True


async def evaluate_all(c: Container, s: AsyncSession) -> dict[str, Any]:
    alerts = list((await s.scalars(select(Alert).where(Alert.status == "active"))).all())
    if not alerts:
        return {"evaluated": 0, "triggered": 0}
    snaps = {m.instrument_id: m for m in (await s.scalars(select(MetricSnapshot).where(MetricSnapshot.is_latest.is_(True)))).all()}
    insts = {i.id: i for i in (await s.scalars(select(Instrument))).all()}
    rots = list((await s.scalars(select(SectorRotationSnapshot).where(SectorRotationSnapshot.params_key == "default")
                                 .order_by(SectorRotationSnapshot.computed_at.desc()).limit(2))).all())
    fired = 0
    for a in alerts:
        inst = insts.get(a.instrument_id) if a.instrument_id else None
        m = snaps.get(a.instrument_id) if a.instrument_id else None
        sym = inst.symbol if inst else ""
        p, state = a.params, dict(a.state or {})
        if a.kind == "price_move" and m:
            v = m.flat.get(WINDOW_METRIC[p["window"]])
            if v is not None and abs(v) >= p["pct"]:
                fired += await _fire(s, c, a, f"{m.as_of}:{p['window']}", f"{sym} moved {v:+.1f}% over {p['window']} (threshold {p['pct']:g}%), as of {m.as_of}")
        elif a.kind == "indicator_cross" and m:
            v = m.flat.get(p["metric"])
            if v is not None:
                now_true = v > p["value"] if p["op"] == "gt" else v < p["value"]
                if now_true and state.get("was_true") is False:
                    label = METRIC_CATALOG[p["metric"]]["label"]
                    fired += await _fire(s, c, a, f"{m.as_of}:{p['metric']}:{p['op']}", f"{sym}: {label} crossed {'above' if p['op'] == 'gt' else 'below'} {p['value']:g} (now {v:.2f}), as of {m.as_of}")
                state["was_true"] = now_true
        elif a.kind == "data_quality" and inst:
            if m is None:
                fired += await _fire(s, c, a, f"nodata:{c.clock.now().date()}", f"{sym}: no metrics available; price data is missing")
            elif m.score.get("freshness") == "stale":
                fired += await _fire(s, c, a, f"stale:{m.score.get('last_bar')}", f"{sym}: price data is stale (last bar {m.score.get('last_bar')})")
        elif a.kind == "filing" and inst:
            latest = await s.scalar(select(func.max(FinancialPeriod.period_end)).where(FinancialPeriod.instrument_id == inst.id))
            if latest and state.get("latest") and str(latest) > state["latest"]:
                fired += await _fire(s, c, a, f"filing:{latest}", f"{sym}: financial statements for the period ending {latest} were ingested")
            if latest:
                state["latest"] = str(latest)
        elif a.kind == "theme_membership":
            two = list((await s.scalars(select(ThemeSnapshot).where(ThemeSnapshot.theme_id == p["theme_id"])
                                        .order_by(ThemeSnapshot.created_at.desc(), ThemeSnapshot.id.desc()).limit(2))).all())
            if len(two) == 2:
                await s.refresh(two[0], ["constituents"])
                await s.refresh(two[1], ["constituents"])
                new, old = ({k.instrument_id: k.symbol for k in t.constituents} for t in two)
                added, removed = set(new) - set(old), set(old) - set(new)
                if a.instrument_id:
                    added, removed = added & {a.instrument_id}, removed & {a.instrument_id}
                if added or removed:
                    msg = "; ".join(x for x in (("added: " + ", ".join(sorted(new[i] for i in added))) if added else "",
                                                ("removed: " + ", ".join(sorted(old[i] for i in removed))) if removed else "") if x)
                    fired += await _fire(s, c, a, f"theme:{two[0].id}", f"Theme membership changed ({two[0].period} r{two[0].revision}) - {msg}")
        elif a.kind == "sector_quadrant" and len(rots) == 2:
            new_q = (rots[0].payload["sectors"].get(p["sector"]) or {}).get("quadrant")
            old_q = (rots[1].payload["sectors"].get(p["sector"]) or {}).get("quadrant")
            if new_q and old_q and new_q != old_q:
                fired += await _fire(s, c, a, f"rrg:{rots[0].id}", f"{p['sector']} moved from {old_q} to {new_q} on the RRG (data as of {rots[0].data_as_of})")
        a.state = state
    await s.commit()
    return {"evaluated": len(alerts), "triggered": fired}


def to_dict(a: Alert, inst: Instrument | None = None) -> dict[str, Any]:
    return {"id": a.id, "kind": a.kind, "instrument_id": a.instrument_id, "symbol": inst.symbol if inst else None, "params": a.params,
            "status": a.status, "channel": a.channel, "created_at": a.created_at.isoformat(),
            "last_triggered_at": a.last_triggered_at.isoformat() if a.last_triggered_at else None}
