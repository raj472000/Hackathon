"""Minimal MCP client over Streamable HTTP.

Implements what the connectors need: `initialize`, `notifications/initialized`, `tools/list`,
`tools/call`, a long-lived session (the broker login is bound to the MCP session), JSON and SSE
response framing, per-call deadlines, bounded concurrency, pacing, retries for retryable failures
only, and a circuit breaker.

Only tools the server advertises can be called, and when an allowlist is given only tools on it.
Tool errors are raised, never returned, so an error can never be cached as a value.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.resilience import CircuitBreaker, CircuitOpenError, Clock, MinIntervalLimiter, retry_async

PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "capital-markets-intelligence", "version": "0.1.0"}


class McpError(Exception):
    pass


class McpUnavailable(McpError):
    """Transport-level failure or upstream 5xx/429. Retryable."""


class McpToolError(McpError):
    """The tool ran and reported an error (`isError`) or the server returned a JSON-RPC error."""


class McpNotSupported(McpError):
    """The server does not advertise this tool."""


class McpForbiddenTool(McpError):
    """The tool exists but policy forbids calling it."""


class McpInvalidArguments(McpError):
    pass


class _SessionExpired(McpError):
    pass


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]


class McpClient:
    def __init__(
        self,
        name: str,
        url: str,
        *,
        connect_timeout: float = 10.0,
        tool_timeout: float = 30.0,
        max_concurrency: int = 4,
        max_retries: int = 2,
        min_interval: float = 0.2,
        allowed_tools: frozenset[str] | set[str] | None = None,
        breaker_threshold: int = 5,
        breaker_reset: float = 60.0,
        headers: dict[str, str] | None = None,
        clock: Clock | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name, self.url = name, url
        self.clock = clock or Clock()
        self.max_retries = max_retries
        self.allowed_tools = frozenset(allowed_tools) if allowed_tools is not None else None
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(tool_timeout, connect=connect_timeout), transport=transport,
                                       headers=headers or {}, follow_redirects=False)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._limiter = MinIntervalLimiter(min_interval, self.clock)
        self._breaker = CircuitBreaker(breaker_threshold, breaker_reset, self.clock)
        self._connect_lock = asyncio.Lock()
        self._session_id: str | None = None
        self._protocol: str | None = None
        self.session_store: Path | None = None  # set for servers that bind an authorisation to the session (Kite)
        self.restored_session = False
        self._id = 0
        self.advertised: dict[str, ToolInfo] = {}
        self.server_info: dict[str, Any] = {}
        self.connected = False
        self.discovered_at: datetime | None = None
        self.session_resets = 0
        self.session_reset_at: datetime | None = None
        self._stats: dict[str, Any] = {"calls": 0, "errors": 0, "retries": 0, "latency_ms": None, "last_success_at": None,
                                       "last_error": None, "last_error_at": None}

    def __repr__(self) -> str:
        return f"McpClient(name={self.name!r}, url={self._safe_url()!r}, connected={self.connected})"

    def _safe_url(self) -> str:
        u = urlsplit(self.url)
        return f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}{u.path}"

    @property
    def tools(self) -> dict[str, ToolInfo]:
        """Advertised tools this client is permitted to call."""
        if self.allowed_tools is None:
            return dict(self.advertised)
        return {k: v for k, v in self.advertised.items() if k in self.allowed_tools}

    def has_tool(self, name: str) -> bool:
        return name in self.tools

    # ------------------------------------------------------------------ transport
    async def _post(self, payload: dict[str, Any]) -> Any:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol:
            headers["MCP-Protocol-Version"] = self._protocol
        try:
            r = await self._http.post(self.url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise McpUnavailable(f"timeout calling {self.name}: {type(e).__name__}") from e
        except httpx.HTTPError as e:
            raise McpUnavailable(f"cannot reach {self.name}: {type(e).__name__}: {e}") from e
        if r.status_code == 202 or (r.status_code == 200 and not r.content):
            return None
        if r.status_code == 404 and self._session_id:
            raise _SessionExpired("session not found")
        if r.status_code >= 500 or r.status_code == 429:
            raise McpUnavailable(f"{self.name} returned HTTP {r.status_code}")
        body = self._decode(r, payload.get("id"))
        if r.status_code >= 400:
            msg = (body or {}).get("error", {}).get("message") if isinstance(body, dict) else None
            # Kite answers a forgotten session with HTTP 400, text/plain "Invalid session ID" (no JSON body).
            if self._session_id and "session" in (msg or r.text[:200]).lower():
                raise _SessionExpired(msg)
            raise McpToolError(f"{self.name} rejected the request (HTTP {r.status_code}): {msg or r.text[:200]}")
        if sid := r.headers.get("mcp-session-id"):
            self._session_id = sid
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            raise McpToolError(f"{self.name} error {err.get('code')}: {err.get('message')}")
        return body.get("result") if isinstance(body, dict) else None

    @staticmethod
    def _decode(r: httpx.Response, rid: Any) -> Any:
        if "text/event-stream" in r.headers.get("content-type", ""):
            found = None
            for block in r.text.replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == rid and ("result" in msg or "error" in msg):
                    found = msg
            return found
        try:
            return r.json()
        except (json.JSONDecodeError, ValueError):
            return None

    async def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._id += 1
            payload["id"] = self._id
        return await self._post(payload)

    # ------------------------------------------------------------------ lifecycle
    async def _handshake(self) -> None:
        self._session_id = self._protocol = None
        init = await self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO})
        if not isinstance(init, dict):
            raise McpUnavailable(f"{self.name} returned no initialize result")
        self._protocol = init.get("protocolVersion") or PROTOCOL_VERSION
        self.server_info = init.get("serverInfo") or {}
        await self._rpc("notifications/initialized", notify=True)

    async def _new_session(self) -> None:
        """The server forgot our session: start a new one and count it, so a provider whose
        authorisation is bound to the session (Kite) can report that the login was lost."""
        await self._handshake()
        self.session_resets += 1
        self.session_reset_at = self.clock.now()
        self._save_session()

    # ------------------------------------------------------------------ session persistence
    def _load_session(self) -> bool:
        """Reuse the session saved by a previous run of the API, so an authorisation bound to it survives a restart."""
        if self.session_store is None or not self.session_store.is_file():
            return False
        try:
            doc = json.loads(self.session_store.read_text())
            if doc.get("url") != self._safe_url() or not doc.get("session_id"):
                return False
        except (OSError, ValueError):
            return False
        self._session_id, self._protocol = doc["session_id"], doc.get("protocol") or PROTOCOL_VERSION
        self.server_info = doc.get("server_info") or {}
        return True

    def _save_session(self) -> None:
        if self.session_store is None or not self._session_id:
            return
        try:
            self.session_store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.session_store.with_suffix(".tmp")
            # Owner-only: the id is a handle to the account holder's approved session.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"url": self._safe_url(), "session_id": self._session_id, "protocol": self._protocol,
                           "server_info": self.server_info, "saved_at": self.clock.now().isoformat()}, f)
            os.replace(tmp, self.session_store)
        except OSError:
            pass  # persistence is a convenience; the live session keeps working without it

    async def forget_session(self) -> None:
        """Drop the current session (and the saved copy) and open a new, unauthorised one."""
        if self.session_store is not None:
            with contextlib.suppress(OSError):
                self.session_store.unlink()
        async with self._connect_lock:
            await self._handshake()
            self.restored_session = False
            self._save_session()

    async def connect(self, refresh: bool = False) -> dict[str, ToolInfo]:
        """Handshake and discover the advertised tools. Safe to call repeatedly."""
        async with self._connect_lock:
            if self.connected and not refresh:
                return self.tools
            try:
                self._breaker.before_call()
            except CircuitOpenError as e:
                raise McpUnavailable(f"{self.name}: {e}") from e
            try:
                # Re-reading the tools of a live session keeps that session: a broker login is bound to it,
                # so a fresh handshake would silently discard the user's approval.
                restored = not self._session_id and self._load_session()
                if not (restored or (refresh and self.connected and self._session_id)):
                    await self._handshake()
                    self._save_session()
                tools: dict[str, ToolInfo] = {}
                cursor: str | None = None
                for _ in range(20):
                    try:
                        res = await self._rpc("tools/list", {"cursor": cursor} if cursor else {})
                    except _SessionExpired:
                        restored = False  # the saved session has ended on the server: start a fresh one
                        await self._new_session()
                        res = await self._rpc("tools/list", {"cursor": cursor} if cursor else {})
                    for t in (res or {}).get("tools", []):
                        tools[t["name"]] = ToolInfo(t["name"], t.get("description") or "", t.get("inputSchema") or {})
                    cursor = (res or {}).get("nextCursor")
                    if not cursor:
                        break
            except McpError as e:
                self.connected = False
                self._fail(e)
                self._breaker.failure()
                raise McpUnavailable(str(e)) from e
            self.advertised, self.connected, self.discovered_at = tools, True, self.clock.now()
            self.restored_session = self.restored_session or restored
            self._breaker.success()
            self._stats["last_success_at"] = self.clock.now()
            return self.tools

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ calls
    def _validate(self, tool: str, arguments: dict[str, Any]) -> None:
        schema = self.advertised[tool].input_schema or {}
        props = schema.get("properties") or {}
        missing = [r for r in schema.get("required") or [] if r not in arguments]
        if missing:
            raise McpInvalidArguments(f"{tool}: missing required argument(s) {', '.join(missing)}")
        unknown = [k for k in arguments if k not in props]
        if unknown and (props or schema.get("additionalProperties") is False):
            raise McpInvalidArguments(f"{tool}: argument(s) not in the advertised schema: {', '.join(unknown)}")

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        if self.allowed_tools is not None and tool not in self.allowed_tools:
            raise McpForbiddenTool(f"{tool} is not on the allowlist for {self.name}")
        if not self.connected:
            await self.connect()
        if tool not in self.advertised:
            raise McpNotSupported(f"{self.name} does not advertise a tool named {tool}")
        self._validate(tool, arguments)
        try:
            self._breaker.before_call()
        except CircuitOpenError as e:
            raise McpUnavailable(f"{self.name}: {e}") from e

        async def attempt() -> Any:
            await self._limiter.acquire()
            t0 = time.perf_counter()
            self._stats["calls"] += 1
            params = {"name": tool, "arguments": arguments}
            try:
                res = await self._rpc("tools/call", params)
            except _SessionExpired:
                await self._new_session()
                res = await self._rpc("tools/call", params)
            ms = (time.perf_counter() - t0) * 1000
            prev = self._stats["latency_ms"]
            self._stats["latency_ms"] = ms if prev is None else prev * 0.8 + ms * 0.2
            return res

        attempts = 0

        async def counted() -> Any:
            nonlocal attempts
            attempts += 1
            return await attempt()

        async with self._sem:
            try:
                res = await retry_async(counted, retries=self.max_retries, is_retryable=lambda e: isinstance(e, McpUnavailable),
                                        clock=self.clock)
            except _SessionExpired as e:
                self._fail(e)
                raise McpUnavailable(f"{self.name}: session could not be re-established") from e
            except McpUnavailable as e:
                self._stats["retries"] += attempts - 1
                self._fail(e)
                self._breaker.failure()
                raise
            except McpToolError as e:
                self._fail(e, upstream=False)
                raise
        self._stats["retries"] += attempts - 1
        value = self._parse_result(tool, res)
        self._breaker.success()
        self._stats["last_success_at"] = self.clock.now()
        return value

    def _parse_result(self, tool: str, res: Any) -> Any:
        if not isinstance(res, dict):
            raise McpToolError(f"{tool}: empty result")
        text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text").strip()
        if res.get("isError"):
            e = McpToolError(f"{tool}: {text[:300] or 'tool reported an error'}")
            self._fail(e, upstream=False)
            raise e
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        sc = res.get("structuredContent")
        if sc is not None:
            return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
        return text

    def _fail(self, e: Exception, upstream: bool = True) -> None:
        self._stats["errors"] += 1
        self._stats["last_error"] = str(e)[:300]
        self._stats["last_error_at"] = self.clock.now()
        self._last_failure_upstream = upstream

    # ------------------------------------------------------------------ health
    def health(self) -> dict[str, Any]:
        s = self._stats
        last_ok, last_err = s["last_success_at"], s["last_error_at"]
        if self.connected and (last_err is None or (last_ok and last_ok >= last_err) or not getattr(self, "_last_failure_upstream", True)):
            state = "ok"
        elif self.connected:
            state = "degraded"
        else:
            state = "unavailable" if last_err else "not_connected"
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {
            "name": self.name, "url": self._safe_url(), "state": state, "breaker": self._breaker.state,
            "server": self.server_info, "tool_count": len(self.tools), "tools": sorted(self.tools),
            "blocked_tools": sorted(set(self.advertised) - set(self.tools)),
            "discovered_at": iso(self.discovered_at), "last_success_at": iso(last_ok), "last_error": s["last_error"],
            "last_error_at": iso(last_err), "latency_ms": None if s["latency_ms"] is None else round(s["latency_ms"], 1),
            "calls": s["calls"], "errors": s["errors"], "retries": s["retries"],
            "error_rate": round(s["errors"] / s["calls"], 4) if s["calls"] else None,
        }
