"""Equal-weight research score.

Method (version `score-v1`)
1. Each metric is turned into a 0-100 mid-rank percentile inside its peer set at the as-of date,
   oriented so that higher is more favourable.
2. A pillar score is the plain mean of its available metric percentiles. Missing or inapplicable
   metrics leave the denominator. A pillar needs at least `MIN_PILLAR_COVERAGE` of its metrics.
3. The overall score is the mean of the five pillar scores, 20% each. If any pillar is
   insufficient the overall score is withheld and `complete` is False. A clearly labelled partial
   score over the available pillars is offered when at least `MIN_PILLARS_FOR_PARTIAL` exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

VERSION = "score-v1"

PILLARS = ("fundamentals", "valuation", "momentum", "risk", "context")
PILLAR_WEIGHTS: dict[str, float] = {p: 1 / len(PILLARS) for p in PILLARS}
PILLAR_LABELS = {
    "fundamentals": "Fundamentals and financial quality",
    "valuation": "Valuation",
    "momentum": "Price action and momentum",
    "risk": "Risk and volatility",
    "context": "Market / sector context",
}
MIN_PEERS = 5
MIN_PILLAR_COVERAGE = 0.5
MIN_PILLARS_FOR_PARTIAL = 3


@dataclass(frozen=True)
class MetricSpec:
    name: str
    higher_is_better: bool = True
    peer_relative: bool = False  # rank inside the sector peer group when it is large enough


DEFAULT_PROFILE: dict[str, list[MetricSpec]] = {
    "fundamentals": [
        MetricSpec("roe"), MetricSpec("roce"), MetricSpec("revenue_growth_yoy"), MetricSpec("pat_growth_yoy"),
        MetricSpec("ebitda_margin"), MetricSpec("debt_equity", False), MetricSpec("interest_coverage"),
        MetricSpec("cfo_to_pat"),
    ],
    "valuation": [
        MetricSpec("pe", False, True), MetricSpec("pb", False, True), MetricSpec("ev_ebitda", False, True),
        MetricSpec("fcf_yield", True, True), MetricSpec("dividend_yield", True, True),
    ],
    "momentum": [
        MetricSpec("return_3m"), MetricSpec("return_6m"), MetricSpec("return_12m"), MetricSpec("rel_strength_6m"),
        MetricSpec("ma_alignment"), MetricSpec("dist_from_52w_high_pct"),
    ],
    "risk": [
        MetricSpec("volatility_252d", False), MetricSpec("downside_volatility_252d", False),
        MetricSpec("max_drawdown_1y"), MetricSpec("beta", False),
    ],
    "context": [
        MetricSpec("sector_rs_ratio"), MetricSpec("sector_rs_momentum"), MetricSpec("rel_strength_vs_sector_3m"),
        MetricSpec("avg_traded_value"),
    ],
}


def percentile_ranks(values: Mapping[str, float | None], higher_is_better: bool = True,
                     min_sample: int = MIN_PEERS) -> dict[str, float | None]:
    """Mid-rank percentile (0-100): (count below + 0.5 * count equal) / n * 100."""
    present = {k: float(v) for k, v in values.items() if v is not None and v == v}
    if len(present) < min_sample:
        return dict.fromkeys(values)
    ordered = sorted(present.values())
    n = len(ordered)
    import bisect

    out: dict[str, float | None] = {}
    for k in values:
        if k not in present:
            out[k] = None
            continue
        v = present[k]
        below = bisect.bisect_left(ordered, v)
        equal = bisect.bisect_right(ordered, v) - below
        pct = (below + 0.5 * equal) / n * 100
        out[k] = pct if higher_is_better else 100 - pct
    return out


@dataclass
class MetricScore:
    name: str
    value: float | None
    score: float | None
    higher_is_better: bool
    peer_scope: str  # universe | sector | none


@dataclass
class PillarResult:
    pillar: str
    score: float | None
    sufficient: bool
    metrics_used: int
    metrics_total: int
    weight: float
    contribution: float | None
    metrics: list[MetricScore] = field(default_factory=list)


@dataclass
class ScoreResult:
    instrument_id: str
    overall: float | None
    partial_overall: float | None
    complete: bool
    pillars: dict[str, PillarResult]
    pillars_available: int
    completeness_pct: float
    missing_metrics: list[str]
    top_positive: list[dict[str, Any]]
    top_negative: list[dict[str, Any]]
    disclosure: str
    version: str = VERSION


def score_universe(
    rows: Mapping[str, Mapping[str, Any]],
    profile: Mapping[str, list[MetricSpec]] | None = None,
    peer_groups: Mapping[str, str | None] | None = None,
    inapplicable: Mapping[str, set[str]] | None = None,
) -> dict[str, ScoreResult]:
    """`inapplicable` maps an instrument to metrics that do not apply to it (for example leverage
    ratios for a bank). Those metrics are removed from that instrument's pillar denominator and do
    not count as missing data."""
    profile = profile or DEFAULT_PROFILE
    inapplicable = inapplicable or {}
    ids = sorted(rows)
    # metric -> id -> (percentile, scope)
    ranked: dict[str, dict[str, tuple[float | None, str]]] = {}
    for specs in profile.values():
        for spec in specs:
            vals = {i: _num(rows[i].get(spec.name)) for i in ids}
            uni = percentile_ranks(vals, spec.higher_is_better)
            res: dict[str, tuple[float | None, str]] = {i: (uni[i], "universe" if uni[i] is not None else "none") for i in ids}
            if spec.peer_relative and peer_groups:
                groups: dict[str, list[str]] = {}
                for i in ids:
                    g = peer_groups.get(i)
                    if g:
                        groups.setdefault(g, []).append(i)
                for members in groups.values():
                    sub = percentile_ranks({i: vals[i] for i in members}, spec.higher_is_better)
                    for i in members:
                        if sub[i] is not None:
                            res[i] = (sub[i], "sector")
            ranked[spec.name] = res

    out: dict[str, ScoreResult] = {}
    for i in ids:
        pillars: dict[str, PillarResult] = {}
        missing: list[str] = []
        all_scores: list[MetricScore] = []
        total = present = 0
        for pillar, specs in profile.items():
            details = []
            skip = inapplicable.get(i, set())
            for spec in specs:
                if spec.name in skip:
                    details.append(MetricScore(spec.name, None, None, spec.higher_is_better, "not_applicable"))
                    continue
                sc, scope = ranked[spec.name][i]
                details.append(MetricScore(spec.name, _num(rows[i].get(spec.name)), sc, spec.higher_is_better, scope))
            applicable = [d for d in details if d.peer_scope != "not_applicable"]
            used = [d.score for d in applicable if d.score is not None]
            total += len(applicable)
            present += len(used)
            missing += [d.name for d in applicable if d.score is None]
            sufficient = bool(applicable) and len(used) / len(applicable) >= MIN_PILLAR_COVERAGE and len(used) > 0
            score = sum(used) / len(used) if sufficient else None
            w = PILLAR_WEIGHTS.get(pillar, 1 / len(profile))
            pillars[pillar] = PillarResult(pillar, score, sufficient, len(used), len(applicable), w,
                                           None if score is None else score * w, details)
            all_scores += [d for d in details if d.score is not None]
        avail = [p for p in pillars.values() if p.sufficient]
        complete = len(avail) == len(pillars)
        overall = sum(p.contribution for p in avail) if complete else None  # type: ignore[misc]
        partial = None
        if not complete and len(avail) >= MIN_PILLARS_FOR_PARTIAL:
            partial = sum(p.score for p in avail) / len(avail)  # type: ignore[misc]
        lacking = [p.pillar for p in pillars.values() if not p.sufficient]
        if complete:
            disclosure = "All five pillars have sufficient coverage; each contributes 20%."
        elif partial is not None:
            disclosure = (f"Incomplete: insufficient coverage for {', '.join(lacking)}. The partial score is the equal-weight "
                          f"mean of the {len(avail)} available pillars and is not comparable with a complete score.")
        else:
            disclosure = f"Insufficient data: no score. Pillars lacking coverage: {', '.join(lacking)}."
        ordered = sorted(all_scores, key=lambda d: (-(d.score or 0), d.name))
        as_dict = lambda d: {"metric": d.name, "value": d.value, "score": round(d.score, 1), "peer_scope": d.peer_scope}  # noqa: E731
        out[i] = ScoreResult(
            instrument_id=i, overall=overall, partial_overall=partial, complete=complete, pillars=pillars,
            pillars_available=len(avail), completeness_pct=(present / total * 100) if total else 0.0,
            missing_metrics=missing, top_positive=[as_dict(d) for d in ordered[:3]],
            top_negative=[as_dict(d) for d in sorted(ordered, key=lambda d: (d.score or 0, d.name))[:3]],
            disclosure=disclosure,
        )
    return out


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


CONFIDENCE_RULE = ("High: data completeness >= 85% and newest price bar <= 3 days old. Medium: completeness >= 60% and "
                   "<= 14 days old. Low otherwise, or when the data age is unknown.")


def confidence(completeness_pct: float, data_age_days: float | None) -> str:
    if data_age_days is None:
        return "Low"
    if completeness_pct >= 85 and data_age_days <= 3:
        return "High"
    if completeness_pct >= 60 and data_age_days <= 14:
        return "Medium"
    return "Low"


STANCE_RULE = "Favorable: score >= 65. Mixed: 45 to below 65. Unfavorable: below 45. Insufficient data: no score."


def stance(result: ScoreResult) -> str:
    v = result.overall if result.complete else result.partial_overall
    if v is None:
        return "Insufficient data"
    return "Favorable" if v >= 65 else "Mixed" if v >= 45 else "Unfavorable"


def percentile_band(score: float | None) -> str | None:
    if score is None:
        return None
    for cut, label in ((80, "Top quintile"), (60, "Upper-middle"), (40, "Middle"), (20, "Lower-middle")):
        if score >= cut:
            return label
    return "Bottom quintile"
