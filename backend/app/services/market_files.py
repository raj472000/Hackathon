"""Whole-market universe and daily prices from the files NSE publishes.

- Universe: NSE's list of listed equities (`NSE_EQUITY_LIST_URL`) for the series in `UNIVERSE_SERIES`,
  classified with NSE's industry from the Nifty Total Market constituents file (`NSE_INDUSTRY_LIST_URL`).
  Stocks outside that file have no NSE classification and are left unclassified rather than guessed.
  Rows in the curated reference file (indices, sector-index mapping, hand-checked sectors) take precedence.
- Prices: NSE's daily bhavcopy (`NSE_BHAVCOPY_URL_TEMPLATE`), one file per trading day for every security,
  so the whole market costs one request per day instead of one per stock. Bhavcopy prices are unadjusted;
  splits and bonus issues from NSE's market-wide corporate-actions list are back-adjusted with the same
  `domain.adjust` rules used for the per-symbol path. Downloaded files are cached, so a re-run reads disk.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import nse_filings as nf
from app.connectors.base import ProviderUnavailable
from app.core.container import Container
from app.db.models import CorporateActionRow, DataSourceRecord, Instrument, PriceBar
from app.domain import adjust

PROVIDER = "nse_bhavcopy_file"
# NSE industry names -> the sector names used across the app (and by the sector-index mapping).
SECTOR_ALIASES = {"Automobile and Auto Components": "Automobile", "Fast Moving Consumer Goods": "FMCG",
                  "Oil Gas & Consumable Fuels": "Oil & Gas", "Telecommunication": "Telecom"}
FINANCIAL_SECTORS = {"Financial Services"}
INSERT_CHUNK = 5000
LABEL_MAJORITY = 0.6  # an NSE industry label maps to a sector only when this share of classified stocks agree
LABELS_FILE = "industry_labels.json"
LABEL_MAX_PAUSES = 5
LABEL_RECHECK_DAYS = 30  # a company NSE has no industry label for is asked again after this many days


def configured(c: Container) -> str | None:
    """None when the whole-market path can run, else the reason it cannot."""
    s = c.settings
    if s.is_demo:
        return "demo mode uses the synthetic universe"
    if s.universe_scope != "all":
        return "UNIVERSE_SCOPE is not 'all'"
    missing = [n for n, v in (("NSE_EQUITY_LIST_URL", s.nse_equity_list_url), ("NSE_BHAVCOPY_URL_TEMPLATE", s.nse_bhavcopy_url_template)) if not v]
    return f"{', '.join(missing)} not set" if missing else None


# ------------------------------------------------------------------ universe
async def universe_all(c: Container, reference: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    s = c.settings
    series = {x.strip().upper() for x in s.universe_series.split(",") if x.strip()}
    listed = await nf.equity_list(c.filings, s.nse_equity_list_url)  # type: ignore[arg-type]
    industries: dict[str, dict[str, str]] = {}
    note = None
    if s.nse_industry_list_url:
        try:
            industries = await nf.industry_list(c.filings, s.nse_industry_list_url)
        except ProviderUnavailable as e:
            note = f"industry classification unavailable: {e}"
    ref_by = {str(r["symbol"]).upper(): r for r in reference}
    index_for = {r["sector"]: r["sector_index_symbol"] for r in reference
                 if r.get("instrument_type", "EQ") == "EQ" and r.get("sector") and r.get("sector_index_symbol")}
    labels = load_labels(c)
    rows = [r for r in reference if (r.get("instrument_type") or "EQ") == "INDEX"]
    seen: set[str] = set()
    for row in listed:
        sym = row.get("SYMBOL", "").upper()
        if not sym or row.get("SERIES", "").upper() not in series or sym in seen:
            continue
        seen.add(sym)
        if sym in ref_by:
            rows.append({**ref_by[sym], "isin": ref_by[sym].get("isin") or row.get("ISIN NUMBER")})
            continue
        ind = industries.get(sym, {}).get("Industry")
        sector = SECTOR_ALIASES.get(ind, ind) if ind else None
        rows.append({"symbol": sym, "name": row.get("NAME OF COMPANY"), "sector": sector, "industry": ind,
                     "sector_kind": "financial" if sector in FINANCIAL_SECTORS else "industrial", "instrument_type": "EQ",
                     "sector_index_symbol": index_for.get(sector or ""), "isin": row.get("ISIN NUMBER") or None,
                     "classification_source": "NSE Nifty Total Market constituents" if ind else "not classified: outside NSE's index files"})
    # Curated equities NSE no longer lists (renamed, merged, delisted) stay so their history remains visible.
    rows += [r for sym, r in ref_by.items() if (r.get("instrument_type") or "EQ") == "EQ" and sym not in seen]
    eq = [r for r in rows if r.get("instrument_type") == "EQ"]
    derived = classify_by_label(eq, labels, index_for)
    return rows, {"listed": len(listed), "equities": len(eq), "classified": sum(1 for r in eq if r.get("sector")),
                  "classified_by_label": derived, "industry_labels": len(labels), "series": sorted(series), "note": note}


def label_sectors(eq: list[dict[str, Any]], labels: dict[str, str]) -> dict[str, tuple[str, int, int]]:
    """NSE industry label -> (sector, agreeing stocks, stocks with the label), learned from stocks NSE classifies.
    Only labels where at least LABEL_MAJORITY of those stocks share one sector are mapped."""
    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in eq:
        lab = labels.get(str(r["symbol"]).upper())
        if lab and r.get("sector"):
            votes[lab][r["sector"]] += 1
    out = {}
    for lab, v in votes.items():
        sector, n = max(v.items(), key=lambda kv: kv[1])
        total = sum(v.values())
        if n / total >= LABEL_MAJORITY:
            out[lab] = (sector, n, total)
    return out


def classify_by_label(eq: list[dict[str, Any]], labels: dict[str, str], index_for: dict[str, str]) -> int:
    """Gives stocks outside NSE's index files the sector that NSE-classified stocks with the same NSE industry
    label belong to. Every stock's NSE label is kept as its industry."""
    mapping = label_sectors(eq, labels)
    n = 0
    for r in eq:
        lab = labels.get(str(r["symbol"]).upper())
        if lab:
            r["industry"] = lab
        if r.get("sector") or not lab or lab not in mapping:
            continue
        sector, agree, total = mapping[lab]
        r["sector"], r["sector_index_symbol"] = sector, index_for.get(sector)
        r["sector_kind"] = "financial" if sector in FINANCIAL_SECTORS else "industrial"
        r["classification_source"] = f"NSE industry {lab[:32]}: {agree} of {total} classified peers"
        n += 1
    return n


def _labels_path(c: Container) -> Any:
    return c.settings.nse_filings_cache_dir / LABELS_FILE


def _read_json(p: Any) -> dict[str, str]:
    import json

    try:
        return dict(json.loads(p.read_text())) if p.exists() else {}
    except (ValueError, OSError):
        return {}


def load_labels(c: Container) -> dict[str, str]:
    return _read_json(_labels_path(c))


def _no_label_path(c: Container) -> Any:
    return c.settings.nse_filings_cache_dir / "industry_labels_none.json"


async def refresh_industry_labels(c: Container, symbols: list[str], budget: int = 3000) -> dict[str, Any]:
    """Fetches NSE's industry label for companies that do not have one yet (bounded per run, with pauses
    when NSE throttles). Labels are kept in a small JSON file next to the other NSE caches."""
    import json

    if configured(c) or not c.filings.available:
        return {"status": "skipped", "reason": configured(c) or c.filings.status()["reason"]}
    labels = load_labels(c)
    today = c.clock.now().date()
    none = _read_json(_no_label_path(c))  # symbol -> date NSE last had no label for it
    recent = {x for x, d in none.items() if (today - date.fromisoformat(d)).days < LABEL_RECHECK_DAYS}
    todo = [x for x in symbols if x not in labels and x not in recent][:budget]
    got = missing = pauses = 0
    i = 0
    while i < len(todo):
        try:
            lab = await nf.company_industry(c.filings, todo[i], today)
        except ProviderUnavailable:
            if c.filings.status()["breaker"] == "open" and pauses < LABEL_MAX_PAUSES:
                pauses += 1
                await c.clock.sleep(c.filings.breaker_reset_seconds + 1)
                continue
            if c.filings.status()["breaker"] == "open":
                break
            i += 1
            continue
        if lab:
            labels[todo[i]] = lab
            got += 1
        else:
            none[todo[i]] = today.isoformat()
            missing += 1
        i += 1
        if got and got % 100 == 0:
            _labels_path(c).write_text(json.dumps(labels))
    _labels_path(c).parent.mkdir(parents=True, exist_ok=True)
    _labels_path(c).write_text(json.dumps(labels))
    _no_label_path(c).write_text(json.dumps(none))
    return {"status": "ok", "fetched": got, "no_label": missing, "skipped_recently_checked": len(recent), "pauses": pauses,
            "remaining": len(symbols) - len(labels)}


# ------------------------------------------------------------------ prices
def _f(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "", "-") else None
    except ValueError:
        return None


def _dec(v: float | None, nd: int = 4) -> Decimal | None:
    return None if v is None or v != v else Decimal(str(round(v, nd)))


async def _actions(c: Container, start: date, end: date) -> tuple[dict[str, list[adjust.CorporateAction]], str | None]:
    try:
        raw = await nf.market_corporate_actions(c.filings, start, end)
    except ProviderUnavailable as e:
        return {}, f"corporate actions unavailable, prices of stocks with a split or bonus may show a gap: {e}"
    out: dict[str, list[adjust.CorporateAction]] = defaultdict(list)
    for r in raw:
        parsed = adjust.parse_action(str(r.get("subject") or ""))
        ex = nf._parse_nse_date(r.get("exDate"))
        if parsed and ex and parsed[0] in ("split", "bonus"):
            out[str(r.get("symbol", "")).upper()].append(adjust.CorporateAction(ex, parsed[0], parsed[1], str(r.get("subject"))[:300]))
    return out, None


async def ingest_bhavcopy(c: Container, s: AsyncSession, progress: Any = None) -> dict[str, Any]:
    """Load daily bars for every equity in the universe from NSE's bhavcopy files.

    New instruments get the full history window; others get the days after their last stored bar. An
    instrument with a split or bonus not yet recorded is rebuilt in full, so all its history stays adjusted."""
    why = configured(c)
    if why:
        return {"status": "skipped", "reason": why}
    st = c.settings
    today = c.clock.now().date()
    start = today - timedelta(days=int(365.25 * st.history_years) + 21)
    insts = {i.symbol: i for i in (await s.scalars(select(Instrument).where(Instrument.instrument_type == "EQ",
                                                                            Instrument.trading_status == "active"))).all()}
    from_files = set((await s.scalars(select(PriceBar.instrument_id).where(PriceBar.provider == PROVIDER).distinct())).all())
    last = dict((await s.execute(select(PriceBar.instrument_id, func.max(PriceBar.date)).group_by(PriceBar.instrument_id))).all())
    actions, act_note = await _actions(c, start, today)
    known = {(r.instrument_id, r.ex_date, r.kind) for r in (await s.scalars(select(CorporateActionRow))).all()}
    rebuild: set[str] = set()
    need_from: dict[str, date] = {}
    for sym, inst in insts.items():
        new_actions = [a for a in actions.get(sym, []) if (inst.id, a.ex_date, a.kind) not in known]
        # Rebuild when the full window was never read from the files (new instrument, or history that came only
        # from the per-symbol path, which can be short) or when a split/bonus is new; otherwise append new days.
        if inst.id not in last or inst.id not in from_files or new_actions:
            rebuild.add(sym)
            need_from[sym] = start
        else:
            need_from[sym] = last[inst.id] + timedelta(days=1)
    lo = min(need_from.values(), default=today)
    # Rebuilt instruments need every day in the window; read them from the (cached) files as well.
    days = [lo + timedelta(days=n) for n in range((today - lo).days + 1) if (lo + timedelta(days=n)).weekday() < 5]
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    loaded = holidays = 0
    failed: list[str] = []
    series = {x.strip().upper() for x in st.universe_series.split(",") if x.strip()}
    for n, day in enumerate(days):
        try:
            got = await nf.bhavcopy(c.filings, st.nse_bhavcopy_url_template, day)  # type: ignore[arg-type]
        except ProviderUnavailable as e:
            failed.append(f"{day}: {str(e)[:120]}")
            if c.filings.status()["breaker"] == "open":
                break
            continue
        if progress:
            progress(n + 1, len(days))
        if got is None:
            holidays += 1
            continue
        loaded += 1
        for r in got:
            sym = r.get("TckrSymb", "").upper()
            if sym not in insts or r.get("SctySrs", "").upper() not in series or day < need_from[sym]:
                continue
            close = _f(r.get("ClsPric"))
            if close is None or close <= 0:
                continue
            rows[sym].append({"date": day, "open": _f(r.get("OpnPric")), "high": _f(r.get("HghPric")), "low": _f(r.get("LwPric")),
                              "close": close, "prev_close": _f(r.get("PrvsClsgPric")), "volume": _f(r.get("TtlTradgVol")),
                              "traded_value": _f(r.get("TtlTrfVal"))})
    now = datetime.now(UTC)
    written = rebuilt = gaps = 0
    pending: list[dict[str, Any]] = []
    dialect = c.engine.dialect.name
    for sym, recs in rows.items():
        inst = insts[sym]
        df = pd.DataFrame(recs).drop_duplicates("date")
        df.index = pd.to_datetime(df.pop("date"))
        df = df.sort_index()
        acts = actions.get(sym, [])
        adj = adjust.adjust_bars(df, acts) if acts else df
        gaps += len(adjust.detect_unexplained_gaps(adj, acts)) if sym in rebuild else 0
        if sym in rebuild:
            await s.execute(delete(PriceBar).where(PriceBar.instrument_id == inst.id))
            rebuilt += 1
        for a in acts:
            if (inst.id, a.ex_date, a.kind) not in known:
                s.add(CorporateActionRow(instrument_id=inst.id, ex_date=a.ex_date, kind=a.kind, ratio=_dec(a.ratio, 6),
                                         description=a.description[:300], provider="nse_filings"))
                known.add((inst.id, a.ex_date, a.kind))
        for ts, r in adj.iterrows():
            pending.append({"instrument_id": inst.id, "date": ts.date(), "open": _dec(r.get("open")), "high": _dec(r.get("high")),
                            "low": _dec(r.get("low")), "close": _dec(r["close"]), "volume": _dec(r.get("volume"), 2),
                            "traded_value": _dec(r.get("traded_value"), 2), "provider": PROVIDER,
                            "adjustment": "split_adjusted", "retrieved_at": now, "is_demo": False})
        if len(pending) >= INSERT_CHUNK:
            written += await _upsert(s, pending, dialect)
            pending = []
    written += await _upsert(s, pending, dialect)
    detail = {"days_read": loaded, "holidays": holidays, "failed_days": failed[:20], "instruments_updated": len(rows),
              "instruments_rebuilt": rebuilt, "unexplained_gaps": gaps, "from": lo.isoformat(), "to": today.isoformat(),
              "note": act_note}
    s.add(DataSourceRecord(instrument_id=None, provider=PROVIDER, source_id="daily bhavcopy", dataset="history",
                           status="ok" if loaded and not failed else ("unavailable" if not loaded else "degraded"),
                           rows=written, detail=detail, as_of=max((d for d in days), default=None)))
    await s.commit()
    return {"status": "ok" if loaded else "failed", "bars": written, **detail}


async def _upsert(s: AsyncSession, rows: list[dict[str, Any]], dialect: str) -> int:
    if not rows:
        return 0
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    stmt = insert(PriceBar)
    cols = ("open", "high", "low", "close", "volume", "traded_value", "provider", "adjustment", "retrieved_at")
    stmt = stmt.on_conflict_do_update(index_elements=["instrument_id", "date"], set_={k: stmt.excluded[k] for k in cols})
    for i in range(0, len(rows), INSERT_CHUNK):
        await s.execute(stmt, rows[i: i + INSERT_CHUNK])
    await s.commit()
    return len(rows)
