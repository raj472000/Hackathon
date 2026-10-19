"""LLM provider: any OpenAI-compatible chat-completions API, configured entirely from settings.

OpenRouter, OpenAI, Together, Groq, Mistral, DeepSeek, Fireworks, a self-hosted vLLM or Ollama
server and many others expose the same `POST {base}/chat/completions` protocol. Which one is used
is decided only by `.env`: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID`, plus optional extra
headers and request-body fields (`LLM_EXTRA_HEADERS`, `LLM_EXTRA_BODY`). No provider URL or
provider-specific behaviour is built into the code.

The model id is configuration, not an assumption: `check_availability()` verifies it against the
provider's `GET {base}/models` list when one is published, then with one minimal call.

The model is used for synthesis and classification only. Output must validate against a Pydantic
schema; one constrained repair attempt is made, after which the caller falls back to a
deterministic summary. Tokens, latency, errors, cost (when the provider reports it) and a daily
call/token budget are tracked. The API key is never logged or returned.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    pass


class LLMInvalidOutput(LLMError):
    pass


class LLMBudgetExceeded(LLMError):
    pass


class LLMTruncated(LLMInvalidOutput):
    """The reply hit max_tokens before any answer text, typically because a reasoning model spent the budget thinking."""


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> Any:
    t = _FENCE.sub("", (text or "").strip()).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b > a:
            return json.loads(t[a: b + 1])
        raise


class ChatCompletionsLLM:
    """Client for an OpenAI-compatible `/chat/completions` route. Provider details come from settings."""

    name = "llm"

    def __init__(self, *, api_key: str | None, model_id: str | None, enabled: bool, base_url: str | None,
                 provider_name: str = "LLM", extra_headers: dict[str, str] | None = None, extra_body: dict[str, Any] | None = None,
                 verify_model_list: bool = True, response_format: str = "json_schema", timeout: float = 60.0, max_retries: int = 1,
                 max_output_tokens: int = 4000, temperature: float = 0.1, daily_budget: int = 200, daily_token_budget: int = 2_000_000,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token, self.model_id, self.enabled = api_key, model_id, enabled
        self.label = provider_name or "LLM"
        self.base_url = self._normalise(base_url) if base_url else None
        self._extra_headers = {str(k): str(v) for k, v in (extra_headers or {}).items() if v not in (None, "")}
        self._extra_body = dict(extra_body or {})
        self.verify_model_list, self.response_format = verify_model_list, response_format
        self.max_retries, self.max_output_tokens, self.temperature = max_retries, max_output_tokens, temperature
        self.daily_budget, self.daily_token_budget = daily_budget, daily_token_budget
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._day = datetime.now(UTC).date()
        self._usage: dict[str, Any] = self._zero()
        self._verified: dict[str, Any] | None = None

    @staticmethod
    def _normalise(url: str) -> str:
        """LLM_BASE_URL is used as written; a pasted full `.../chat/completions` URL is trimmed to its base."""
        return re.sub(r"/chat/completions/?$", "", url.strip()).rstrip("/")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(provider={self.label!r}, model_id={self.model_id!r}, available={self.available})"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json", **self._extra_headers}

    @staticmethod
    def _zero() -> dict[str, Any]:
        return {"calls": 0, "errors": 0, "invalid_outputs": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cost_usd": None, "latency_ms_avg": None, "last_error": None, "last_call_at": None}

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.base_url and self._token and self.model_id)

    def _reason(self) -> str | None:
        if not self.enabled:
            return "LLM_ENABLED is false"
        if not self.base_url:
            return "LLM_BASE_URL is not set"
        if not self._token:
            return "LLM_API_KEY is not set"
        if not self.model_id:
            return "LLM_MODEL_ID is not set"
        return None

    def status(self) -> dict[str, Any]:
        self._roll()
        return {"name": self.name, "provider": self.label, "state": "ok" if self.available else "not_configured", "reason": self._reason(),
                "model_id": self.model_id, "base_url": self.base_url, "verified": self._verified,
                "usage": {**self._usage, "model_id": self.model_id, "day": self._day.isoformat(), "daily_budget": self.daily_budget,
                          "daily_token_budget": self.daily_token_budget,
                          "note": "Cost (USD) is summed from the provider's usage report when it sends one; otherwise token counts are the usage measure."}}

    def _roll(self) -> None:
        today = datetime.now(UTC).date()
        if today != self._day:
            self._day, self._usage = today, self._zero()

    async def close(self) -> None:
        await self._http.aclose()

    async def check_availability(self) -> dict[str, Any]:
        """One minimal request to confirm the token, route and model id work for this account."""
        if not self.available:
            self._verified = {"ok": False, "reason": self._reason(), "checked_at": datetime.now(UTC).isoformat()}
            return self._verified
        listed = await self._model_listed()
        if listed is False:
            self._verified = {"ok": False, "checked_at": datetime.now(UTC).isoformat(), "suggestions": await self._similar_models(),
                              "reason": f"model id '{self.model_id}' is not in {self.label}'s model list; check LLM_MODEL_ID"}
            return self._verified
        try:
            await self._chat([{"role": "user", "content": "Reply with the single word OK."}], None, max_tokens=5)
            self._verified = {"ok": True, "checked_at": datetime.now(UTC).isoformat()}
        except LLMError as e:
            self._verified = {"ok": False, "reason": str(e), "checked_at": datetime.now(UTC).isoformat(),
                              "suggestions": await self._similar_models()}
        return self._verified

    async def _model_ids(self) -> list[str] | None:
        """The route's advertised model ids, or None when the list cannot be read."""
        try:
            r = await self._http.get(f"{self.base_url}/models", headers=self._headers())
            if r.status_code != 200:
                return None
            return [m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m]
        except (httpx.HTTPError, ValueError, AttributeError):
            return None

    async def _model_listed(self) -> bool | None:
        """True/False when the provider publishes a model list, None when it does not or the check is off."""
        if not self.verify_model_list:
            return None
        ids = await self._model_ids()
        return None if not ids else self.model_id in ids

    async def _similar_models(self) -> list[str]:
        """Model ids the route offers that resemble the configured one (helps with a mistyped id)."""
        import difflib

        ids = await self._model_ids() or []
        want = (self.model_id or "").lower()
        key = lambda s: re.sub(r"[^a-z0-9.]", "", s.split("/")[-1].lower())  # noqa: E731
        by_key = {i: key(i) for i in ids}
        close = difflib.get_close_matches(key(want), list(by_key.values()), n=5, cutoff=0.6)
        return [i for c in close for i, k in by_key.items() if k == c][:5]

    async def _chat(self, messages: list[dict[str, str]], response_format: dict[str, Any] | None, max_tokens: int | None = None) -> str:
        if not self.available:
            raise LLMUnavailable(self._reason() or "LLM is not configured")
        self._roll()
        if self._usage["calls"] >= self.daily_budget or self._usage["total_tokens"] >= self.daily_token_budget:
            raise LLMBudgetExceeded("daily LLM budget reached; research falls back to deterministic summaries until tomorrow (UTC)")
        body: dict[str, Any] = {"model": self.model_id, "messages": messages, "temperature": self.temperature,
                                "max_tokens": max_tokens or self.max_output_tokens, **self._extra_body}
        if response_format:
            body["response_format"] = response_format
        last: Exception | None = None
        for _ in range(self.max_retries + 1):
            self._usage["calls"] += 1
            self._usage["last_call_at"] = datetime.now(UTC).isoformat()
            t0 = time.perf_counter()
            try:
                r = await self._http.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers())
            except httpx.HTTPError as e:
                last = LLMUnavailable(f"LLM request failed: {type(e).__name__}")
                self._err(last)
                continue
            if r.status_code >= 500 or r.status_code == 429:
                last = LLMUnavailable(f"LLM provider returned HTTP {r.status_code}")
                self._err(last)
                continue
            if r.status_code != 200:
                detail = ""
                try:
                    err = r.json().get("error")
                    detail = str((err.get("message") if isinstance(err, dict) else err) or "")[:200]
                except (ValueError, AttributeError):
                    pass
                if self._token:
                    detail = detail.replace(self._token, "[REDACTED]")
                detail = detail.strip().rstrip(".")
                e2 = LLMUnavailable(f"{self.label} rejected the request (HTTP {r.status_code})" + (f": {detail}" if detail else "")
                                    + ". Check LLM_API_KEY, LLM_MODEL_ID, LLM_BASE_URL and the account's credits.")
                self._err(e2)
                raise e2
            try:
                data = r.json()
            except ValueError:
                last = LLMUnavailable(f"{self.label} returned a response that is not JSON")
                self._err(last)
                continue
            if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                # Some gateways report an upstream provider failure inside a 200 response.
                err = data["error"]
                msg = str((err.get("message") if isinstance(err, dict) else err) or "")[:200]
                last = LLMUnavailable(f"{self.label} upstream error: {msg}")
                self._err(last)
                continue
            ms = (time.perf_counter() - t0) * 1000
            prev = self._usage["latency_ms_avg"]
            self._usage["latency_ms_avg"] = round(ms if prev is None else prev * 0.8 + ms * 0.2, 1)
            u = data.get("usage") or {}
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self._usage[k] += int(u.get(k) or 0)
            if isinstance(u.get("cost"), (int, float)):
                self._usage["cost_usd"] = round((self._usage["cost_usd"] or 0.0) + float(u["cost"]), 6)
            try:
                choice = data["choices"][0]
                content = choice["message"].get("content") or ""
            except (KeyError, IndexError, TypeError, AttributeError) as e:
                raise LLMInvalidOutput("LLM response had no message content") from e
            if not content.strip() and choice.get("finish_reason") == "length":
                self._err(LLMTruncated("truncated"))
                raise LLMTruncated(
                    f"{self.label} used the whole output budget ({body['max_tokens']} tokens) before writing an answer "
                    "(reasoning tokens count against it). Raise LLM_MAX_OUTPUT_TOKENS or lower reasoning in LLM_EXTRA_BODY.")
            return content
        raise last or LLMUnavailable("LLM request failed")

    def _err(self, e: Exception) -> None:
        self._usage["errors"] += 1
        self._usage["last_error"] = str(e)[:200]

    async def complete_validated(self, system: str, user: str, model: type[T]) -> T:
        """Chat completion constrained to `model`'s JSON schema and validated with Pydantic.
        Makes one repair attempt when the output does not validate."""
        schema = model.model_json_schema()
        fmt: dict[str, Any] | None = None
        if self.response_format == "json_schema":
            fmt = {"type": "json_schema", "json_schema": {"name": model.__name__, "schema": schema, "strict": True}}
        elif self.response_format == "json_object":
            fmt = {"type": "json_object"}
        # The schema always travels in the prompt too: some providers accept response_format but do not show it to the model.
        system = f"{system}\n\nReply with one JSON object only, valid against this JSON schema:\n{json.dumps(schema)}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            text = await self._chat(messages, fmt)
        except LLMTruncated:
            # One retry with a larger budget; a reasoning model may need more room for a long evidence bundle.
            text = await self._chat(messages, fmt, max_tokens=min(self.max_output_tokens * 2, 16000))
        try:
            return model.model_validate(extract_json(text))
        except (ValidationError, json.JSONDecodeError, ValueError) as first:
            problem = str(first)[:600]
        messages += [
            {"role": "assistant", "content": text[:4000]},
            {"role": "user", "content": "Your previous reply did not match the required JSON schema.\n"
                                        f"Problem: {problem}\nReturn only one JSON object that validates against this schema, "
                                        f"with no commentary and no code fences:\n{json.dumps(schema)}"},
        ]
        text = await self._chat(messages, fmt)
        try:
            return model.model_validate(extract_json(text))
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            self._usage["invalid_outputs"] += 1
            raise LLMInvalidOutput(f"LLM output failed schema validation after one repair attempt: {str(e)[:200]}") from e



def build_llm(s: Any, secret: Any) -> ChatCompletionsLLM:
    """The provider described by settings. `secret` unwraps SecretStr values."""
    return ChatCompletionsLLM(
        api_key=secret(s.llm_api_key), model_id=s.llm_model_id, enabled=s.llm_enabled, base_url=s.llm_base_url,
        provider_name=s.llm_provider_name, extra_headers=s.llm_extra_headers, extra_body=s.llm_extra_body,
        verify_model_list=s.llm_verify_model_list, response_format=s.llm_response_format, timeout=s.llm_timeout_seconds,
        max_retries=s.llm_max_retries, max_output_tokens=s.llm_max_output_tokens, temperature=s.llm_temperature,
        daily_budget=s.llm_daily_budget, daily_token_budget=s.llm_daily_token_budget)
