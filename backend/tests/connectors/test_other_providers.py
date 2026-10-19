"""Mocked contract tests: fundamentals files and web search. (The LLM client: test_llm_provider.py.)
No test here calls a paid or external service."""

import json

import httpx
import pytest

from app.connectors.base import ProviderUnavailable
from app.connectors.fundamentals_file import FileFundamentalsProvider, FundamentalsValidationError
from app.connectors.web_search import BraveSearch, DdgsSearch, NullSearch, build_search
from app.devtools import demo_data as dd

# ------------------------------------------------------------ fundamentals files

def test_fundamentals_file_roundtrip_and_validation(tmp_path):
    p = FileFundamentalsProvider(tmp_path, allow_demo=True)
    assert p.get_statements("DEMOIT01") is None
    assert p.status()["instruments_with_data"] == 0
    p.save("DEMOIT01", dd.fundamentals("DEMOIT01"))
    got = p.get_statements("demoit01")
    assert got["currency"] == "INR" and len(got["periods"]) == 5 and got["is_demo"] is True
    assert got["ingested_at"] and p.status()["instruments_with_data"] == 1


def test_fundamentals_rejects_bad_payloads(tmp_path):
    p = FileFundamentalsProvider(tmp_path, allow_demo=True)
    good = dd.fundamentals("DEMOIT01")
    for mutate, msg in [
        (lambda d: d.update(unit="lakh-ish"), "unit"),
        (lambda d: d["periods"].append(dict(d["periods"][0])), "duplicate"),
        (lambda d: d["periods"][0].update(revenue="abc"), "revenue"),
        (lambda d: d.update(periods=[]), "period"),
        (lambda d: d.update(symbol="../../etc/passwd"), "symbol"),
    ]:
        bad = json.loads(json.dumps(good))
        mutate(bad)
        with pytest.raises(FundamentalsValidationError) as e:
            p.save(bad.get("symbol", "X") if "/" not in str(bad.get("symbol")) else "X", bad)
        assert msg in str(e.value).lower()


def test_live_mode_refuses_demo_fundamentals(tmp_path):
    p = FileFundamentalsProvider(tmp_path, allow_demo=False)
    with pytest.raises(FundamentalsValidationError):
        p.save("DEMOIT01", dd.fundamentals("DEMOIT01"))
    (tmp_path / "DEMOIT01.json").write_text(json.dumps(dd.fundamentals("DEMOIT01")))
    assert p.get_statements("DEMOIT01") is None, "a demo file on disk is ignored in live mode"


# ------------------------------------------------------------ web search

async def test_no_search_provider_reports_unavailable():
    s = build_search("none", None)
    assert isinstance(s, NullSearch) and s.available is False
    assert await s.search("INFY results") == []
    assert "WEB_SEARCH_PROVIDER" in s.status()["reason"]


def test_ddgs_is_the_keyless_provider_and_tavily_is_gone():
    assert isinstance(build_search("ddgs", None), DdgsSearch) and build_search("ddgs", None).available is True
    assert isinstance(build_search("tavily", "k"), NullSearch), "Tavily is no longer supported"
    assert isinstance(build_search("brave", None), NullSearch), "Brave still needs a key"
    assert "not supported" in build_search("tavily", "k").status()["reason"]
    from app.core.config import Settings

    assert Settings(_env_file=None, web_search_provider="tavily").web_search_provider == "tavily", "a stale .env value must not stop start-up"
    off = build_search("ddgs", None, disabled_reason="Demo symbols are fictional")
    assert isinstance(off, NullSearch) and off.status()["reason"] == "Demo symbols are fictional"


class FakeDDGS:
    """Stands in for ddgs.DDGS; returns rows in the shape the real library returned on 2026-10-12."""

    calls: list = []

    def __init__(self, news=None, text=None, error=None):
        self._news, self._text, self._error = news or [], text or [], error

    def news(self, query, **kw):
        FakeDDGS.calls.append(("news", query, kw))
        if self._error:
            raise self._error
        return self._news

    def text(self, query, **kw):
        FakeDDGS.calls.append(("text", query, kw))
        return self._text


NEWS = [
    {"date": "2026-10-08T10:03:36+00:00", "title": "Outcome of <b>Board</b> Meeting", "body": "Board approved results", "url": "https://www.nseindia.com/x", "image": "", "source": "NSE"},
    {"date": "", "title": "Infosys Q2", "body": "Revenue grew", "url": "https://news.example.com/a", "source": "Example"},
    {"date": "2026-10-01T00:00:00+00:00", "title": "bad", "body": "x", "url": "javascript:alert(1)"},
    {"date": "2026-10-01T00:00:00+00:00", "title": "internal", "body": "x", "url": "http://169.254.169.254/latest"},
]


async def test_ddgs_news_results_are_normalised_and_classified():
    FakeDDGS.calls = []
    s = DdgsSearch(client_factory=lambda: FakeDDGS(news=NEWS))
    out = await s.search("INFY results", max_results=5)
    assert FakeDDGS.calls == [("news", "INFY results", {"max_results": 5, "region": "in-en"})], "text search is not needed when news returns rows"
    assert [r["url"] for r in out] == ["https://www.nseindia.com/x", "https://news.example.com/a"], "unsafe and internal URLs are dropped"
    assert out[0]["source_type"] == "primary" and out[1]["source_type"] == "secondary"
    assert out[0]["title"] == "Outcome of Board Meeting" and out[0]["snippet"] == "Board approved results"
    assert out[0]["published_at"] == "2026-10-08" and out[1]["published_at"] is None, "day-level date only; no invented time of day"
    assert out[0]["id"] == "S1" and out[0]["provider"] == "ddgs" and out[0]["retrieved_at"]
    assert s.status()["state"] == "ok" and s.status()["last_success_at"]


async def test_ddgs_orders_newest_first_and_reports_dates_in_market_time():
    rows = [{"date": "2026-04-22T18:30:00+00:00", "title": "Old", "body": "b", "url": "https://a.example.com/old"},
            {"date": "", "title": "Undated", "body": "b", "url": "https://a.example.com/undated"},
            {"date": "2026-10-09T10:24:20+00:00", "title": "New", "body": "b", "url": "https://a.example.com/new"}]
    out = await DdgsSearch(client_factory=lambda: FakeDDGS(news=rows)).search("x")
    assert [(r["id"], r["title"], r["published_at"]) for r in out] == [("S1", "New", "2026-10-09"), ("S2", "Old", "2026-04-23"), ("S3", "Undated", None)]
    # 18:30 UTC is already the next day in India


async def test_ddgs_falls_back_to_text_search_when_news_is_empty():
    FakeDDGS.calls = []
    s = DdgsSearch(client_factory=lambda: FakeDDGS(text=[{"title": "Investors", "href": "https://www.infosys.com/investors.html", "body": "Financial results"}]))
    out = await s.search("INFY results")
    assert [c[0] for c in FakeDDGS.calls] == ["news", "text"]
    assert out[0]["url"] == "https://www.infosys.com/investors.html" and out[0]["published_at"] is None


async def test_ddgs_failure_raises_unavailable_not_empty_success():
    from ddgs.exceptions import RatelimitException

    s = DdgsSearch(client_factory=lambda: FakeDDGS(error=RatelimitException("202 Ratelimit")))
    with pytest.raises(ProviderUnavailable) as e:
        await s.search("x")
    assert "rate limit" in str(e.value).lower() and s.status()["state"] == "degraded"


async def test_search_failure_raises_unavailable_not_empty_success():
    s = BraveSearch("k", url="https://search.example/news", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ProviderUnavailable):
        await s.search("x")


def test_brave_endpoint_comes_from_settings_not_code():
    assert "WEB_SEARCH_BASE_URL" in build_search("brave", "key").status()["reason"]
    assert build_search("brave", "key", base_url="https://search.example/news").name == "brave"


async def test_brave_calls_the_configured_endpoint():
    seen = {}

    def h(req):
        seen["url"] = str(req.url.copy_with(query=None))
        return httpx.Response(200, json={"results": []})

    s = BraveSearch("k", url="https://search.example/news", transport=httpx.MockTransport(h))
    assert await s.search("infy") == [] and seen["url"] == "https://search.example/news"
