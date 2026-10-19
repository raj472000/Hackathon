"""Bulk deterministic metrics, scores and labels for the whole universe.

One pass over stored bars and statements produces a `MetricSnapshot` per instrument. Nothing here
calls a language model. Each snapshot records the formula versions that produced it.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.db.models import FinancialPeriod, Instrument, MetricSnapshot
from app.domain import adjust, entry_setup, reconcile, risk, rrg, scoring, technicals, valuation
from app.domain import financial_metrics as fm
from app.domain.result import MetricValue, Status, bad, ok
from app.domain.scoring import percentile_ranks
from app.domain.theme_dsl import METRIC_CATALOG
from app.services import sector_rotation
from app.services.ingestion import load_bars

VERSIONS = {"fundamentals": fm.VERSION, "valuation": valuation.VERSION, "technicals": technicals.VERSION, "risk": risk.VERSION,
            "score": scoring.VERSION, "entry": entry_setup.VERSION, "rrg": rrg.VERSION, "adjust": adjust.VERSION,
            "reconcile": reconcile.VERSION}
FRESH_DAYS = 4

_FUND_NAMES = [n for n, m in METRIC_CATALOG.items() if m["group"] == "fundamentals"]
_VAL_NAMES = ["market_cap", "enterprise_value", "pe", "peg", "pb", "ps", "net_debt", "ev_ebitda", "ev_ebit", "ev_sales",
              "price_to_fcf", "fcf_yield", "earnings_yield", "dividend_yield"]


def _missing_bundle(names: list[str], reason: str) -> dict[str, MetricValue]:
    return {n: bad(n, Status.MISSING, reason, METRIC_CATALOG.get(n, {}).get("unit", "ratio"), "") for n in names}


def fundamentals_for(periods: list[dict[str, Any]], sector_kind: str, price: float | None) -> tuple[dict[str, MetricValue], dict[str, Any]]:
    """Fundamental and valuation bundles from annual periods plus the latest price."""
    annual = sorted((p for p in periods if p.get("period_type", "annual") == "annual"), key=lambda p: str(p["period_end"]))
    if not annual:
        reason = "no financial statements ingested for this instrument"
        return {**_missing_bundle(_FUND_NAMES, reason), **_missing_bundle(_VAL_NAMES, reason)}, {"periods": 0}
    out = {**fm.compute_fundamentals(annual, sector_kind), **fm.compute_consistency(annual)}
    cur = annual[-1]
    shares = cur.get("shares_outstanding")
    eps = cur.get("eps")
    if eps is None and cur.get("pat") is not None and shares:
        eps = cur["pat"] / shares
    dps = cur.get("dividend_per_share")
    if dps is None and cur.get("dividends_paid") is not None and shares:
        dps = abs(cur["dividends_paid"]) / shares
    basis = f"FY ending {cur['period_end']} (latest annual)"
    quarters = [(q["period_end"], q.get("eps")) for q in periods if q.get("period_type") == "quarterly"]
    ttm_eps = fm.ttm("eps_ttm", quarters) if quarters else None
    if ttm_eps is not None and ttm_eps.status == Status.OK and str(sorted(quarters)[-1][0]) > str(cur["period_end"]):
        # Trailing twelve months is more recent than the last annual report: use it for earnings-based ratios.
        eps = ttm_eps.value
        latest_q = sorted(quarters)[-1][0]
        basis = f"TTM to {latest_q} (sum of the latest four quarterly EPS as filed)"
        shares = next((q.get("shares_outstanding") for q in sorted(periods, key=lambda q: str(q["period_end"]), reverse=True)
                       if q.get("period_type") == "quarterly" and q.get("shares_outstanding")), shares)
    out.update(valuation.compute_valuation(
        price=price, shares_outstanding=shares, eps_ttm=eps, book_value_per_share=out["book_value_per_share"].value,
        revenue=cur.get("revenue"), ebitda=cur.get("ebitda"), ebit=cur.get("ebit"), fcf=out["fcf"].value if "fcf" in out else None,
        total_debt=cur.get("total_debt"), cash=cur.get("cash"), minority_interest=cur.get("minority_interest"),
        dividend_per_share=dps, eps_growth_pct=out["eps_growth_yoy"].value, growth_basis="EPS growth YoY, latest annual",
        earnings_basis=basis, sector_kind=sector_kind))
    return out, {"periods": len(annual), "latest_period_end": str(cur["period_end"]), "earnings_basis": basis}


async def compute_all(c: Container, s: AsyncSession) -> dict[str, Any]:
    insts = list((await s.scalars(select(Instrument))).all())
    bars = await load_bars(s)
    if not bars:
        return {"status": "skipped", "reason": "no price bars ingested", "instruments": 0}
    today = c.clock.now().date()
    series = sector_rotation.build_series(insts, bars, c.benchmark_symbol)
    bench: pd.Series | None = series["benchmark"]
    as_of: date = (bench.index[-1] if bench is not None and len(bench) else max(b.index[-1] for b in bars.values())).date()
    try:
        rot = sector_rotation.compute(series, c.benchmark_symbol, today=today)["sectors"]
    except rrg.RRGError:
        rot = {}

    periods: dict[int, list[dict[str, Any]]] = {}
    meta_by: dict[int, dict[str, Any]] = {}
    # One statements source per instrument, so periods from different sources are never mixed:
    # NSE filings first (primary source), then uploaded files.
    by_source: dict[int, dict[str, list[FinancialPeriod]]] = {}
    for p in (await s.scalars(select(FinancialPeriod))).all():
        by_source.setdefault(p.instrument_id, {}).setdefault(p.source, []).append(p)
    for iid, groups in by_source.items():
        src = sorted(groups, key=lambda k: (not k.startswith("NSE filings"), k))[0]
        rows = groups[src]
        periods[iid] = [{**p.facts, "period_end": p.period_end.isoformat(), "period_type": p.period_type} for p in rows]
        last = max(rows, key=lambda p: p.period_end)
        meta_by[iid] = {"source": src, "currency": last.currency, "unit": last.unit, "ingested_at": last.ingested_at.isoformat(),
                        "is_demo": last.is_demo, "source_url": last.source_url, "other_sources": sorted(k for k in groups if k != src)}

    equities = [i for i in insts if i.instrument_type != "INDEX"]
    bundles: dict[int, dict[str, MetricValue]] = {}
    extras: dict[int, dict[str, Any]] = {}
    for inst in equities:
        df = bars.get(inst.id)
        if df is None or df.empty:
            continue
        df = df.loc[: pd.Timestamp(as_of)]
        if df.empty:
            continue
        m: dict[str, MetricValue] = {}
        m.update(technicals.compute_technicals(df, bench))
        m.update(risk.compute_risk(df, bench))
        last_close = float(df["close"].iloc[-1])
        m["close"] = ok("close", last_close, "price", "last adjusted close", {"date": df.index[-1].date().isoformat()})
        bo = technicals.breakout_status(df)
        m["breakout_signal"] = (
            bad("breakout_signal", Status.INSUFFICIENT_HISTORY, "fewer than 56 bars", "count", "close vs prior 55-bar range")
            if bo == "insufficient_history"
            else ok("breakout_signal", {"breakout": 1.0, "breakdown": -1.0, "none": 0.0}[bo], "count", "close vs prior 55-bar range", {"status": bo}))
        fb, fmeta = fundamentals_for(periods.get(inst.id, []), inst.sector_kind, last_close)
        m.update(fb)
        sec = rot.get(inst.sector or "")
        sec_series = series["sectors"].get(inst.sector or "", {}).get("series")
        if sec and sec.get("status") == "ok" and sec["trail"]:
            pt = sec["trail"][-1]
            m["sector_rs_ratio"] = ok("sector_rs_ratio", pt["rs_ratio"], "score", "sector RS-Ratio (rrg-v1)", {"sector": inst.sector, "date": pt["date"]})
            m["sector_rs_momentum"] = ok("sector_rs_momentum", pt["rs_momentum"], "score", "sector RS-Momentum (rrg-v1)", {"sector": inst.sector})
        else:
            why = (sec or {}).get("reason") or "sector series unavailable"
            m["sector_rs_ratio"] = bad("sector_rs_ratio", Status.MISSING, why, "score", "sector RS-Ratio (rrg-v1)")
            m["sector_rs_momentum"] = bad("sector_rs_momentum", Status.MISSING, why, "score", "sector RS-Momentum (rrg-v1)")
        if sec_series is not None:
            m["sector_return_3m"] = technicals.period_return(sec_series.loc[: pd.Timestamp(as_of)], "3M", "sector_return_3m")
            m["rel_strength_vs_sector_3m"] = technicals.relative_strength(df["close"], sec_series, "3M", "rel_strength_vs_sector_3m")
        else:
            for n in ("sector_return_3m", "rel_strength_vs_sector_3m"):
                m[n] = bad(n, Status.MISSING, "sector series unavailable", "pct", "")
        m["pe_history_percentile"] = bad("pe_history_percentile", Status.INSUFFICIENT_HISTORY,
                                         "needs point-in-time earnings history, which is not ingested", "pct",
                                         "percentile of current P/E within its own history")
        bundles[inst.id] = m
        providers = df["provider"].value_counts().to_dict() if "provider" in df else {}
        extras[inst.id] = {"last_bar": df.index[-1].date().isoformat(), "bars": len(df), "data_age_days": (today - df.index[-1].date()).days,
                           "bar_providers": providers, "fundamentals": {**fmeta, **meta_by.get(inst.id, {})}}

    ids = {i.id: i for i in equities if i.id in bundles}
    flat: dict[str, dict[str, Any]] = {str(i): {k: v.value for k, v in bundles[i].items()} for i in ids}
    # P/E position inside the sector (0 = cheapest). Needs at least MIN_PEERS peers with a valid P/E.
    for sector in {i.sector for i in ids.values()}:
        members = [str(i.id) for i in ids.values() if i.sector == sector]
        ranks = percentile_ranks({k: flat[k].get("pe") for k in members})
        for k in members:
            flat[k]["pe_sector_percentile"] = ranks[k]
            bundles[int(k)]["pe_sector_percentile"] = (
                ok("pe_sector_percentile", ranks[k], "pct", "mid-rank percentile of P/E among sector peers", {"sector": sector, "peers": len(members)})  # type: ignore[arg-type]
                if ranks[k] is not None
                else bad("pe_sector_percentile", Status.MISSING if flat[k].get("pe") is None else Status.INSUFFICIENT_HISTORY,
                         "P/E unavailable" if flat[k].get("pe") is None else f"fewer than {scoring.MIN_PEERS} sector peers with a valid P/E",
                         "pct", "mid-rank percentile of P/E among sector peers"))
    not_applicable = {str(i): {k for k, v in bundles[i].items() if v.status == Status.NOT_APPLICABLE} for i in ids}
    scores = scoring.score_universe(flat, peer_groups={str(i.id): i.sector for i in ids.values()}, inapplicable=not_applicable)

    await s.execute(update(MetricSnapshot).where(MetricSnapshot.is_latest.is_(True)).values(is_latest=False))
    existing = {r.instrument_id: r for r in (await s.scalars(select(MetricSnapshot).where(MetricSnapshot.as_of == as_of))).all()}
    now = c.clock.now()
    for iid, inst in ids.items():
        key = str(iid)
        sr = scores[key]
        row_flat = flat[key]
        ex = extras[iid]
        rl = risk.risk_label(row_flat.get("volatility_252d"), row_flat.get("max_drawdown_1y"), row_flat.get("beta"))
        pillar = {p: (None if r.score is None else round(r.score, 1)) for p, r in sr.pillars.items()}
        for p, v in pillar.items():  # type: ignore[assignment]
            row_flat[f"{p}_score"] = v
        row_flat["overall_score"] = None if sr.overall is None else round(sr.overall, 1)
        row_flat["completeness_pct"] = round(sr.completeness_pct, 1)
        conf = scoring.confidence(sr.completeness_pct, ex["data_age_days"])
        setup = entry_setup.evaluate({**row_flat, "risk_label": rl.label, "data_age_days": ex["data_age_days"]})
        shown = sr.overall if sr.complete else sr.partial_overall
        score_doc = {
            "overall": None if sr.overall is None else round(sr.overall, 1),
            "partial_overall": None if sr.partial_overall is None else round(sr.partial_overall, 1),
            "complete": sr.complete, "disclosure": sr.disclosure, "pillars_available": sr.pillars_available,
            "band": scoring.percentile_band(shown), "stance": scoring.stance(sr), "stance_rule": scoring.STANCE_RULE,
            "completeness_pct": round(sr.completeness_pct, 1), "missing_metrics": sr.missing_metrics,
            "confidence": conf, "confidence_rule": scoring.CONFIDENCE_RULE,
            "risk": {"label": rl.label, "criteria": rl.criteria, "drivers": rl.drivers},
            "pillars": {p: {"label": scoring.PILLAR_LABELS[p], "score": pillar[p], "sufficient": r.sufficient, "weight": r.weight,
                            "contribution": None if r.contribution is None else round(r.contribution, 2),
                            "metrics_used": r.metrics_used, "metrics_total": r.metrics_total,
                            "metrics": [{"name": d.name, "label": METRIC_CATALOG.get(d.name, {}).get("label", d.name), "value": d.value,
                                         "score": None if d.score is None else round(d.score, 1),
                                         "higher_is_better": d.higher_is_better, "peer_scope": d.peer_scope} for d in r.metrics]}
                        for p, r in sr.pillars.items()},
            "top_positive": sr.top_positive, "top_negative": sr.top_negative,
            "entry_setup": asdict(setup), "freshness": "fresh" if ex["data_age_days"] <= FRESH_DAYS else "stale",
            "peer_universe": {"size": len(ids), "sector": inst.sector, "exchange": "NSE"}, **ex,
        }
        row = existing.get(iid)
        if row is None:
            row = MetricSnapshot(instrument_id=iid, as_of=as_of, flat={}, detail={}, score={}, versions={})
            s.add(row)
        row.flat = row_flat
        row.detail = {k: {kk: vv for kk, vv in v.to_dict().items() if kk != "value"} for k, v in bundles[iid].items()}
        row.score, row.versions = score_doc, VERSIONS
        row.overall_score = None if sr.overall is None else round(sr.overall, 2)  # type: ignore[assignment]
        row.is_latest, row.is_demo, row.computed_at = True, c.settings.is_demo, now
    await s.commit()
    complete = sum(1 for r in scores.values() if r.complete)
    from app.observability import metrics as om

    om.STALE_INSTRUMENTS.set(sum(1 for e in extras.values() if e["data_age_days"] > FRESH_DAYS))
    om.DATA_COVERAGE.set(complete / len(ids) if ids else 0)
    return {"status": "ok", "as_of": as_of.isoformat(), "instruments": len(ids), "complete_scores": complete,
            "incomplete_scores": len(ids) - complete, "versions": VERSIONS}


async def latest_rows(s: AsyncSession) -> list[tuple[MetricSnapshot, Instrument]]:
    q = select(MetricSnapshot, Instrument).join(Instrument, Instrument.id == MetricSnapshot.instrument_id).where(MetricSnapshot.is_latest.is_(True))
    return [(m, i) for m, i in (await s.execute(q)).all()]


def as_theme_rows(rows: list[tuple[MetricSnapshot, Instrument]]) -> dict[str, dict[str, Any]]:
    return {str(i.id): {**m.flat, "instrument_type": i.instrument_type, "sector": i.sector, "sector_kind": i.sector_kind,
                        "symbol": i.symbol, "governance_flag": i.governance_flag} for m, i in rows}
