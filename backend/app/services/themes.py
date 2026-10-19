"""Themes: built-in seeding, custom themes, versions and immutable snapshots."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.container import Container
from app.db.models import Theme, ThemeSnapshot, ThemeSnapshotConstituent, ThemeVersion
from app.domain import scoring, theme_dsl
from app.domain.theme_dsl import ThemeSpec
from app.domain.themes_builtin import BUILTIN_THEMES
from app.services import metrics
from app.services.jobs import audit

MAX_EXCLUSIONS_STORED = 500


class ThemeError(ValueError):
    pass


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:70] or "theme"


async def seed_builtin(s: AsyncSession) -> int:
    existing = {t.slug: t for t in (await s.scalars(select(Theme).where(Theme.is_builtin.is_(True)))).all()}
    n = 0
    for b in BUILTIN_THEMES:
        spec = b.spec.model_dump(mode="json")
        t = existing.get(b.slug)
        if t is None:
            t = Theme(slug=b.slug, name=b.title, category=b.category, is_builtin=True, spec=spec, version=1, owner="system")
            s.add(t)
            await s.flush()
            s.add(ThemeVersion(theme_id=t.id, version=1, spec=spec, changed_by="system"))
            n += 1
        elif t.spec != spec:
            t.version += 1
            t.spec = spec
            s.add(ThemeVersion(theme_id=t.id, version=t.version, spec=spec, changed_by="system"))
        t.name, t.category, t.description, t.methodology = b.title, b.category, b.description, b.methodology
        t.requires, t.available, t.scoring_version = list(b.requires), b.available, scoring.VERSION
    await s.commit()
    return n


async def create_custom(s: AsyncSession, owner: str, spec: ThemeSpec, description: str = "", request_id: str | None = None) -> Theme:
    base = slugify(spec.name)
    slug, n = f"custom-{base}", 1
    while await s.scalar(select(Theme.id).where(Theme.slug == slug)):
        n += 1
        slug = f"custom-{base}-{n}"
    doc = spec.model_dump(mode="json")
    t = Theme(slug=slug, name=spec.name, category="Custom", description=description[:2000], methodology=" · ".join(spec.describe()),
              owner=owner, is_builtin=False, spec=doc, version=1, requires=["prices"], available=True, scoring_version=scoring.VERSION)
    s.add(t)
    await s.flush()
    s.add(ThemeVersion(theme_id=t.id, version=1, spec=doc, changed_by=owner))
    await audit(s, owner, "theme.create", "theme", t.id, {"slug": slug, "spec": doc}, request_id)
    await s.commit()
    return t


async def update_theme(s: AsyncSession, theme: Theme, actor: str, spec: ThemeSpec | None, description: str | None,
                       archived: bool | None, request_id: str | None = None) -> Theme:
    if theme.is_builtin:
        raise ThemeError("built-in themes cannot be edited; create a custom theme from its definition instead")
    changes: dict[str, Any] = {}
    if spec is not None and spec.model_dump(mode="json") != theme.spec:
        theme.version += 1
        theme.spec, theme.name, theme.methodology = spec.model_dump(mode="json"), spec.name, " · ".join(spec.describe())
        s.add(ThemeVersion(theme_id=theme.id, version=theme.version, spec=theme.spec, changed_by=actor))
        changes["version"] = theme.version
    if description is not None:
        theme.description = description[:2000]
        changes["description"] = True
    if archived is not None:
        theme.archived = archived
        changes["archived"] = archived
    await audit(s, actor, "theme.update", "theme", theme.id, changes, request_id)
    await s.commit()
    return theme


def period_for(c: Container, now: datetime | None = None) -> str:
    return (now or c.clock.now()).astimezone(ZoneInfo(c.settings.default_market_timezone)).strftime("%Y-%m")


async def refresh_theme(c: Container, s: AsyncSession, theme: Theme, trigger: str = "scheduled",
                        rows_cache: list[Any] | None = None) -> ThemeSnapshot:
    """Evaluate the theme on the latest metric snapshots and store a new immutable snapshot.

    Earlier snapshots are never modified. A second refresh within the same month is stored as the
    next revision of that month.
    """
    if not theme.available:
        raise ThemeError(f"theme requires data that is not configured: {', '.join(theme.requires)}")
    rows = rows_cache if rows_cache is not None else await metrics.latest_rows(s)
    if not rows:
        raise ThemeError("no metric snapshots available; run ingestion and metrics first")
    data = metrics.as_theme_rows(rows)
    sym = {str(i.id): i.symbol for _m, i in rows}
    ev = theme_dsl.evaluate(ThemeSpec.model_validate(theme.spec), data)
    period = period_for(c)
    rev = (await s.scalar(select(func.max(ThemeSnapshot.revision)).where(ThemeSnapshot.theme_id == theme.id, ThemeSnapshot.period == period))) or 0
    snap = ThemeSnapshot(
        theme_id=theme.id, period=period, revision=rev + 1, theme_version=theme.version, spec=theme.spec,
        metrics_as_of=max(m.as_of for m, _ in rows), universe_count=ev.universe_count, eligible_count=ev.eligible_count,
        exclusions=[{"symbol": sym.get(e.instrument_id, e.instrument_id), "reason": e.reason} for e in ev.exclusions[:MAX_EXCLUSIONS_STORED]],
        versions={**metrics.VERSIONS, "theme_dsl": ev.dsl_version}, trigger=trigger, is_demo=c.settings.is_demo, created_at=c.clock.now())
    for k in ev.constituents:
        px = data[k.instrument_id].get("close")
        snap.constituents.append(ThemeSnapshotConstituent(
            instrument_id=int(k.instrument_id), symbol=sym[k.instrument_id], rank=k.rank, score=Decimal(str(k.score)) if k.score is not None else None,
            values=k.values, price_at_snapshot=None if px is None else Decimal(str(round(px, 4)))))
    s.add(snap)
    await s.commit()
    return snap


async def refresh_all(c: Container, s: AsyncSession, trigger: str = "scheduled") -> dict[str, Any]:
    rows = await metrics.latest_rows(s)
    themes = (await s.scalars(select(Theme).where(Theme.archived.is_(False)))).all()
    done, skipped, failed = 0, [], []
    for t in themes:
        if not t.available:
            skipped.append({"slug": t.slug, "reason": f"requires {', '.join(t.requires)}"})
            continue
        try:
            await refresh_theme(c, s, t, trigger, rows)
            done += 1
        except (ThemeError, ValueError) as e:
            failed.append({"slug": t.slug, "error": str(e)[:200]})
    if not rows:
        raise ThemeError("no metric snapshots available; nothing was refreshed and earlier snapshots are unchanged")
    return {"period": period_for(c), "refreshed": done, "skipped_unavailable": skipped, "failed": failed}


async def current_snapshot(s: AsyncSession, theme_id: int, snapshot_id: int | None = None) -> ThemeSnapshot | None:
    q = select(ThemeSnapshot).options(selectinload(ThemeSnapshot.constituents)).where(ThemeSnapshot.theme_id == theme_id)
    q = q.where(ThemeSnapshot.id == snapshot_id) if snapshot_id else q.order_by(ThemeSnapshot.created_at.desc(), ThemeSnapshot.id.desc())
    return await s.scalar(q.limit(1))


def next_refresh(c: Container) -> str | None:
    try:
        trig = CronTrigger.from_crontab(c.settings.theme_refresh_cron, timezone=ZoneInfo(c.settings.default_market_timezone))
        nxt = trig.get_next_fire_time(None, c.clock.now())
        return nxt.isoformat() if nxt else None
    except ValueError:
        return None


def theme_dict(t: Theme, snap: ThemeSnapshot | None = None, c: Container | None = None) -> dict[str, Any]:
    spec = ThemeSpec.model_validate(t.spec)
    return {
        "id": t.id, "slug": t.slug, "name": t.name, "category": t.category, "description": t.description,
        "methodology": t.methodology, "interpreted_filters": spec.describe(), "owner": t.owner, "is_builtin": t.is_builtin,
        "available": t.available, "requires": t.requires, "version": t.version, "spec": t.spec, "archived": t.archived,
        "scoring_version": t.scoring_version, "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat(),
        "refresh_schedule": c.settings.theme_refresh_cron if c else None, "next_refresh": next_refresh(c) if c else None,
        "last_refreshed": snap.created_at.isoformat() if snap else None,
        "latest_snapshot": None if snap is None else {"id": snap.id, "period": snap.period, "revision": snap.revision,
                                                      "constituents": len(snap.constituents), "eligible_count": snap.eligible_count},
    }


def snapshot_dict(snap: ThemeSnapshot, live: dict[int, dict[str, Any]] | None = None, names: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    cons, moved = [], 0
    for k in snap.constituents:
        px0 = float(k.price_at_snapshot) if k.price_at_snapshot is not None else None
        px1 = (live or {}).get(k.instrument_id, {}).get("close")
        chg = None if not px0 or px1 is None else round((px1 / px0 - 1) * 100, 2)
        moved += 1 if chg else 0
        cons.append({"instrument_id": k.instrument_id, "symbol": k.symbol, **(names or {}).get(k.instrument_id, {}), "rank": k.rank,
                     "score": float(k.score) if k.score is not None else None, "values": k.values, "price_at_snapshot": px0,
                     "latest_close": px1, "change_since_snapshot_pct": chg})
    return {
        "id": snap.id, "theme_id": snap.theme_id, "period": snap.period, "revision": snap.revision, "theme_version": snap.theme_version,
        "created_at": snap.created_at.isoformat(), "metrics_as_of": snap.metrics_as_of.isoformat() if snap.metrics_as_of else None,
        "universe_count": snap.universe_count, "eligible_count": snap.eligible_count, "trigger": snap.trigger,
        "versions": snap.versions, "spec": snap.spec, "is_demo": snap.is_demo, "constituents": cons, "exclusions": snap.exclusions,
        "prices_moved_since_snapshot": moved > 0, "currency": "INR",
    }
