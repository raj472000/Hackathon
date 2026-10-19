"""The LLM client: one OpenAI-compatible client whose provider is chosen only by settings.

Every HTTP call is a mocked transport; no paid inference runs here. Two different providers are
configured purely through settings (an OpenRouter-style gateway and a self-hosted server) to show
that switching provider needs no code change."""

import json
import logging

import httpx
import pytest
from pydantic import BaseModel

from app.connectors.llm import (
    ChatCompletionsLLM,
    LLMBudgetExceeded,
    LLMInvalidOutput,
    LLMUnavailable,
    build_llm,
)
from app.core.config import Settings
from app.core.container import _secret, build_container
from app.observability.logging import redact

KEY = "sk-test-0123456789abcdef0123456789abcdef"
BASE = "https://gateway.example/api/v1"
MODEL = "z-ai/glm-5.3-flash"


class Out(BaseModel):
    answer: str
    score: int


def _resp(content, usage=None):
    return httpx.Response(200, json={"id": "gen-1", "choices": [{"message": {"role": "assistant", "content": content}}],
                                     "usage": usage or {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100}})


def client(handler, **kw):
    kw.setdefault("max_retries", 0)
    kw.setdefault("daily_budget", 10)
    return ChatCompletionsLLM(api_key=KEY, base_url=BASE, model_id=MODEL, enabled=True, transport=httpx.MockTransport(handler), **kw)


# ------------------------------------------------------------ configuration
def test_nothing_provider_specific_is_built_in():
    s = Settings(_env_file=None)
    assert s.llm_base_url is None and s.llm_api_key is None and s.llm_model_id is None and s.llm_enabled is False
    llm = build_llm(s, _secret)
    assert llm.base_url is None and llm.available is False and llm.label == "LLM"


@pytest.mark.parametrize("missing,reason", [
    ({"enabled": False}, "LLM_ENABLED is false"),
    ({"base_url": None}, "LLM_BASE_URL is not set"),
    ({"api_key": None}, "LLM_API_KEY is not set"),
    ({"model_id": None}, "LLM_MODEL_ID is not set"),
])
def test_unavailable_reasons_name_the_setting_to_fill_in(missing, reason):
    kw = {"api_key": KEY, "base_url": BASE, "model_id": MODEL, "enabled": True, **missing}
    m = ChatCompletionsLLM(**kw)
    assert m.available is False and m.status()["reason"] == reason


def test_settings_from_environment_select_the_provider(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER_NAME", "OpenRouter")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.setenv("LLM_MODEL_ID", MODEL)
    monkeypatch.setenv("LLM_EXTRA_HEADERS", '{"HTTP-Referer": "https://markets.example", "X-Title": "Markets Intelligence"}')
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"usage": {"include": true}}')
    s = Settings(_env_file=None)
    assert s.llm_extra_headers == {"HTTP-Referer": "https://markets.example", "X-Title": "Markets Intelligence"}
    assert s.llm_extra_body == {"usage": {"include": True}} and KEY not in repr(s)
    llm = build_llm(s, _secret)
    assert llm.available and llm.label == "OpenRouter" and llm.base_url == "https://openrouter.ai/api/v1" and llm.model_id == MODEL


@pytest.mark.parametrize("configured,expected", [
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
    ("https://openrouter.ai/api/v1/", "https://openrouter.ai/api/v1"),
    ("https://openrouter.ai/api/v1/chat/completions", "https://openrouter.ai/api/v1"),
    ("http://localhost:11434/v1", "http://localhost:11434/v1"),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
])
def test_base_url_is_used_as_written(configured, expected):
    assert ChatCompletionsLLM(api_key=KEY, base_url=configured, model_id=MODEL, enabled=True).base_url == expected


# ------------------------------------------------------------ requests
async def test_request_shape_with_configured_headers_and_body_extras():
    seen = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["headers"], seen["body"] = str(req.url), req.headers, json.loads(req.content)
        return _resp('{"answer": "ok", "score": 4}', {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100, "cost": 0.000021})

    m = client(h, provider_name="OpenRouter", extra_headers={"HTTP-Referer": "https://markets.example", "X-Title": "MI", "X-Empty": ""},
               extra_body={"usage": {"include": True}})
    assert await m.complete_validated("system", "user", Out) == Out(answer="ok", score=4)
    assert seen["url"] == f"{BASE}/chat/completions" and seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert seen["headers"]["http-referer"] == "https://markets.example" and seen["headers"]["x-title"] == "MI" and "x-empty" not in seen["headers"]
    b = seen["body"]
    assert b["model"] == MODEL and b["usage"] == {"include": True} and b["temperature"] <= 0.2 and b["max_tokens"] > 0
    assert b["response_format"]["type"] == "json_schema" and b["response_format"]["json_schema"]["schema"]["required"] == ["answer", "score"]
    u = m.status()["usage"]
    assert u["calls"] == 1 and u["total_tokens"] == 100 and u["cost_usd"] == pytest.approx(0.000021)
    assert m.status()["provider"] == "OpenRouter" and m.status()["base_url"] == BASE


async def test_self_hosted_provider_without_schema_support_gets_the_schema_in_the_prompt():
    """e.g. an Ollama or vLLM server: LLM_RESPONSE_FORMAT=json_object (or none), no model-list check."""
    seen = {"paths": []}

    def h(req: httpx.Request) -> httpx.Response:
        seen["paths"].append(req.url.path)
        seen["body"] = json.loads(req.content)
        return _resp('{"answer": "local", "score": 1}')

    m = ChatCompletionsLLM(api_key="none", base_url="http://localhost:11434/v1", model_id="glm4:9b", enabled=True, provider_name="Ollama",
                           response_format="json_object", verify_model_list=False, transport=httpx.MockTransport(h))
    assert (await m.complete_validated("Be brief.", "u", Out)).answer == "local"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert "valid against this JSON schema" in seen["body"]["messages"][0]["content"]
    assert (await m.check_availability())["ok"] is True and "/v1/models" not in seen["paths"]

    m2 = ChatCompletionsLLM(api_key="x", base_url="http://localhost:8000/v1", model_id="m", enabled=True, response_format="none",
                            transport=httpx.MockTransport(h))
    await m2.complete_validated("s", "u", Out)
    assert "response_format" not in seen["body"]


async def test_cost_stays_unknown_when_the_provider_does_not_report_it():
    m = client(lambda r: _resp('{"answer": "a", "score": 1}', {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}))
    await m.complete_validated("s", "u", Out)
    assert m.status()["usage"]["cost_usd"] is None and m.status()["usage"]["total_tokens"] == 10


async def test_repairs_once_then_fails_cleanly():
    replies = iter(['{"answer": "ok"}', '```json\n{"answer": "ok", "score": 2}\n```'])
    bodies = []

    def h(req):
        bodies.append(json.loads(req.content))
        return _resp(next(replies))

    assert (await client(h).complete_validated("sys", "user", Out)).score == 2 and len(bodies) == 2
    assert "did not match" in bodies[1]["messages"][-1]["content"]
    m2 = client(lambda r: _resp("not json at all"))
    with pytest.raises(LLMInvalidOutput):
        await m2.complete_validated("sys", "user", Out)
    assert m2.status()["usage"]["calls"] == 2 and m2.status()["usage"]["invalid_outputs"] == 1


async def test_budget_cap_blocks_further_calls():
    m = client(lambda r: _resp('{"answer": "ok", "score": 1}'), daily_budget=1)
    await m.complete_validated("s", "u", Out)
    with pytest.raises(LLMBudgetExceeded):
        await m.complete_validated("s", "u", Out)


# ------------------------------------------------------------ failures
async def test_rejection_names_the_settings_and_never_leaks_the_key(caplog):
    caplog.set_level(logging.DEBUG)
    m = client(lambda r: httpx.Response(402, json={"error": {"code": 402, "message": f"Insufficient credits for key {KEY}."}}), provider_name="OpenRouter")
    with pytest.raises(LLMUnavailable) as ei:
        await m.complete_validated("s", "u", Out)
    msg = str(ei.value)
    assert "OpenRouter rejected the request (HTTP 402)" in msg and "LLM_API_KEY" in msg and "LLM_BASE_URL" in msg and "[REDACTED]" in msg
    assert KEY not in msg and KEY not in caplog.text and KEY not in json.dumps(m.status()) and KEY not in repr(m)
    assert m.status()["usage"]["errors"] == 1


async def test_upstream_error_inside_a_200_and_rate_limits_are_retried():
    seq = iter([httpx.Response(200, json={"error": {"code": 502, "message": "Provider returned error"}}),
                httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}}),
                _resp('{"answer": "recovered", "score": 2}')])
    m = client(lambda r: next(seq), max_retries=2)
    assert (await m.complete_validated("s", "u", Out)).answer == "recovered" and m.status()["usage"]["errors"] == 2
    m2 = client(lambda r: httpx.Response(200, json={"error": {"message": "No endpoints found"}}))
    with pytest.raises(LLMUnavailable, match="upstream error: No endpoints found"):
        await m2.complete_validated("s", "u", Out)


# ------------------------------------------------------------ model verification
async def test_unlisted_model_is_rejected_without_a_paid_call_and_similar_ids_are_suggested():
    chats = []

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "z-ai/glm-5.3-flash"}, {"id": "z-ai/glm-5.3"}, {"id": "openai/gpt-4o"}]})
        chats.append(1)
        return _resp("OK")

    bad = ChatCompletionsLLM(api_key=KEY, base_url=BASE, model_id="z-ai/glm-5.3-flsh", enabled=True, transport=httpx.MockTransport(h))
    v = await bad.check_availability()
    assert v["ok"] is False and "LLM_MODEL_ID" in v["reason"] and v["suggestions"][0] == "z-ai/glm-5.3-flash" and chats == []
    good = ChatCompletionsLLM(api_key=KEY, base_url=BASE, model_id=MODEL, enabled=True, transport=httpx.MockTransport(h))
    assert (await good.check_availability())["ok"] is True and chats == [1]


async def test_model_check_still_probes_when_the_list_is_unreachable():
    m = client(lambda r: httpx.Response(503) if r.url.path.endswith("/models") else _resp("OK"))
    assert (await m.check_availability())["ok"] is True


# ------------------------------------------------------------ wiring
async def test_container_builds_the_configured_provider(tmp_path):
    s = Settings(_env_file=None, app_env="test", database_url=f"sqlite+aiosqlite:///{tmp_path}/o.db", scheduler_enabled=False,
                 llm_enabled=True, llm_provider_name="OpenRouter", llm_base_url="https://openrouter.ai/api/v1", llm_api_key=KEY, llm_model_id=MODEL)
    c = build_container(s)
    try:
        st = c.llm.status()
        assert st["state"] == "ok" and st["provider"] == "OpenRouter" and st["model_id"] == MODEL and KEY not in json.dumps(st)
    finally:
        await c.close()


def test_container_requires_a_database_url():
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        build_container(Settings(_env_file=None, app_env="test"))


def test_log_redaction_covers_provider_keys():
    out = redact({"note": f"calling with {KEY} now", "auth": f"Bearer {KEY}"})
    assert KEY not in json.dumps(out) and "sk-[REDACTED]" in out["note"]
