"""AI refinement of a theme: a plain-language request narrows a theme's members.

The model only proposes extra filters, sectors and a ranking in the theme DSL; the proposal is
validated against the metric catalog and evaluated deterministically on the latest metric
snapshots. Without a model, the rules interpreter proposes the filters. Nothing is saved: the
theme's monthly snapshots are untouched.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.llm import LLMError
from app.core.container import Container
from app.db.models import Theme
from app.domain import theme_dsl
from app.domain.theme_dsl import METRIC_CATALOG, Filter, RankKey, ThemeSpec
from app.services import metrics

VERSION = "theme-ai-v1"


class RefineFilter(BaseModel):
    metric: str = Field(max_length=60)
    op: Literal["gt", "gte", "lt", "lte", "between"]
    value: float
    value2: float | None = None


class RefinePlan(BaseModel):
    filters: list[RefineFilter] = Field(default_factory=list, max_length=8)
    sectors: list[str] = Field(default_factory=list, max_length=10)
    rank_by: str | None = Field(default=None, max_length=60)
    rank_direction: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=25, ge=1, le=100)
    explanation: str = Field(default="", max_length=400)


SYSTEM = """You refine an equity theme screen for NSE stocks. Translate the user's request into extra filters
in the theme DSL. Use only metric keys from the catalog and sectors from the sector list; percent metrics are in
percent units. Do not repeat the theme's existing filters unless tightening them. Never invent symbols or data.
explanation: one sentence describing what the filters do. Return only the JSON object."""


class RefineError(ValueError):
    pass


def rules_plan(query: str) -> RefinePlan:
    it = theme_dsl.interpret(query)
    fs = [RefineFilter(metric=f.metric, op=f.op if f.op != "eq" else "gte", value=f.value, value2=f.value2)  # type: ignore[arg-type]
          for f in it.spec.filters]
    sectors = list(it.spec.universe.sectors or [])
    notes = [x for x in it.ambiguities if not (sectors and x.startswith("No recognised screening condition"))]
    parts = ([f"Restricted to {', '.join(sectors)}"] if sectors else []) + notes[:3]
    return RefinePlan(filters=fs, sectors=sectors, explanation="; ".join(parts) or "Matched phrases: " + ", ".join(it.matched_phrases))


async def refine(c: Container, s: AsyncSession, theme: Theme, query: str) -> dict[str, Any]:
    rows = await metrics.latest_rows(s)
    if not rows:
        raise RefineError("No computed metrics are stored yet. Refresh the data first.")
    base = ThemeSpec.model_validate(theme.spec)
    sectors_known = sorted({i.sector for _m, i in rows if i.sector})
    source, reason = "rules", None
    plan: RefinePlan | None = None
    if c.llm.available:
        user = json.dumps({"request": query, "theme": {"name": base.name, "filters": base.describe()},
                           "sectors": sectors_known,
                           "metric_catalog": {k: f"{v['label']} ({v['unit']})" for k, v in METRIC_CATALOG.items()}})
        try:
            plan, source = await c.llm.complete_validated(SYSTEM, user, RefinePlan), "llm"
        except LLMError as e:
            reason = str(e)[:300]
    else:
        reason = c.llm.status().get("reason") or "language model not configured"
    if plan is None:
        plan = rules_plan(query)

    notes: list[str] = []
    extra: list[Filter] = []
    for f in plan.filters:
        try:
            extra.append(Filter(metric=f.metric, op=f.op, value=f.value, value2=f.value2))
        except ValueError:
            notes.append(f"Ignored a filter on '{f.metric[:40]}': not a supported metric or range.")
    secs = [x for x in plan.sectors if x in sectors_known]
    notes += [f"Ignored sector '{x[:40]}': not in the tracked universe." for x in plan.sectors if x not in sectors_known]
    if base.universe.sectors:
        secs = [x for x in secs if x in base.universe.sectors] if secs else list(base.universe.sectors)
    rank = base.rank_by
    if plan.rank_by:
        if plan.rank_by in METRIC_CATALOG:
            rank = [RankKey(metric=plan.rank_by, direction=plan.rank_direction)]
        else:
            notes.append(f"Ignored ranking by '{plan.rank_by[:40]}': not a supported metric.")
    if not extra and not secs and rank is base.rank_by:
        notes.append("No usable condition was found in the request; showing the theme unchanged.")

    merged = {f.metric: f for f in base.filters}
    merged.update({f.metric: f for f in extra})
    spec = base.model_copy(update={"filters": list(merged.values())[:25], "rank_by": rank, "limit": plan.limit,
                                   "universe": base.universe.model_copy(update={"sectors": secs or None})})
    ev = theme_dsl.evaluate(spec, metrics.as_theme_rows(rows))
    by_id = {str(i.id): (m, i) for m, i in rows}
    members = []
    for k in ev.constituents:
        m, i = by_id[k.instrument_id]
        members.append({"instrument_id": i.id, "symbol": i.symbol, "name": i.name, "sector": i.sector, "rank": k.rank,
                        "score": k.score, "values": k.values, "close": m.flat.get("close"),
                        "stance": (m.score or {}).get("stance"), "risk_label": ((m.score or {}).get("risk") or {}).get("label")})
    reasons: dict[str, int] = {}
    for ex in ev.exclusions:
        key = ex.reason.split(" (value")[0]
        reasons[key] = reasons.get(key, 0) + 1
    as_of = max(m.as_of for m, _ in rows)
    return {
        "query": query, "version": VERSION, "source": source, "llm_reason_not_used": reason if source != "llm" else None,
        "model_id": c.llm.model_id, "explanation": plan.explanation,
        "added_filters": [f.describe() for f in extra], "sectors": secs, "interpreted_filters": spec.describe(), "notes": notes,
        "spec": spec.model_dump(mode="json"), "members": members, "eligible_count": ev.eligible_count,
        "universe_count": ev.universe_count, "top_exclusion_reasons": sorted(reasons.items(), key=lambda t: -t[1])[:8],
        "metrics_as_of": as_of.isoformat(), "saved": False,
        "note": "A filtered view of the latest data. The theme's monthly snapshots are not changed.",
    }
