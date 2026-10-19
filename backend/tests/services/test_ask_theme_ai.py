"""Ask-the-market, AI theme refinement, fetch-latest refreshes and the bounded overview.
All market data is DEMO synthetic data; the LLM is a mocked HTTP transport (no paid inference)."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.api.v1 import market as market_routes
from app.connectors.llm import ChatCompletionsLLM
from app.db.models import Theme
from app.services import ask, theme_ai

pytestmark = pytest.mark.usefixtures("broker_logged_in")
API = "/api/v1"


def _llm(replies):
    it = iter(replies)

    def h(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(next(it))}}],
                                         "usage": {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600}})

    return ChatCompletionsLLM(api_key="sk-test0000", base_url="https://llm.test/v1", model_id="z-ai/glm-5.3-flash", enabled=True, transport=httpx.MockTransport(h), max_retries=0)


def _failing_llm(status=402):
    return ChatCompletionsLLM(api_key="sk-test0000", base_url="https://llm.test/v1", model_id="z-ai/glm-5.3-flash", enabled=True, max_retries=0,
                          transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "You have no remaining credits"})))


def _with(demo, **over):
    c = demo.container
    return SimpleNamespace(**{**c.__dict__, "benchmark_symbol": c.benchmark_symbol, **over})


class DownMarketData:
    """Every live quote fails, as when the NSE and broker MCP servers are unreachable."""

    async def quote(self, symbol, instrument_type="EQ"):
        return {"symbol": symbol, "status": "unavailable", "fields": {}, "sources": []}


async def _job(demo, job_id, limit_s=60.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit_s:
        st = (await demo.client.get(f"{API}/jobs/{job_id}")).json()
        if st["status"] != "running":
            return st
        await asyncio.sleep(0.1)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------ ask: interpretation
async def test_rules_plan_reads_symbols_conditions_and_market_questions(demo):
    async with demo.container.sessions() as s:
        insts = await ask._universe(s)
    p = ask.rule_plan("How is DEMOIT01 doing compared with DEMOFIN02?", insts)
    assert p.intent == "compare" and set(p.symbols) == {"DEMOIT01", "DEMOFIN02"}
    p = ask.rule_plan("Show me low volatility banks", insts)
    assert p.intent == "screen" and any(f.metric == "volatility_252d" for f in p.filters) and p.sectors == ["Financial Services"]
    p = ask.rule_plan("What are the top gainers this month?", insts)
    assert p.intent == "movers" and p.lookback_days == 21 and p.sort_direction == "desc"
    assert ask.rule_plan("How is the market today?", insts).intent == "market"
    assert ask.rule_plan("Tell me a joke", insts).intent == "unknown"


async def test_sanitize_drops_unknown_symbols_metrics_and_sectors(demo):
    async with demo.container.sessions() as s:
        insts = await ask._universe(s)
    raw = ask.AskPlan(intent="instrument", symbols=["NSE:DEMOIT01", "MADEUP"], sectors=["Moon Mining"], sort_metric="vibes",
                      filters=[ask.PlanFilter(metric="roe", op="gte", value=15), ask.PlanFilter(metric="secret_sauce", op="gt", value=1)])
    p, notes = ask.sanitize_plan(raw, insts)
    assert p.symbols == ["DEMOIT01"] and [f.metric for f in p.filters] == ["roe"] and p.sectors == [] and p.sort_metric is None
    assert len(notes) == 4 and any("MADEUP" in n for n in notes)


# ------------------------------------------------------------ ask: data from MCP, fallback to the database
async def test_ask_uses_live_mcp_quote_and_stored_metrics_without_llm(demo):
    async with demo.container.sessions() as s:
        d = await ask.ask(_with(demo), s, "How is DEMOIT01 doing?")
    assert d["plan"]["intent"] == "instrument" and d["plan_source"] == "rules" and d["answer_source"] == "rules"
    assert d["llm"]["used"] is False and "LLM_ENABLED" in d["llm"]["reason_not_used"]
    q = next(e for e in d["evidence"] if e["id"] == "Q1")
    assert q["live"] is True and "live MCP" in q["source"] and q["data"]["last_price"] > 0
    m = next(e for e in d["evidence"] if e["id"] == "M1")
    assert m["source"].startswith("system database") and "return_3m" in m["data"]
    assert d["data_sources"]["live_mcp"] >= 1 and "DEMOIT01" in d["answer"]["answer"]


async def test_ask_falls_back_to_database_when_mcp_is_down(demo):
    async with demo.container.sessions() as s:
        d = await ask.ask(_with(demo, market_data=DownMarketData()), s, "Quote for DEMOFIN01 please")
    q = next(e for e in d["evidence"] if e["id"] == "Q1")
    assert q["live"] is False and q["source"] == "system database (last ingested bar)"
    assert q["as_of"] and q["data"]["last_price"] > 0 and "Live quote was unavailable" in q["note"]
    assert any("Live quote was unavailable" in c for c in d["answer"]["caveats"]) and "stored close" in d["answer"]["answer"]


async def test_ask_screen_question_returns_ranked_table_from_database(demo):
    async with demo.container.sessions() as s:
        d = await ask.ask(_with(demo), s, "Which stocks have the lowest volatility?")
    assert d["plan"]["intent"] == "screen" and d["plan"]["sort_metric"] == "volatility_252d"
    vols = [r["values"]["volatility_252d"] for r in d["table"]]
    assert vols and vols == sorted(vols)
    s1 = next(e for e in d["evidence"] if e["id"] == "S1")
    assert s1["data"]["matched"] >= len(vols) and s1["source"].startswith("system database")


async def test_ask_with_llm_plans_validates_and_drops_uncited_points(demo):
    plan = {"intent": "instrument", "symbols": ["DEMOIT02", "INVENTED"], "filters": [], "sectors": [], "sort_metric": None,
            "sort_direction": "desc", "limit": 10}
    answer = {"answer": "DEMOIT02 trades near its stored levels.",
              "points": [{"text": "Live quote retrieved", "evidence_ids": ["Q1"]}, {"text": "Made-up claim", "evidence_ids": ["X9"]}],
              "caveats": []}
    llm = _llm([plan, answer])
    async with demo.container.sessions() as s:
        d = await ask.ask(_with(demo, llm=llm), s, "Tell me about DEMOIT02")
    assert d["plan_source"] == "llm" and d["answer_source"] == "llm" and d["llm"]["used"] and d["llm"]["model_id"] == "z-ai/glm-5.3-flash"
    assert d["plan"]["symbols"] == ["DEMOIT02"] and any("INVENTED" in n for n in d["plan_notes"])
    assert [p["text"] for p in d["answer"]["points"]] == ["Live quote retrieved"]
    assert d["llm"]["uncited_points_removed"] == ["Made-up claim"] and llm.status()["usage"]["total_tokens"] == 1200


async def test_ask_falls_back_to_rules_when_llm_has_no_credits(demo):
    async with demo.container.sessions() as s:
        d = await ask.ask(_with(demo, llm=_failing_llm(402)), s, "How is DEMOIT03 doing?")
    assert d["plan_source"] == "rules" and d["answer_source"] == "rules" and "402" in d["llm"]["reason_not_used"]
    assert d["plan"]["symbols"] == ["DEMOIT03"] and d["answer"]["points"]


async def test_ask_api_validates_and_audits(demo):
    r = await demo.client.post(API + "/ask", json={"question": "Top gainers in Information Technology"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["plan"]["intent"] == "movers" and d["plan"]["sectors"] == ["Information Technology"] and d["movers"] and d["disclaimer"]
    assert all(row["sector"] == "Information Technology" for row in d["movers"])
    assert (await demo.client.post(API + "/ask", json={"question": "x"})).status_code == 422
    ev = (await demo.client.get(API + "/audit/events")).json()["items"]
    assert any(e["action"] == "ask" for e in ev)


# ------------------------------------------------------------ themes: AI filter
async def test_theme_ai_filter_rules_narrows_members_deterministically(demo):
    async with demo.container.sessions() as s:
        t = await s.scalar(select(Theme).where(Theme.slug == "price-momentum"))
        base = await theme_ai.refine(_with(demo), s, t, "show everything")
        d = await theme_ai.refine(_with(demo), s, t, "only banks")
    assert base["source"] == "rules" and base["members"]
    assert d["sectors"] == ["Financial Services"] and all(m["sector"] == "Financial Services" for m in d["members"])
    assert len(d["members"]) <= len(base["members"]) and d["saved"] is False
    assert d["explanation"] == "Restricted to Financial Services" and "No recognised" not in d["explanation"]


async def test_theme_ai_filter_with_llm_ignores_unknown_metrics(demo):
    reply = {"filters": [{"metric": "volatility_252d", "op": "lt", "value": 60, "value2": None},
                         {"metric": "astrology_score", "op": "gt", "value": 1, "value2": None}],
             "sectors": [], "rank_by": "volatility_252d", "rank_direction": "asc", "limit": 5, "explanation": "Calmer names first"}
    async with demo.container.sessions() as s:
        t = await s.scalar(select(Theme).where(Theme.slug == "low-volatility"))
        d = await theme_ai.refine(_with(demo, llm=_llm([reply])), s, t, "the five calmest")
    assert d["source"] == "llm" and d["explanation"] == "Calmer names first" and len(d["members"]) <= 5
    assert any("astrology_score" in n for n in d["notes"])
    vols = [m["values"]["volatility_252d"] for m in d["members"]]
    assert vols == sorted(vols) and all(v < 60 for v in vols)


async def test_theme_ai_filter_api(demo):
    r = await demo.client.post(API + "/themes/low-volatility/ai-filter", json={"query": "low debt"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert any("Debt / equity" in f for f in d["added_filters"]) and d["metrics_as_of"]
    assert all(m["values"]["debt_equity"] < 0.5 for m in d["members"])


# ------------------------------------------------------------ refresh buttons
async def test_builtin_themes_have_members_after_full_refresh(demo):
    for slug in ("price-momentum", "low-volatility"):
        d = (await demo.client.get(f"{API}/themes/{slug}")).json()
        assert d["snapshot"] and d["snapshot"]["constituents"], slug


async def test_theme_refresh_fetches_latest_prices_then_reranks_and_keeps_history(demo):
    before = (await demo.client.get(f"{API}/themes/price-momentum/snapshots")).json()["items"]
    j = (await demo.client.post(f"{API}/themes/price-momentum/refresh")).json()
    st = await _job(demo, j["job_id"])
    assert st["status"] == "succeeded", st
    assert st["detail"]["prices"]["ok"] > 0 and st["detail"]["metrics"]["status"] == "ok"
    after = (await demo.client.get(f"{API}/themes/price-momentum/snapshots")).json()["items"]
    ids_before, ids_after = {x["id"] for x in before}, {x["id"] for x in after}
    assert ids_after == ids_before | {st["detail"]["snapshot_id"]} and st["detail"]["snapshot_id"] not in ids_before
    kept = {x["id"]: x for x in after}
    assert all(kept[x["id"]]["top"] == x["top"] and kept[x["id"]]["created_at"] == x["created_at"] for x in before)


async def test_instrument_refresh_job(demo):
    j = (await demo.client.post(f"{API}/instruments/NSE:DEMOIT01/refresh")).json()
    st = await _job(demo, j["job_id"])
    assert st["status"] == "succeeded" and st["detail"]["prices"]["instruments"] == 1 and st["detail"]["prices"]["ok"] == 1


async def test_overview_never_waits_long_on_a_slow_breadth_source(demo, monkeypatch):
    class SlowNse:
        async def market_breadth(self):
            await asyncio.sleep(30)

    monkeypatch.setattr(market_routes, "BREADTH_TIMEOUT_SECONDS", 0.2)
    c = _with(demo, nse=SlowNse(), cache=demo.container.cache)
    await demo.container.cache.delete_prefix("overview:")
    t0 = time.monotonic()
    m = await market_routes._market_breadth(c, fresh=True)
    assert time.monotonic() - t0 < 2 and m["state"] == "unavailable" and "did not answer" in m["reason"]
    hit = await market_routes._market_breadth(c)
    assert hit["cache"] == "hit" and hit["state"] == "unavailable"
    await demo.container.cache.delete_prefix("overview:")


async def test_overview_page_load_never_waits_for_breadth(demo, monkeypatch):
    class SlowNse:
        async def market_breadth(self):
            await asyncio.sleep(0.3)
            return {"data": {"advances": 10, "declines": 5}, "source_id": "t", "provider": "nse_mcp", "retrieved_at": "x", "is_demo": True}

    c = _with(demo, nse=SlowNse(), cache=demo.container.cache)
    await demo.container.cache.delete_prefix("overview:")
    t0 = time.monotonic()
    first = await market_routes._market_breadth(c)
    assert time.monotonic() - t0 < 0.1 and first["state"] == "checking"
    await asyncio.sleep(0.5)
    later = await market_routes._market_breadth(c)
    assert later["state"] == "ok" and later["cache"] == "hit" and later["data"]["advances"] == 10
    await demo.container.cache.delete_prefix("overview:")
