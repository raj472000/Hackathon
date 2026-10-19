"""Live connector check: connects to the configured MCP servers with the application's own
connector code, lists what they advertise, and reports the capability mapping.

    cd backend && DATA_MODE=live uv run python ../scripts/check_connectors.py [--save-schemas DIR] [--symbol INFY]

It makes read-only calls only. With --symbol it also fetches one quote and a few days of history
from every source that is reachable and authorised.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from app.connectors.base import ProviderUnavailable
from app.core.config import Settings
from app.core.container import build_container


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-schemas", type=Path)
    ap.add_argument("--symbol")
    a = ap.parse_args()
    s = Settings(data_mode="live", database_url="sqlite+aiosqlite:///:memory:", scheduler_enabled=False)
    c = build_container(s)
    await c.start()
    ok = True
    clients = {"nse_bhavcopy": c.nse._clients["bhav"], "nse_cm_market": c.nse._clients["cm"], "kite_mcp": c.kite.client}
    for name, cl in clients.items():
        if cl is None:
            print(f"[{name}] not configured")
            continue
        h = cl.health()
        print(f"[{name}] {h['url']} state={h['state']} server={h['server']} tools={h['tool_count']} blocked={len(h['blocked_tools'])}")
        if h["state"] != "ok":
            ok = False
            print(f"    last error: {h['last_error']}")
            continue
        for t in sorted(cl.advertised.values(), key=lambda t: t.name):
            props = t.input_schema.get("properties", {})
            req = set(t.input_schema.get("required", []))
            flag = "" if cl.has_tool(t.name) else "  [BLOCKED by allowlist]"
            print(f"    {t.name}({', '.join(k + ('' if k in req else '?') for k in props)}){flag}")
        if a.save_schemas:
            a.save_schemas.mkdir(parents=True, exist_ok=True)
            out = a.save_schemas / f"{name}_tools_{date.today().isoformat()}.json"
            out.write_text(json.dumps({"_recorded": f"{date.today().isoformat()} from {h['url']}", "server": h["server"],
                                       "tools": [{"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                                                 for t in cl.advertised.values()]}, indent=1))
            print(f"    saved {out}")
    print("\nCapabilities")
    for prov, caps in (("nse_mcp", c.nse.capabilities()), ("kite_mcp", c.kite.capabilities())):
        for k, v in caps.items():
            print(f"  {prov:8} {k:18} {'yes via ' + v['tool'] if v['supported'] else 'no  (' + str(v['reason'])[:90] + ')'}")
    print("\nBroker session:", json.dumps(await c.kite.auth_state()))
    if a.symbol:
        end = date.today()
        start = end - timedelta(days=200)
        bench = s.benchmark_symbol
        for label, call in (("nse quote", c.nse.get_quote(a.symbol)), ("nse history (200 days, paged)", c.nse.get_history(a.symbol, start, end)),
                            ("nse corporate actions", c.nse.get_corporate_actions(a.symbol)),
                            (f"nse index quote {bench}", c.nse.get_quote(bench, "INDEX")),
                            (f"nse index history {bench}", c.nse.get_history(bench, start, end, "INDEX")),
                            ("nse unknown symbol", c.nse.get_history("NOSUCHSYM", start, end)),
                            ("kite resolve", c.kite.resolve(a.symbol)), ("kite history", c.kite.get_history(a.symbol, start, end)),
                            ("correlated history", c.market_data.history(a.symbol, start, end)), ("correlated quote", c.market_data.quote(a.symbol))):
            try:
                r = await call
                if hasattr(r, "coverage"):
                    detail = f"{len(r.frame)} bars, coverage {r.coverage}, sources {[(x.provider, x.dataset, x.status) for x in r.sources]}, warnings {r.warnings[:2]}"
                elif hasattr(r, "frame"):
                    detail = f"{len(r.frame)} bars {r.frame.index[0].date()}..{r.frame.index[-1].date()}, last close {r.frame['close'].iloc[-1]}"
                elif hasattr(r, "fields"):
                    detail = {k: v.value for k, v in r.fields.items()} | {"as_of": str(r.fields["last_price"].as_of)}
                elif isinstance(r, dict) and "fields" in r:
                    detail = {k: (v["value"], v["provider"], v["status"]) for k, v in r["fields"].items()} | {"sources": [(x["provider"], x["status"]) for x in r["sources"]]}
                elif isinstance(r, dict):
                    detail = {k: r.get(k) for k in ("id", "instrument_token", "isin", "segment")}
                else:
                    detail = f"{len(r)} rows: {[(str(x.ex_date), x.kind, x.ratio) for x in r][:6]}"
                print(f"  {label}: OK {detail}")
            except ProviderUnavailable as e:
                print(f"  {label}: {e.state} - {e}")
    await c.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
