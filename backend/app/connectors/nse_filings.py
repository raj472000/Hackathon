"""NSE filings adapter: financial results (XBRL) and con-call transcripts that NSE publishes for listed companies.

Neither NSE MCP server nor the Kite MCP advertises a financial-statements or filings tool (see the
recorded tool lists in tests/fixtures/live), so statements and transcripts come from the documents
NSE itself publishes on its corporate-filings pages:

- `{NSE_FILINGS_BASE_URL}/integrated-filing-results?index=equities&symbol=..`  quarterly results filed
  under SEBI's integrated filing (from Dec 2024), each with an Ind-AS XBRL instance document;
- `{NSE_FILINGS_BASE_URL}/corporate-announcements?index=equities&symbol=..`  announcements; con-call
  transcripts are PDFs filed under "Analysts/Institutional Investor Meet/Con. Call Updates".

Documents are fetched only from hosts listed in NSE_FILINGS_DOCUMENT_HOSTS, size-capped, and cached on
disk (a filed document never changes). XBRL values are taken as filed and normalised to INR crore;
values this adapter derives (EBIT, EBITDA, total debt, capex, share count) are listed per period in
`derived` with their formula. Nothing is estimated.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import time
import xml.etree.ElementTree as ET  # noqa: S405 - DTDs/entities are rejected before parsing
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.connectors.base import ProviderUnavailable
from app.core.resilience import CircuitBreaker, CircuitOpenError, MinIntervalLimiter

NAME = "nse_filings"
MAX_DOC_BYTES = 15 * 1024 * 1024
LIST_TTL_SECONDS = 12 * 3600
CRORE = 1e7
TRANSCRIPT_CATEGORY = re.compile(r"con\.?\s*call|analyst|investor meet", re.I)
TRANSCRIPT_TEXT = re.compile(r"transcript", re.I)
EARNINGS_TEXT = re.compile(r"earning|result|quarter|q[1-4]", re.I)
RESULTS_SEASON_DAYS = 75
MAX_FILINGS_READ = 9  # newest filings read per company: five quarters and the year-end filings within ~2 years  # results calls are held within ~60 days of the quarter end

_XBRLI = "{http://www.xbrl.org/2003/instance}"
_DIM = "explicitMember"

# field -> XBRL element candidates in preference order (Ind-AS taxonomy, then the banking format)
SINGLE: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromOperations", "NetPremiumIncome", "Income"),
    "depreciation": ("DepreciationDepletionAndAmortisationExpense",),
    "interest": ("FinanceCosts", "InterestExpended"),
    "pbt": ("ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax", "ProfitLossBeforeTax"),
    "tax": ("TaxExpense",),
    "pat": ("ProfitOrLossAttributableToOwnersOfParent", "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates",
            "ProfitLossForPeriod", "ProfitLossForThePeriod", "ProfitLossAfterTaxAndExtraordinaryItems"),
    "eps": ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "BasicEarningsLossPerShareFromContinuingOperations",
            "BasicEarningsPerShareAfterExtraordinaryItems", "BasicAndDilutedEPSAfterExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized"),
    "diluted_eps": ("DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                    "DilutedEarningsLossPerShareFromContinuingOperations", "DilutedEarningsPerShareAfterExtraordinaryItems",
                    "BasicAndDilutedEPSAfterExtraordinaryItemsNetOfTaxExpenseForThePeriodNotToBeAnnualized"),
    "exceptional_items": ("ExceptionalItemsBeforeTax",),
    "equity": ("EquityAttributableToOwnersOfParent", "Equity"),
    "cash": ("CashAndCashEquivalents",),
    "short_term_investments": ("CurrentInvestments",),
    "total_assets": ("Assets",),
    "current_assets": ("CurrentAssets",),
    "current_liabilities": ("CurrentLiabilities",),
    "total_liabilities": ("Liabilities",),
    "inventory": ("Inventories",),
    "receivables": ("TradeReceivablesCurrent",),
    "payables": ("TradePayablesCurrent",),
    "goodwill": ("Goodwill",),
    "minority_interest": ("NonControllingInterest",),
    "cfo": ("CashFlowsFromUsedInOperatingActivities",),
    "cfi": ("CashFlowsFromUsedInInvestingActivities",),
    "cff": ("CashFlowsFromUsedInFinancingActivities",),
    "dividends_paid": ("DividendsPaidClassifiedAsFinancingActivities",),
    "buybacks": ("PaymentsToAcquireOrRedeemEntitysShares",),
}
PER_SHARE = {"eps", "diluted_eps"}
FIELD_LABELS = {"Consolidated": "consolidated", "Non-Consolidated": "standalone", "Standalone": "standalone"}


class FilingsError(ProviderUnavailable):
    pass


class NotPublished(FilingsError):
    """NSE answered 404: the file does not exist (for a daily file, a trading holiday)."""


# ------------------------------------------------------------------ XBRL
@dataclass
class Context:
    start: date | None
    end: date | None
    instant: date | None
    dimensional: bool


@dataclass
class XbrlDoc:
    contexts: dict[str, Context]
    facts: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # name -> [(context id, text)]

    def text(self, name: str) -> str | None:
        for _cid, v in self.facts.get(name, []):
            if v.strip():
                return v.strip()
        return None

    def value(self, name: str, *, start: date | None = None, end: date | None = None, instant: date | None = None) -> float | None:
        """A non-dimensional fact for exactly this duration (start/end) or instant."""
        for cid, v in self.facts.get(name, []):
            c = self.contexts.get(cid)
            if c is None or c.dimensional:
                continue
            if instant is not None and c.instant != instant:
                continue
            if instant is None and (c.start != start or c.end != end):
                continue
            try:
                return float(v)
            except ValueError:
                return None
        return None

    def durations_ending(self, end: date) -> list[tuple[date, date]]:
        out = {(c.start, c.end) for c in self.contexts.values() if not c.dimensional and c.end == end and c.start is not None}
        return sorted(out, key=lambda p: p[0], reverse=True)  # type: ignore[arg-type,return-value]


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "").strip()[:10]) if s else None
    except ValueError:
        return None


def parse_xbrl(data: bytes) -> XbrlDoc:
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data.upper():
        raise FilingsError("XBRL document declares a DTD or entities; refused", "unavailable", NAME)
    try:
        root = ET.fromstring(data)  # noqa: S314 - DTD/entity declarations rejected above; source host is allowlisted
    except ET.ParseError as e:
        raise FilingsError(f"XBRL document is not valid XML: {e}", "unavailable", NAME) from e
    contexts: dict[str, Context] = {}
    for ctx in root.iter(f"{_XBRLI}context"):
        cid = ctx.get("id") or ""
        dims = any(el.tag.endswith(_DIM) or el.tag.endswith("typedMember") for el in ctx.iter())
        contexts[cid] = Context(_d(ctx.findtext(f".//{_XBRLI}startDate")), _d(ctx.findtext(f".//{_XBRLI}endDate")),
                                _d(ctx.findtext(f".//{_XBRLI}instant")), dims)
    doc = XbrlDoc(contexts)
    for el in root:
        ref = el.get("contextRef")
        if ref is None or not isinstance(el.tag, str):
            continue
        name = el.tag.rsplit("}", 1)[-1]
        doc.facts.setdefault(name, []).append((ref, el.text or ""))
    return doc


def _add(*vals: float | None) -> float | None:
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def extract_period(doc: XbrlDoc, start: date, end: date) -> tuple[dict[str, float], dict[str, str]]:
    """Facts for one reporting period: durations for [start, end], balances at `end`. Monetary values in INR crore."""
    out: dict[str, float] = {}
    derived: dict[str, str] = {}

    def dur(name: str) -> float | None:
        return doc.value(name, start=start, end=end)

    def bal(name: str) -> float | None:
        return doc.value(name, instant=end)

    balance_fields = {"equity", "cash", "short_term_investments", "total_assets", "current_assets", "current_liabilities",
                      "total_liabilities", "inventory", "receivables", "payables", "goodwill", "minority_interest"}
    for f, names in SINGLE.items():
        # First non-zero candidate; a zero is used only when no candidate has a non-zero value
        # (some filings report e.g. profit attributable to owners as 0 alongside the period's profit).
        found = [v for n in names if (v := (bal(n) if f in balance_fields else dur(n))) is not None]
        if found:
            v = next((x for x in found if x != 0), found[0])
            out[f] = v if f in PER_SHARE else v / CRORE
    if "equity" not in out and (bal("Capital") is not None or bal("ReservesAndSurplus") is not None):  # banking format
        out["equity"] = (_add(bal("Capital"), bal("ReservesAndSurplus")) or 0) / CRORE
        derived["equity"] = "capital + reserves and surplus (banking format)"
    st, lt = bal("BorrowingsCurrent"), bal("BorrowingsNoncurrent")
    if st is not None or lt is not None:
        out["short_term_debt"], out["long_term_debt"] = (st or 0) / CRORE, (lt or 0) / CRORE
        out["total_debt"] = (_add(st, lt) or 0) / CRORE
        derived["total_debt"] = "BorrowingsCurrent + BorrowingsNoncurrent (lease liabilities excluded)"
    capex = _add(dur("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
                 dur("PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"), dur("PurchaseOfIntangibleAssetsUnderDevelopment"))
    if capex is not None:
        out["capex"] = abs(capex) / CRORE
        derived["capex"] = "purchase of property, plant and equipment + purchase of intangible assets (cash flow statement)"
    cogs = _add(dur("CostOfMaterialsConsumed"), dur("PurchasesOfStockInTrade"),
                dur("ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade"))
    if cogs:  # zero for service companies: cost of goods is not meaningful there
        out["cogs"] = cogs / CRORE
        derived["cogs"] = "materials consumed + purchases of stock-in-trade + change in inventories"
    is_bank = "InterestEarned" in doc.facts
    pbe = dur("ProfitBeforeExceptionalItemsAndTax")
    if pbe is None and out.get("pbt") is not None:
        pbe = out["pbt"] * CRORE - (dur("ExceptionalItemsBeforeTax") or 0)
    fin, oth = dur("FinanceCosts"), dur("OtherIncome")
    if not is_bank and pbe is not None and fin is not None:
        out["ebit"] = (pbe + fin - (oth or 0)) / CRORE
        derived["ebit"] = "profit before exceptional items and tax + finance costs - other income"
        if out.get("depreciation") is not None:
            out["ebitda"] = out["ebit"] + out["depreciation"]
            derived["ebitda"] = "EBIT + depreciation and amortisation"
    # Paid-up capital and face value are reported against the filing's own contexts (quarter or year to date).
    paid = next((v for cid, t in doc.facts.get("PaidUpValueOfEquityShareCapital", [])
                 if (c := doc.contexts.get(cid)) and not c.dimensional and end in (c.end, c.instant) and (v := _num(t))), None)
    face = next((v for _cid, t in doc.facts.get("FaceValueOfEquityShareCapital", []) if (v := _num(t))), None)
    if paid and face:
        out["shares_outstanding"] = paid / face / CRORE
        derived["shares_outstanding"] = "paid-up equity share capital / face value per share (crore shares)"
    elif out.get("pat") and out.get("eps"):
        out["shares_outstanding"] = out["pat"] / out["eps"]
        derived["shares_outstanding"] = "profit after tax / basic EPS (face value not filed), crore shares"
    return out, derived


BONUS_SPLIT_RATIO = 1.4  # a share-count jump at least this large between filings is treated as a bonus or split


def restate_share_changes(periods: list[dict[str, Any]]) -> None:
    """Restate per-share figures of periods before a bonus issue or split to the latest share count.

    Share counts come from paid-up capital / face value. When the latest count is at least
    BONUS_SPLIT_RATIO times an earlier one, the earlier EPS is multiplied by old/new shares so that
    growth rates compare like with like. The reported values are kept under `*_reported`."""
    dated = [p for p in periods if p.get("shares_outstanding")]
    if not dated:
        return
    latest = max(dated, key=lambda p: p["period_end"])["shares_outstanding"]
    for p in dated:
        ratio = latest / p["shares_outstanding"]
        if ratio < BONUS_SPLIT_RATIO:
            continue
        for k in ("eps", "diluted_eps"):
            if p.get(k) is not None:
                p[f"{k}_reported"] = p[k]
                p[k] = p[k] / ratio
        p["shares_outstanding_reported"] = p["shares_outstanding"]
        p["shares_outstanding"] = latest
        p.setdefault("derived", {})["eps"] = (f"restated for a share-count change of x{ratio:.2f} (bonus issue or split) "
                                              "to the latest share count")


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _months(start: date, end: date) -> int:
    return round(((end - start).days + 1) / 30.44)


# ------------------------------------------------------------------ listings
def _parse_nse_date(s: Any) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip().title(), fmt).date()
        except ValueError:
            continue
    return None


def fiscal_quarter(period_end: date) -> tuple[int, int]:
    """Indian fiscal year April-March: (fiscal_year, quarter); FY2027 ends 31 Mar 2027."""
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(period_end.month)
    if q is None:
        raise ValueError(f"{period_end} is not a quarter end")
    return (period_end.year + 1 if period_end.month >= 4 else period_end.year), q


def quarter_discussed(filed: date) -> date:
    """Latest quarter end strictly before a filing date: the quarter a results call discusses."""
    y = filed.year
    ends = [date(y - 1, 12, 31), date(y, 3, 31), date(y, 6, 30), date(y, 9, 30), date(y, 12, 31)]
    return max(e for e in ends if e < filed)


@dataclass
class TranscriptFiling:
    symbol: str
    filed_at: date
    url: str
    description: str
    period_end: date
    fiscal_year: int
    quarter: int


class NseFilingsProvider:
    name = NAME

    def __init__(self, base_url: str | None, document_hosts: list[str], cache_dir: Path, *, user_agent: str,
                 min_interval: float = 1.0, timeout: float = 30.0, enabled: bool = True,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.document_hosts = {h.strip().lower() for h in document_hosts if h.strip()}
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False,
                                       headers={"User-Agent": user_agent, "Accept": "application/json, */*"})
        self._limiter = MinIntervalLimiter(min_interval)
        self.breaker_reset_seconds = 120.0
        self._breaker = CircuitBreaker(5, self.breaker_reset_seconds)
        self._lock = asyncio.Lock()
        self._stats: dict[str, Any] = {"calls": 0, "errors": 0, "cache_hits": 0, "last_success_at": None, "last_error": None}

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.base_url)

    def status(self) -> dict[str, Any]:
        reason = None if self.available else ("disabled in demo mode" if not self.enabled else "NSE_FILINGS_BASE_URL is not set")
        return {"name": self.name, "state": "ok" if self.available else "not_configured", "reason": reason, "base_url": self.base_url,
                "document_hosts": sorted(self.document_hosts), "breaker": self._breaker.state, **self._stats,
                "note": "Financial results (XBRL) and con-call transcripts as filed with NSE."}

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ transport
    def _cache_path(self, key: str, ext: str) -> Path:
        return self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.{ext}"

    async def _get(self, url: str) -> bytes:
        if not self.available:
            raise FilingsError(self.status()["reason"] or "not configured", "not_configured", NAME)
        try:
            self._breaker.before_call()
        except CircuitOpenError as e:
            raise FilingsError(f"NSE filings temporarily paused after repeated failures: {e}", "unavailable", NAME) from e
        async with self._lock:  # one request at a time, spaced by the limiter
            await self._limiter.acquire()
            self._stats["calls"] += 1
            try:
                async with self._http.stream("GET", url) as r:
                    if r.status_code == 404:
                        raise NotPublished(f"NSE has no file at {urlsplit(url).path}", "unavailable", NAME)
                    if r.status_code != 200:
                        raise FilingsError(f"NSE returned HTTP {r.status_code} for {urlsplit(url).path}", "unavailable", NAME)
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_DOC_BYTES:
                            raise FilingsError(f"document larger than {MAX_DOC_BYTES // 1_048_576} MB refused", "unavailable", NAME)
            except httpx.HTTPError as e:
                self._fail(f"{type(e).__name__} fetching {urlsplit(url).path}")
                raise FilingsError(f"NSE filings request failed: {type(e).__name__}", "unavailable", NAME) from e
            except NotPublished:
                self._breaker.success()  # a definite answer from NSE, not an outage
                raise
            except FilingsError as e:
                self._fail(str(e))
                raise
        self._breaker.success()
        self._stats["last_success_at"] = datetime.now(UTC).isoformat()
        return bytes(body)

    def _fail(self, msg: str) -> None:
        self._breaker.failure()
        self._stats["errors"] += 1
        self._stats["last_error"] = msg[:200]

    async def _api(self, path: str, params: dict[str, str]) -> Any:
        """A JSON listing, cached on disk for LIST_TTL_SECONDS."""
        url = str(httpx.URL(f"{self.base_url}/{path}", params=params))
        p = self._cache_path(url, "json")
        if p.exists() and time.time() - p.stat().st_mtime < LIST_TTL_SECONDS:
            self._stats["cache_hits"] += 1
            return json.loads(p.read_text())
        body = await self._get(url)
        try:
            data = json.loads(body)
        except ValueError as e:
            self._fail(f"{path}: response is not JSON")
            raise FilingsError(f"NSE {path} did not return JSON (the site may be blocking automated requests)", "unavailable", NAME) from e
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        return data

    def allowed_document(self, url: str) -> bool:
        u = urlsplit(url)
        return u.scheme == "https" and (u.hostname or "").lower() in self.document_hosts and not u.username and u.port in (None, 443)

    async def document(self, url: str, ext: str, ttl: float | None = None) -> bytes:
        """A document from an allowlisted NSE host. Filings never change and are cached permanently;
        lists that NSE republishes (equity list, index constituents) pass a `ttl` in seconds."""
        if not self.enabled:
            raise FilingsError("NSE public files are disabled in demo mode", "not_configured", NAME)
        if not self.allowed_document(url):
            raise FilingsError(f"document host not allowed: {urlsplit(url).hostname}", "unavailable", NAME)
        p = self._cache_path(url, ext)
        if p.exists() and (ttl is None or time.time() - p.stat().st_mtime < ttl):
            self._stats["cache_hits"] += 1
            return p.read_bytes()
        body = await self._get(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        return body

    # ------------------------------------------------------------------ financial results
    async def result_filings(self, symbol: str) -> list[dict[str, Any]]:
        """Integrated-filing financial results, newest first, one per period and basis.

        Earlier filings (before the integrated format) carry only the quarter's own figures in XBRL, so the
        machine-readable annual history starts with the year ended March 2025."""
        out: list[dict[str, Any]] = []
        data = await self._api("integrated-filing-results", {"index": "equities", "symbol": symbol.upper(),
                                                              "type": "Integrated Filing- Financials"})
        for r in (data.get("data") if isinstance(data, dict) else data) or []:
            end = _parse_nse_date(r.get("qe_Date"))
            if end is None or not str(r.get("xbrl") or "").endswith(".xml") or "Financial" not in str(r.get("type", "Financials")):
                continue
            out.append({"period_end": end, "basis": FIELD_LABELS.get(str(r.get("consolidated")), "standalone"), "kind": "quarterly",
                        "xbrl": r["xbrl"], "filed_at": _parse_nse_date(r.get("broadcast_Date")), "audited": r.get("audited"),
                        "revision": r.get("type_Sub"), "source": "integrated-filing-results"})
        # newest filing wins for the same period, basis and kind (revisions replace originals)
        best: dict[tuple[date, str, str], dict[str, Any]] = {}
        for f in out:
            k = (f["period_end"], f["basis"], f["kind"])
            if k not in best or (f["filed_at"] or date.min) > (best[k]["filed_at"] or date.min):
                best[k] = f
        return sorted(best.values(), key=lambda f: f["period_end"], reverse=True)

    async def statements(self, symbol: str, max_annual: int = 5, max_quarters: int = 5) -> dict[str, Any] | None:
        """Annual and quarterly periods in the shape of an uploaded statements file (INR crore)."""
        filings = await self.result_filings(symbol)
        if not filings:
            return None
        bases = {f["basis"] for f in filings if f["kind"] == "quarterly"} or {f["basis"] for f in filings}
        basis = "consolidated" if "consolidated" in bases else "standalone"
        chosen = [f for f in filings if f["basis"] == basis]
        periods: dict[tuple[str, date], dict[str, Any]] = {}
        warnings: list[str] = []
        quarters = annual = 0
        for f in chosen[:MAX_FILINGS_READ]:
            # Any filing can carry a 12-month (year-to-date) context: the last quarter of the company's financial
            # year, which ends in March for most companies and in December for some (ABB India, for example).
            want_q = f["kind"] == "quarterly" and quarters < max_quarters
            want_a = annual < max_annual and ("annual", f["period_end"]) not in periods
            if not (want_q or want_a):
                continue
            try:
                doc = parse_xbrl(await self.document(f["xbrl"], "xml"))
            except FilingsError as e:
                warnings.append(f"{f['period_end'].isoformat()}: {e}")
                continue
            for start, end in doc.durations_ending(f["period_end"]):
                m = _months(start, end)
                kind = "quarterly" if m == 3 and want_q else "annual" if m == 12 and want_a else None
                if kind is None or (kind, end) in periods:
                    continue
                facts, derived = extract_period(doc, start, end)
                if not facts:
                    continue
                fy = end.year if kind == "annual" else fiscal_quarter(end)[0]
                periods[(kind, end)] = {"period_end": end.isoformat(), "period_type": kind, "fiscal_year": fy,
                                        "published_at": f["filed_at"].isoformat() if f["filed_at"] else None, **facts,
                                        "derived": derived, "xbrl_url": f["xbrl"], "audited": f["audited"]}
                if kind == "quarterly":
                    quarters += 1
                else:
                    annual += 1
        if not periods:
            return None
        ordered = sorted(periods.values(), key=lambda p: (p["period_type"], p["period_end"]))
        restate_share_changes(ordered)
        return {"symbol": symbol.upper(), "exchange": "NSE", "currency": "INR", "unit": "crore", "source": f"NSE filings ({basis})",
                "source_url": f"{self.base_url}/integrated-filing-results?index=equities&symbol={symbol.upper()}", "basis": basis,
                "periods": ordered, "warnings": warnings,
                "retrieved_at": datetime.now(UTC).isoformat()}

    # ------------------------------------------------------------------ con-call transcripts
    async def transcripts(self, symbol: str, limit: int = 4) -> list[TranscriptFiling]:
        """Con-call transcripts filed with NSE, newest first, at most one per fiscal quarter."""
        data = await self._api("corporate-announcements", {"index": "equities", "symbol": symbol.upper()})
        rows = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else []) or []
        best: dict[tuple[int, int], tuple[tuple[int, int, date], TranscriptFiling]] = {}
        for r in rows:
            text = f"{r.get('desc') or ''} {r.get('attchmntText') or ''}"
            url = str(r.get("attchmntFile") or "")
            if not (TRANSCRIPT_CATEGORY.search(str(r.get("desc") or "")) and TRANSCRIPT_TEXT.search(text)) or not url.lower().endswith(".pdf"):
                continue
            filed = _parse_nse_date(str(r.get("an_dt") or r.get("sort_date") or "")[:11])
            if filed is None or not self.allowed_document(url):
                continue
            end = quarter_discussed(filed)
            fy, q = fiscal_quarter(end)
            # Prefer the results call: filed within the results season and described as an earnings/results call.
            in_season = (filed - end).days <= RESULTS_SEASON_DAYS
            earnings = bool(EARNINGS_TEXT.search(f"{text} {url}"))
            rank = (int(in_season), int(earnings), filed)
            t = TranscriptFiling(symbol.upper(), filed, url, str(r.get("attchmntText") or r.get("desc") or "")[:300], end, fy, q)
            if (fy, q) not in best or rank > best[(fy, q)][0]:
                best[(fy, q)] = (rank, t)
        out = [t for rank, t in best.values() if rank[0]] or [t for _rank, t in best.values()]
        out.sort(key=lambda t: t.filed_at, reverse=True)
        return out[:limit]


# ------------------------------------------------------------------ whole-market files
LIST_FILE_TTL_SECONDS = 24 * 3600


def _csv(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig", "replace")
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]


async def equity_list(p: NseFilingsProvider, url: str) -> list[dict[str, str]]:
    """NSE's list of listed equities: SYMBOL, NAME OF COMPANY, SERIES, ISIN NUMBER, FACE VALUE, ..."""
    return _csv(await p.document(url, "csv", ttl=LIST_FILE_TTL_SECONDS))


async def industry_list(p: NseFilingsProvider, url: str) -> dict[str, dict[str, str]]:
    """Index constituent file with NSE's industry classification, by symbol."""
    return {r["Symbol"]: r for r in _csv(await p.document(url, "csv", ttl=LIST_FILE_TTL_SECONDS)) if r.get("Symbol")}


async def bhavcopy(p: NseFilingsProvider, template: str, day: date) -> list[dict[str, str]] | None:
    """All securities traded on `day` from NSE's daily bhavcopy (UDiFF format), or None on a holiday.
    A holiday is remembered so the file is not requested again."""
    url = template.replace("{yyyymmdd}", day.strftime("%Y%m%d"))
    marker = p._cache_path(url, "holiday")
    if marker.exists():
        return None
    try:
        data = await p.document(url, "zip", ttl=None if day < datetime.now(UTC).date() else 3600)
    except NotPublished:
        if day < datetime.now(UTC).date():
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(day.isoformat())
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            return _csv(z.read(name))
    except (zipfile.BadZipFile, StopIteration) as e:
        raise FilingsError(f"bhavcopy for {day} is not a readable zip file", "unavailable", NAME) from e


async def market_corporate_actions(p: NseFilingsProvider, start: date, end: date) -> list[dict[str, Any]]:
    """Every equity corporate action with an ex-date in [start, end], in one call."""
    data = await p._api("corporates-corporateActions", {"index": "equities", "from_date": start.strftime("%d-%m-%Y"),
                                                         "to_date": end.strftime("%d-%m-%Y")})
    return data if isinstance(data, list) else []


async def company_industry(p: NseFilingsProvider, symbol: str, today: date) -> str | None:
    """NSE's industry label for a company (`smIndustry` on its announcements, e.g. "Computers - Software")."""
    start = today.replace(year=today.year - 1)
    data = await p._api("corporate-announcements", {"index": "equities", "symbol": symbol.upper(),
                                                     "from_date": start.strftime("%d-%m-%Y"), "to_date": today.strftime("%d-%m-%Y")})
    rows = data if isinstance(data, list) else []
    return next((str(r["smIndustry"]).strip() for r in rows if str(r.get("smIndustry") or "").strip() not in ("", "-")), None)


def build_filings(s: Any) -> NseFilingsProvider:
    return NseFilingsProvider(
        s.nse_filings_base_url, [h for h in (s.nse_filings_document_hosts or "").split(",")], s.nse_filings_cache_dir,
        user_agent=s.nse_filings_user_agent, min_interval=s.nse_filings_min_interval_seconds, timeout=s.nse_filings_timeout_seconds,
        enabled=not s.is_demo)

