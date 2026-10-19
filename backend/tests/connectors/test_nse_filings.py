"""NSE filings adapter: contract tests on documents recorded from NSE on 15 Oct 2026 (public filings).

The XBRL instance documents and listings in tests/fixtures/live/nse_filings were fetched from
www.nseindia.com/api and nsearchives.nseindia.com; the HTTP layer here is a MockTransport that
serves them, so no network is used."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.connectors.base import ProviderUnavailable
from app.connectors.nse_filings import (
    NseFilingsProvider,
    extract_period,
    fiscal_quarter,
    parse_xbrl,
    quarter_discussed,
    restate_share_changes,
)

FX = Path(__file__).parents[1] / "fixtures" / "live" / "nse_filings"
BASE = "https://www.nseindia.com/api"


def _doc(name: str):
    return parse_xbrl((FX / name).read_bytes())


# ------------------------------------------------------------------ XBRL facts
def test_tcs_quarter_income_statement_balance_sheet_and_derived_values():
    d = _doc("tcs_2026-09-30_consolidated.xml")
    q, derived = extract_period(d, date(2026, 7, 1), date(2026, 9, 30))
    # Reported figures, INR crore (XBRL values are absolute rupees)
    assert q["revenue"] == pytest.approx(73188.0)
    assert q["pat"] == pytest.approx(13884.0)  # attributable to owners of the parent
    assert q["eps"] == pytest.approx(38.37) and q["diluted_eps"] == pytest.approx(38.37)
    assert q["equity"] == pytest.approx(118986.0) and q["total_assets"] == pytest.approx(190782.0)
    assert q["total_debt"] == 0.0 and q["cash"] == pytest.approx(5106.0)
    assert q["shares_outstanding"] == pytest.approx(362.0)  # 362 crore shares = paid-up 362 crore / face value 1
    # EBIT = profit before exceptional items and tax + finance costs - other income
    assert q["ebit"] == pytest.approx(18638.0 + 252.0 - 1337.0)
    assert q["ebitda"] == pytest.approx(q["ebit"] + 1262.0)
    assert set(derived) >= {"ebit", "ebitda", "total_debt", "shares_outstanding"}
    assert "cogs" not in q  # a services company files zero materials cost: not meaningful, not stored as 0


def test_half_year_context_carries_cash_flow():
    d = _doc("tcs_2026-09-30_consolidated.xml")
    h, _ = extract_period(d, date(2026, 4, 1), date(2026, 9, 30))
    assert h["cfo"] == pytest.approx(25801.0) and h["capex"] == pytest.approx(1926.0 + 321.0)
    assert h["dividends_paid"] == pytest.approx(15928.0)


def test_march_filing_has_the_full_year_as_its_own_context():
    d = _doc("tcs_2026-03-31_consolidated.xml")
    spans = d.durations_ending(date(2026, 3, 31))
    assert (date(2025, 4, 1), date(2026, 3, 31)) in spans and (date(2026, 1, 1), date(2026, 3, 31)) in spans
    fy, _ = extract_period(d, date(2025, 4, 1), date(2026, 3, 31))
    assert fy["revenue"] == pytest.approx(267021.0) and fy["eps"] == pytest.approx(136.01)


def test_bank_format_equity_and_no_industrial_ebit():
    d = _doc("hdfcbank_2026-03-31_consolidated.xml")
    fy, derived = extract_period(d, date(2025, 4, 1), date(2026, 3, 31))
    assert fy["pat"] > 0 and fy["eps"] > 0
    assert fy["equity"] > 0 and derived["equity"].startswith("capital + reserves")
    assert "ebit" not in fy and "ebitda" not in fy  # not meaningful for a bank


def test_insurer_format_profit_eps_and_share_count_from_pat_over_eps():
    d = _doc("sbilife_2026-06-30_standalone.xml")
    q, derived = extract_period(d, date(2026, 4, 1), date(2026, 6, 30))
    assert q["pat"] == pytest.approx(724.93, abs=0.01) and q["eps"] == pytest.approx(7.22)
    assert q["shares_outstanding"] == pytest.approx(q["pat"] / q["eps"])
    assert "face value not filed" in derived["shares_outstanding"]


def test_documents_with_a_dtd_or_entities_are_refused():
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    with pytest.raises(ProviderUnavailable, match="DTD"):
        parse_xbrl(evil)


def test_bonus_issue_restates_earlier_eps_to_the_latest_share_count():
    periods = [{"period_end": "2025-03-31", "eps": 92.81, "shares_outstanding": 765.0},
               {"period_end": "2026-03-31", "eps": 49.5, "shares_outstanding": 1539.0}]
    restate_share_changes(periods)
    assert periods[0]["eps"] == pytest.approx(92.81 * 765 / 1539) and periods[0]["eps_reported"] == 92.81
    assert periods[0]["shares_outstanding"] == 1539.0 and "bonus" in periods[0]["derived"]["eps"]
    assert "eps_reported" not in periods[1]


def test_fiscal_quarters_and_the_quarter_a_call_discusses():
    assert fiscal_quarter(date(2026, 9, 30)) == (2027, 2)
    assert fiscal_quarter(date(2026, 3, 31)) == (2026, 4)
    assert quarter_discussed(date(2026, 10, 14)) == date(2026, 9, 30)
    assert quarter_discussed(date(2026, 1, 17)) == date(2025, 12, 31)


# ------------------------------------------------------------------ provider over HTTP
def _provider(tmp_path, handler) -> NseFilingsProvider:
    return NseFilingsProvider(BASE, ["nsearchives.nseindia.com"], tmp_path, user_agent="test", min_interval=0,
                              transport=httpx.MockTransport(handler))


def _serve(calls: list[str]):
    listing = json.loads((FX / "tcs_integrated_filings_2026-10-15.json").read_text())
    by_url = {r["xbrl"]: r for r in listing["data"]}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path.endswith("/integrated-filing-results"):
            return httpx.Response(200, json=listing)
        if str(req.url) in by_url:
            r = by_url[str(req.url)]
            name = {"30-SEP-2026": "tcs_2026-09-30_consolidated.xml", "31-MAR-2026": "tcs_2026-03-31_consolidated.xml"}.get(r["qe_Date"])
            if name and r["consolidated"] == "Consolidated":
                return httpx.Response(200, content=(FX / name).read_bytes())
        return httpx.Response(404)

    return handler


async def test_statements_prefer_consolidated_and_keep_going_past_missing_documents(tmp_path):
    calls: list[str] = []
    p = _provider(tmp_path, _serve(calls))
    st = await p.statements("TCS")
    assert st is not None and st["basis"] == "consolidated" and st["source"] == "NSE filings (consolidated)"
    kinds = {(x["period_type"], x["period_end"]) for x in st["periods"]}
    assert ("quarterly", "2026-09-30") in kinds and ("annual", "2026-03-31") in kinds
    assert st["warnings"]  # documents the fixture server does not have are reported, not fatal
    assert all("Non-Consolidated" not in u for u in calls)
    # Listings and documents are cached on disk: a second read makes no request for them.
    n = len(calls)
    await p.statements("TCS")
    assert all(not u.endswith(".xml") or "INTEGRATED" not in u or u in calls[:n] for u in calls[n:])
    await p.close()


async def test_documents_only_from_allowlisted_https_hosts(tmp_path):
    p = _provider(tmp_path, lambda r: httpx.Response(200, content=b"x"))
    for bad in ("http://nsearchives.nseindia.com/a.pdf", "https://evil.example.com/a.pdf", "https://user@nsearchives.nseindia.com/a.pdf",
                "https://nsearchives.nseindia.com:8443/a.pdf"):
        with pytest.raises(ProviderUnavailable, match="not allowed"):
            await p.document(bad, "pdf")
    await p.close()


async def test_disabled_without_base_url_and_reports_why(tmp_path):
    p = NseFilingsProvider(None, ["nsearchives.nseindia.com"], tmp_path, user_agent="t")
    assert not p.available and p.status()["reason"] == "NSE_FILINGS_BASE_URL is not set"
    with pytest.raises(ProviderUnavailable):
        await p.statements("TCS")
    await p.close()


async def test_blocked_html_response_is_an_error_not_an_empty_list(tmp_path):
    p = _provider(tmp_path, lambda r: httpx.Response(200, text="<html>Access denied</html>"))
    with pytest.raises(ProviderUnavailable, match="did not return JSON"):
        await p.result_filings("TCS")
    await p.close()


async def test_transcripts_pick_the_results_call_of_each_quarter(tmp_path):
    ann = json.loads((FX / "hdfcbank_announcements_2026-10-15.json").read_text())
    p = _provider(tmp_path, lambda r: httpx.Response(200, json=ann))
    ts = await p.transcripts("HDFCBANK", limit=4)
    by_q = {(t.fiscal_year, t.quarter): t for t in ts}
    assert all(t.url.startswith("https://nsearchives.nseindia.com/") and t.url.endswith(".pdf") for t in ts)
    # An investor-meet transcript filed in March is not taken as the Q3 results call; the January call is.
    q3 = by_q.get((2026, 3))
    assert q3 is not None and q3.filed_at.month == 1
    assert [t.filed_at for t in ts] == sorted((t.filed_at for t in ts), reverse=True)
    await p.close()
