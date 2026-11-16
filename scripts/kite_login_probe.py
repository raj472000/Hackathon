"""Standalone Kite MCP login, independent of the Markets app: open a session, call Kite's own `login` tool, print the
link, then check every 10 s (get_profile, read-only) whether the login completed. Stops after 10 minutes."""
import json
import re
import sys
import time

import httpx

U = "https://mcp.kite.trade/mcp"


def body(r):
    t = r.text
    if "text/event-stream" in r.headers.get("content-type", ""):
        t = "\n".join(line[5:].strip() for line in t.splitlines() if line.startswith("data:"))
    try:
        return json.loads(t)
    except ValueError:
        return {"raw": t[:200], "status": r.status_code}


with httpx.Client(timeout=30) as c:
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    r = c.post(U, headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "kite-login-probe", "version": "1"}}})
    h = {**h, "Mcp-Session-Id": r.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-03-26"}
    c.post(U, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    text = json.dumps(body(c.post(U, headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "login", "arguments": {}}})))
    link = re.search(r"https://mcp\.kite\.trade/authorize\?session_id=[^\s\"\\)\]]+", text).group(0)
    print("SESSION", h["Mcp-Session-Id"], flush=True)
    print("LINK", link, flush=True)
    for i in range(60):
        time.sleep(10)
        b = body(c.post(U, headers=h, json={"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call", "params": {"name": "get_profile", "arguments": {}}}))
        res = b.get("result") or {}
        if res and not res.get("isError"):
            content = json.dumps(res.get("content"))[:300]
            uid = re.search(r'user_id\\?":\s*\\?"(\w+)', content)
            print(time.strftime("%H:%M:%S"), "LOGGED IN", uid.group(1) if uid else "(profile returned)", flush=True)
            sys.exit(0)
        print(time.strftime("%H:%M:%S"), "not logged in yet", flush=True)
    print("TIMEOUT: no login completed in 10 minutes", flush=True)
