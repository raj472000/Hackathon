"""Fixes from the live review on 15 Oct 2026: reasoning-model truncation, research grounding of inline
citations, N-day movers with prices of the day, stale-price refresh, NSE con-call transcripts with a
web check, thin-composite sectors, TTM earnings and the broker backfill after login.
Market data is DEMO synthetic data; LLM and NSE filings are mocked HTTP transports."""

import asyncio
import json
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from sqlalchemy import select

from app.connectors.llm import ChatCompletionsLLM, LLMTruncated
from app.db.models import ConcallTranscript, Instrument
from app.domain import concall_score as cs
from app.services import ask, concall, metrics, research, sector_rotation

pytestmark = pytest.mark.usefixtures("broker_logged_in")
API = "/api/v1"


def _with(demo, **over):
    c = demo.container
    return SimpleNamespace(**{**c.__dict__, "benchmark_symbol": c.benchmark_symbol, **over})


async def _job(demo, job_id, limit_s=90.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit_s:
        st = (await demo.client.get(f"{API}/jobs/{job_id}")).json()
        if st["status"] != "running":
            return st
        await asyncio.sleep(0.1)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------ LLM: reasoning models
async def test_reply_cut_off_by_reasoning_is_retried_with_a_larger_budget():
    seen: list[int] = []

    def h(req):
        body = json.loads(req.content)
        seen.append(body["max_tokens"])
        if len(seen) == 1:  # all tokens spent on reasoning: no answer text
            return httpx.Response(200, json={"choices": [{"message": {"content": "", "reasoning": "thinking..."}, "finish_reason": "length"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"answer": "ok"})}, "finish_reason": "stop"}]})

    llm = ChatCompletionsLLM(api_key="sk-test0000", base_url="https://llm.test/v1", model_id="m", enabled=True, max_retries=0,
                             max_output_tokens=1000, transport=httpx.MockTransport(h))
    out = await llm.complete_validated("sys", "user", ask.AskAnswer)
    assert out.answer == "ok" and seen == [1000, 2000]


async def test_truncation_error_names_the_settings_to_change():
    h = lambda req: httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})  # noqa: E731
    llm = ChatCompletionsLLM(api_key="sk-test0000", base_url="https://llm.test/v1", model_id="m", enabled=True, max_retries=0,
                             transport=httpx.MockTransport(h))
    with pytest.raises(LLMTruncated, match="LLM_MAX_OUTPUT_TOKENS"):
        await llm.complete_validated("sys", "user", ask.AskAnswer)


async def test_schema_is_always_in_the_prompt_even_with_strict_structured_output():
    bodies = []

    def h(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"answer": "ok"})}}]})

    llm = ChatCompletionsLLM(api_key="sk-test0000", base_url="https://llm.test/v1", model_id="m", enabled=True, max_retries=0,
                             response_format="json_schema", transport=httpx.MockTransport(h))
    await llm.complete_validated("sys", "user", ask.AskAnswer)
    assert bodies[0]["response_format"]["type"] == "json_schema" and '"answer"' in bodies[0]["messages"][0]["content"]


# ------------------------------------------------------------ research grounding
EVIDENCE = {"items": {"M:roe": {"kind": "calculation", "label": "ROE", "value": 48.72},
                      "M:return_12m": {"kind": "calculation", "label": "Return 12M", "value": -20.97},
                      "S1": {"kind": "source", "title": "Q2 FY27 results", "url": "https://x.test/a", "snippet": "profit up 4% QoQ"}}}


def test_inline_evidence_ids_count_as_citations_and_are_removed_from_the_text():
    clean, why = research.check_point(research.Point(text="ROE is 48.72% but the 1-year return is -20.97% (M:roe, M:return_12m).", evidence=[]),
                                      EVIDENCE)
    assert why is None and clean["evidence"] == ["M:roe", "M:return_12m"] and "(M:" not in clean["text"]


def test_labels_windows_and_dates_are_not_treated_as_facts():
    p = research.Point(text="Q2 FY27 results on Oct 8, 2026: profit up 4% QoQ; RSI(14) and the 200-day SMA200 are context.", evidence=["S1"])
    assert research.check_point(p, EVIDENCE)[1] is None


def test_a_number_cited_to_the_wrong_item_is_still_rejected():
    assert research.check_point(research.Point(text="ROE is -20.97%.", evidence=["M:roe"]), EVIDENCE)[0] is None


# ------------------------------------------------------------ ask: movers over N sessions
async def test_rules_read_bullish_over_the_last_3_days_as_a_3_session_movers_question(demo):
    async with demo.container.sessions() as s:
        insts = await ask._universe(s)
    p = ask.rule_plan("strong bullish stocks in last 3 days", insts)
    assert p.intent == "movers" and p.lookback_days == 3 and p.sort_direction == "desc"
    p = ask.rule_plan("Which stocks fell the most this week?", insts)
    assert p.intent == "movers" and p.lookback_days == 5 and p.sort_direction == "asc"
    assert ask.parse_lookback("over the past 2 weeks") == 10 and ask.parse_lookback("last 1 month") == 21


async def test_movers_rank_by_the_n_session_change_ending_at_the_price_of_the_day(demo):
    r = await demo.client.post(API + "/ask", json={"question": "strong bullish stocks in last 3 days"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["plan"]["intent"] == "movers" and d["plan"]["lookback_days"] == 3 and d["movers"]
    ch = [m["change_pct"] for m in d["movers"]]
    assert ch == sorted(ch, reverse=True)
    async with demo.container.sessions() as s:
        insts = {i.symbol: i for i in await ask._universe(s)}
        top = d["movers"][0]
        bars = (await ask.load_bars(s, [insts[top["symbol"]].id]))[insts[top["symbol"]].id]
    closes = bars["close"]
    # The mock bulk quote reports the last stored session, so the window is the last 4 stored closes.
    expect = (top["price"] / float(closes.iloc[-4]) - 1) * 100
    assert top["change_pct"] == pytest.approx(expect, abs=0.01) and top["from_date"] == closes.index[-4].date().isoformat()
    assert top["live"] is True and top["price_source"] == "nse_mcp" and 0 <= top["up_sessions"] <= 3
    ev = next(e for e in d["evidence"] if e["id"] == "P1")
    assert ev["kind"] == "movers_window" and ev["data"]["top"][0]["symbol"] == top["symbol"]
    assert d["llm_disclaimer"] is None  # no model in the demo stack: the rules answer is not labelled as model output


async def test_movers_fall_back_to_stored_closes_when_no_live_source_answers(demo):
    class Down:
        async def bulk_quotes(self, symbols):
            from app.connectors.base import ProviderUnavailable

            raise ProviderUnavailable("timed out", "unavailable", "nse_mcp")

    class KiteDown:
        async def get_quotes(self, symbols):
            from app.connectors.base import ProviderUnavailable

            raise ProviderUnavailable("broker login required", "login_required", "kite_mcp")

    c = _with(demo, nse=Down(), kite=KiteDown())
    async with demo.container.sessions() as s:
        insts = await ask._universe(s)
        ev, rows = await ask.movers(c, s, ask.AskPlan(intent="movers", lookback_days=5), insts)
    assert rows and not any(r["live"] for r in rows) and all(r["price_source"] == "system database" for r in rows)
    assert "NSE MCP bulk quote unavailable" in ev["note"] and "Kite MCP quotes unavailable" in ev["note"]


async def test_model_answer_carries_the_reliability_disclaimer(demo):
    replies = iter([{"intent": "movers", "lookback_days": 3, "sort_direction": "desc"},
                    {"answer": "DEMOIT01 rose most [P1].", "points": [{"text": "DEMOIT01 led.", "evidence_ids": ["P1"]}], "caveats": []}])

    def h(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(next(replies))}}]})

    llm = ChatCompletionsLLM(api_key="sk-test0000", base_url="https://llm.test/v1", model_id="m", enabled=True, max_retries=0,
                             transport=httpx.MockTransport(h))
    async with demo.container.sessions() as s:
        d = await ask.ask(_with(demo, llm=llm), s, "strong bullish stocks in last 3 days")
    assert d["answer_source"] == "llm" and "language model" in d["llm_disclaimer"] and "not rely on it alone" in d["llm_disclaimer"]


async def test_stale_stored_prices_start_one_background_refresh(demo):
    class Later:
        def now(self):
            return datetime.now(UTC) + timedelta(days=30)

    from app.core.cache import MemoryCache

    started: list[str] = []

    async def fake_start(c, kind, key, fn, trigger, who):
        started.append(trigger)
        return 999

    c = _with(demo, clock=Later(), cache=MemoryCache())
    orig = ask.jobs.start_background
    ask.jobs.start_background = fake_start  # type: ignore[assignment]
    try:
        async with demo.container.sessions() as s:
            first = await ask.ensure_fresh_prices(c, s)
            second = await ask.ensure_fresh_prices(c, s)
    finally:
        ask.jobs.start_background = orig  # type: ignore[assignment]
    # demo mode never refreshes from a question; a live-mode container does, once per cooldown
    assert first["state"] == "current" and started == []
    c.settings = demo.container.settings.model_copy(update={"data_mode": "live"})
    ask.jobs.start_background = fake_start  # type: ignore[assignment]
    try:
        async with demo.container.sessions() as s:
            first = await ask.ensure_fresh_prices(c, s)
            second = await ask.ensure_fresh_prices(c, s)
    finally:
        ask.jobs.start_background = orig  # type: ignore[assignment]
    assert first["state"] == "stale" and first["started_job"] == 999 and second["started_job"] is None
    assert started == ["ask_stale_data"]


# ------------------------------------------------------------ concalls from NSE with a web check
PREV = ("Management: We expect to commission the new Pune plant by the end of Q2 and we are targeting EBITDA margin of 22 percent "
        "for the full year. We will reduce net debt by 500 crore this year through internal accruals. ") * 12
CUR = ("Management: The Pune plant was commissioned in August and is ramping up well. Margins were 20.5 percent this quarter. ") * 12


class FakeFilings:
    available = True

    def status(self):
        return {"reason": None}

    async def transcripts(self, symbol, limit=4):
        from app.connectors.nse_filings import TranscriptFiling

        return [TranscriptFiling(symbol, date(2026, 10, 20), "https://nsearchives.nseindia.com/corporate/cur.pdf", "Transcript", date(2026, 9, 30), 2027, 2),
                TranscriptFiling(symbol, date(2026, 7, 21), "https://nsearchives.nseindia.com/corporate/prev.pdf", "Transcript", date(2026, 6, 30), 2027, 1)]

    async def document(self, url, ext):
        return (CUR if url.endswith("cur.pdf") else PREV).encode()


class FakeSearch:
    name, available = "fake", True

    def status(self):
        return {}

    async def search(self, query, n):
        return [{"title": "Company completes debt reduction of 500 crore", "url": "https://news.test/debt", "snippet":
                 "The company has reduced net debt by 500 crore this year, completed ahead of plan.", "published_at": "2026-09-01",
                 "source_type": "secondary"}]


async def test_transcripts_are_fetched_from_nse_and_promises_checked_on_the_web(demo, monkeypatch):
    monkeypatch.setattr(concall, "extract_pdf_text", lambda b: b.decode())
    c = _with(demo, filings=FakeFilings(), search=FakeSearch())
    async with demo.container.sessions() as s:
        inst = await s.scalar(select(Instrument).where(Instrument.symbol == "DEMOIT03"))
        fetched = await concall.fetch_from_nse(c, s, inst)
        assert fetched["found"] == 2 and all(t["fetched"] for t in fetched["stored"])
        rows = (await s.scalars(select(ConcallTranscript).where(ConcallTranscript.instrument_id == inst.id))).all()
        assert {(t.fiscal_year, t.quarter) for t in rows} >= {(2027, 1), (2027, 2)}
        assert all(t.source_url.startswith("https://nsearchives.nseindia.com/") for t in rows if t.fiscal_year == 2027)
        again = await concall.fetch_from_nse(c, s, inst)  # already stored: not downloaded again
        assert not any(t["fetched"] for t in again["stored"])
        out = await concall.analyze(c, s, inst)
    assert out["mode"] == "heuristic" and out["web"]["queries"] > 0 and out["web"]["results"] > 0
    debt = next(a for a in out["assessments"] if "net debt" in a["commitment"])
    assert debt["evidence_source"] == "web" and debt["web_sources"][0]["url"] == "https://news.test/debt" and debt["grounded"]
    plant = next(a for a in out["assessments"] if "Pune plant" in a["commitment"])
    assert plant["evidence_source"] == "transcript" and plant["verdict"] == "delivered"


def test_a_verdict_citing_a_web_source_that_was_not_retrieved_is_excluded():
    cm = concall.Commitment(id="C1", commitment="Reduce debt by 500 crore", quote="We will reduce net debt by 500 crore this year")
    a = concall.Assessed(id="C1", verdict="delivered", web_source_ids=["W9"], rationale="news says so")
    rows, ft = concall.assemble([cm], [a], "We will reduce net debt by 500 crore this year.", "Nothing relevant.", {})
    assert rows[0]["grounded"] is False and "not among the retrieved results" in rows[0]["grounding_note"]


def test_pdf_ligatures_curly_quotes_and_line_breaks_do_not_break_verbatim_quotes():
    pdf_text = "We remain conﬁdent of “signiﬁcant” AI-\nled growth – and margin expansion."
    assert cs.quote_is_grounded('We remain confident of "significant" AI-led growth - and margin expansion', pdf_text)


async def test_auto_endpoint_runs_fetch_and_analysis_as_a_job(demo, monkeypatch):
    monkeypatch.setattr(concall, "extract_pdf_text", lambda b: b.decode())
    monkeypatch.setattr(demo.container, "filings", FakeFilings())
    r = await demo.client.post(API + "/instruments/NSE:DEMOIT04/concalls/auto")
    assert r.status_code == 202, r.text
    st = await _job(demo, r.json()["job_id"])
    assert st["status"] == "succeeded", st
    assert st["detail"]["fetched"]["found"] == 2 and st["detail"]["current"] == "Q2 FY2027" and st["detail"]["previous"] == "Q1 FY2027"
    lst = (await demo.client.get(API + "/instruments/NSE:DEMOIT04/concalls")).json()
    assert lst["latest"]["current"]["label"] == "Q2 FY2027" and lst["nse_filings"]["available"] is True


async def test_auto_endpoint_explains_when_filings_are_not_configured(demo):
    r = await demo.client.post(API + "/instruments/NSE:DEMOIT04/concalls/auto")
    assert r.status_code == 409 and "NSE_FILINGS_BASE_URL" in r.json()["error"]["message"] or "demo" in r.json()["error"]["message"]


# ------------------------------------------------------------ sector rotation and fundamentals
def test_every_sector_with_history_is_plotted_and_thin_composites_are_labelled():
    idx = pd.bdate_range("2025-01-01", periods=320)
    s = lambda k: pd.Series(100 * (1 + 0.001 * k) ** pd.RangeIndex(len(idx)), index=idx, dtype=float)  # noqa: E731
    insts = [SimpleNamespace(id=1, symbol="AAA", instrument_type="EQ", sector="Telecom", sector_index_symbol=None),
             SimpleNamespace(id=2, symbol="BBB", instrument_type="EQ", sector="Banks", sector_index_symbol=None),
             SimpleNamespace(id=3, symbol="CCC", instrument_type="EQ", sector="Banks", sector_index_symbol=None),
             SimpleNamespace(id=4, symbol="DDD", instrument_type="EQ", sector="Banks", sector_index_symbol=None)]
    bars = {i.id: pd.DataFrame({"close": s(i.id)}) for i in insts}
    series = sector_rotation.build_series(insts, bars, "NIFTY 50")  # type: ignore[arg-type]
    assert series["sectors"]["Telecom"]["kind"] == "composite" and "AAA only" in series["sectors"]["Telecom"]["note"]
    assert "thin proxy" not in series["sectors"]["Banks"]["note"]
    payload = sector_rotation.compute(series, "NIFTY 50")
    assert payload["coverage"]["sectors_plotted"] == payload["coverage"]["sectors_total"] == 2


def test_valuation_uses_trailing_twelve_months_when_quarters_are_newer_than_the_annual_report():
    periods = [{"period_end": "2026-03-31", "period_type": "annual", "revenue": 1000.0, "pat": 100.0, "eps": 10.0, "equity": 500.0,
                "shares_outstanding": 10.0},
               *[{"period_end": d, "period_type": "quarterly", "eps": e, "shares_outstanding": 10.0}
                 for d, e in (("2025-09-30", 2.0), ("2025-12-31", 2.5), ("2026-03-31", 3.0), ("2026-06-30", 3.5))]]
    out, meta = metrics.fundamentals_for(periods, "industrial", 220.0)
    assert out["pe"].value == pytest.approx(220.0 / 11.0) and meta["earnings_basis"].startswith("TTM to 2026-06-30")


# ------------------------------------------------------------ broker login at the start
async def test_first_authorised_check_starts_the_broker_backfill(demo, mock_state):
    mock_state.kite_logged_in = False
    r = await demo.client.post(API + "/sources/broker/login")
    assert r.json()["kite_mcp"]["state"] == "login_required"
    mock_state.kite_logged_in = True
    r = await demo.client.post(API + "/sources/broker/login")
    k = r.json()["kite_mcp"]
    assert k["state"] == "authorized" and isinstance(k["backfill_job"], int)
    st = await _job(demo, k["backfill_job"], 120)
    assert st["kind"] == "broker_backfill" and st["status"] == "succeeded", st
    again = (await demo.client.post(API + "/sources/broker/login")).json()["kite_mcp"]
    assert "backfill_job" not in again  # only the transition to authorised starts it


async def test_model_verdict_based_on_a_retrieved_web_source_is_kept_with_its_link(demo, monkeypatch):
    monkeypatch.setattr(concall, "extract_pdf_text", lambda b: b.decode())
    q_debt = "We will reduce net debt by 500 crore this year through internal accruals"
    q_plant = "We expect to commission the new Pune plant by the end of Q2"
    q_margin = "we are targeting EBITDA margin of 22 percent for the full year"
    replies = iter([
        {"commitments": [{"id": "C1", "commitment": "Reduce net debt by 500 crore", "category": "debt", "importance": "high", "quote": q_debt},
                         {"id": "C2", "commitment": "Commission Pune plant by Q2", "category": "capacity", "importance": "high", "quote": q_plant},
                         {"id": "C3", "commitment": "EBITDA margin 22% for FY", "category": "margin", "importance": "medium", "quote": q_margin}]},
        {"assessments": [{"id": "C1", "verdict": "delivered", "evidence_quote": None, "web_source_ids": ["W1"], "rationale": "News reports it done."},
                         {"id": "C2", "verdict": "delivered", "evidence_quote": "The Pune plant was commissioned in August", "rationale": "Said on call."},
                         {"id": "C3", "verdict": "in_progress", "evidence_quote": "Margins were 20.5 percent this quarter", "rationale": "Below target."}]},
    ])
    prompts: list[str] = []

    def h(req):
        prompts.append(json.loads(req.content)["messages"][-1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(next(replies))}}]})

    llm = ChatCompletionsLLM(api_key="sk-test0000", base_url="https://llm.test/v1", model_id="m", enabled=True, max_retries=0,
                             transport=httpx.MockTransport(h))
    c = _with(demo, filings=FakeFilings(), search=FakeSearch(), llm=llm)
    async with demo.container.sessions() as s:
        inst = await s.scalar(select(Instrument).where(Instrument.symbol == "DEMOFIN01"))
        await concall.fetch_from_nse(c, s, inst)
        out = await concall.analyze(c, s, inst)
    assert out["mode"] == "llm" and "<WEB>" in prompts[1] and "W1" in prompts[1]
    by = {a["id"]: a for a in out["assessments"]}
    assert by["C1"]["evidence_source"] == "web" and by["C1"]["grounded"] and by["C1"]["web_sources"][0]["url"] == "https://news.test/debt"
    assert by["C2"]["evidence_source"] == "transcript" and by["C3"]["grounded"]
    assert out["score"]["assessed"] == 3 and out["score"]["value"] is not None


# ------------------------------------------------------------ Kite login link is always fresh
async def test_login_link_still_works_after_kite_forgets_the_mcp_session(demo, mock_state):
    """Reported 19 Oct 2026: after the API had run for days, "Connect Kite" got no link. Kite had dropped the
    idle MCP session and answered HTTP 400 "Invalid session ID"; the client read that as a tool error and never
    reconnected, so get_profile and login failed until a restart."""
    mock_state.kite_logged_in = False
    first = (await demo.client.post(API + "/sources/broker/login-link")).json()["kite_mcp"]
    assert first["state"] == "login_required" and first["login_url"]
    old_session = demo.container.kite.client._session_id
    mock_state.kite_forget_session = True  # the server forgets the session, as mcp.kite.trade does
    clicked = (await demo.client.post(API + "/sources/broker/login-link")).json()["kite_mcp"]
    assert clicked["state"] == "login_required" and clicked["login_url"] and clicked["login_url"] != first["login_url"]
    assert "reason" not in clicked and demo.container.kite.client._session_id != old_session
    async with httpx.AsyncClient() as h:  # the new link authorises the new session
        assert (await h.get(clicked["login_url"])).status_code == 200
    assert (await demo.client.post(API + "/sources/broker/login")).json()["kite_mcp"]["state"] == "authorized"


async def test_a_failed_login_tool_explains_why_there_is_no_link(demo, mock_state, monkeypatch):
    from app.connectors.mcp_client import McpUnavailable

    mock_state.kite_logged_in = False
    k = demo.container.kite
    real = k.client.call

    async def call(tool, args=None):
        if tool == "login":
            raise McpUnavailable("kite_mcp returned HTTP 503")
        return await real(tool, args)

    monkeypatch.setattr(k.client, "call", call)
    out = (await demo.client.post(API + "/sources/broker/login-link")).json()["kite_mcp"]
    assert out["login_url"] is None and out["issued_at"] is None and "HTTP 503" in out["reason"]


async def test_login_link_is_issued_on_click_and_an_expired_link_is_never_served(demo, mock_state):
    from app.connectors import kite_mcp

    mock_state.kite_logged_in = False
    status = (await demo.client.post(API + "/sources/broker/login")).json()["kite_mcp"]
    assert status["state"] == "login_required" and not status["login_url"], "a status check issues no link"
    # Clicking "open the login page" asks Kite for a new link each time.
    first = (await demo.client.post(API + "/sources/broker/login-link")).json()["kite_mcp"]
    clicked = (await demo.client.post(API + "/sources/broker/login-link")).json()["kite_mcp"]
    assert first["login_url"] and clicked["login_url"] != first["login_url"]
    # Reported 19 Oct 2026: background checks replaced the link while the user was still approving it in Kite.
    # A status check, even after the link has aged, must leave the link the user opened alone.
    k = demo.container.kite
    k._link_issued_at = datetime.now(UTC) - timedelta(seconds=kite_mcp.LOGIN_LINK_MAX_AGE_SECONDS + 5)
    stale = k._pending_login_url
    again = (await demo.client.post(API + "/sources/broker/login")).json()["kite_mcp"]
    assert again["login_url"] == stale and k._pending_login_url == stale
    async with httpx.AsyncClient() as h:
        # An old link is rejected by the server exactly as Kite does ...
        mock_state.link_ttl_seconds = 0.0
        await asyncio.sleep(0.05)
        bad = await h.get(stale)
        assert bad.status_code == 400 and "invalid authorize session" in bad.text
        mock_state.link_ttl_seconds = 300.0
        # ... while the link handed out on click authorises the session.
        fresh = (await demo.client.post(API + "/sources/broker/login-link")).json()["kite_mcp"]["login_url"]
        ok = await h.get(fresh)
        assert ok.status_code == 200
    done = (await demo.client.post(API + "/sources/broker/login")).json()["kite_mcp"]
    assert done["state"] == "authorized" and isinstance(done.get("backfill_job"), int)
    await _job(demo, done["backfill_job"], 120)
    after = (await demo.client.post(API + "/sources/broker/login-link")).json()["kite_mcp"]
    assert after["state"] == "authorized" and not after.get("login_url")


async def test_model_cannot_add_stocks_the_question_did_not_name(demo):
    replies = iter([{"intent": "compare", "symbols": ["DEMOIT01", "DEMOFIN02", "DEMOAUTO01"]},
                    {"answer": "ranked [P1].", "points": [], "caveats": []}])

    def h(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(next(replies))}}]})

    llm = ChatCompletionsLLM(api_key="sk-test0000", base_url="https://llm.test/v1", model_id="m", enabled=True, max_retries=0,
                             transport=httpx.MockTransport(h))
    async with demo.container.sessions() as s:
        d = await ask.ask(_with(demo, llm=llm), s, "strong bullish stocks in last 3 days")
    assert d["plan"]["intent"] == "movers" and d["plan"]["lookback_days"] == 3 and d["plan"]["symbols"] == []
    assert any("not named in the question" in n for n in d["plan_notes"]) and d["movers"]


# ------------------------------------------------------------ whole market: search and live candidates
async def test_search_ranks_the_exact_symbol_first(demo):
    d = (await demo.client.get(API + "/instruments", params={"q": "demoit01", "page_size": 5})).json()
    assert d["items"][0]["symbol"] == "DEMOIT01"


async def test_movers_fetch_live_prices_only_for_the_leading_candidates(demo, monkeypatch):
    asked: list[int] = []
    real = ask.live_prices

    async def spy(c, symbols):
        asked.append(len(symbols))
        return await real(c, symbols)

    monkeypatch.setattr(ask, "live_prices", spy)
    monkeypatch.setattr(ask, "LIVE_CANDIDATES", 5)
    async with demo.container.sessions() as s:
        insts = await ask._universe(s)
        ev, rows = await ask.movers(demo.container, s, ask.AskPlan(intent="movers", lookback_days=3, limit=1), insts)
    eq = [i for i in insts if i.instrument_type == "EQ"]
    assert asked == [5] and len(eq) > 5 and ev["data"]["ranked"] == len(eq)
    assert "leading 5 are re-ranked" in ev["data"]["method"] and rows[0]["live"]


async def test_filings_throttling_pauses_and_resumes_instead_of_abandoning_the_run(demo):
    from app.connectors.base import ProviderUnavailable
    from app.core.resilience import FakeClock
    from app.services import ingestion

    class Throttled:
        available, breaker_reset_seconds = True, 120.0

        def __init__(self):
            self.calls, self.state = 0, "closed"

        def status(self):
            return {"reason": None, "breaker": self.state}

        async def statements(self, symbol):
            self.calls += 1
            if self.calls <= 2:  # NSE times out: the breaker opens
                self.state = "open"
                raise ProviderUnavailable("NSE filings request failed: ReadTimeout", "unavailable", "nse_filings")
            self.state = "closed"
            return None  # no filings for this demo symbol: counted, not failed

    clock = FakeClock()
    f = Throttled()
    c = _with(demo, filings=f, clock=clock, settings=demo.container.settings.model_copy(update={"nse_filings_max_companies_per_run": 3}))
    async with demo.container.sessions() as s:
        insts = list((await s.scalars(select(Instrument).where(Instrument.instrument_type == "EQ"))).all())[:3]
        out = await ingestion.ingest_filings(c, s, insts)
    assert out["pauses"] == 2 and "stopped" not in out and out["without_filings"] == 3 and not out["failed"]
