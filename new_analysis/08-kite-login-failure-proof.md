# Kite login failure: "error completing Kite session" (30 Oct 2026)

## Summary

The Kite login fails at the last step on **Zerodha's hosted Kite MCP server** (`mcp.kite.trade`), not in this app.
A login that used none of this app's code failed in exactly the same way.

## What happens

1. The app gets a login link from Kite's `login` tool.
2. The user signs in at Zerodha. Zerodha redirects with `status=success&request_token=…`, so the sign-in itself worked.
3. `mcp.kite.trade/callback` answers **"error completing Kite session"** (HTTP 500).

## Proof that it isn't this app

| Test | Result |
|---|---|
| **Standalone login** (`scripts/kite_login_probe.py`) | Same error, on the probe's own session `kitemcp-8b6f8ee5…`. Screenshot: `kite-callback-error-standalone.png`. This run used no app code and no Azure. |
| Kite server version and protocol | `Kite MCP Server v0.3.2`. It negotiates protocol `2025-03-26` whether asked for that, `2025-06-18` or `2026-07-28`, and returns a normal stateful session. Zerodha's new stateless routing (in the unreleased `v0.4.0-dev` builds only) doesn't apply. |
| Does Kite still have the session? | Yes, consistently. 20 of 20 `/authorize` checks validated the session (the same check the browser path makes), and 19 of 20 `get_profile` calls got Kite's normal "not logged in" reply. The other one was rate-limited by Cloudflare. |
| The app's own attempt | One link was issued at 08:20 UTC and reused on every click, on one session (no session resets) and one replica. |
| Other users | The same error was reported on Zerodha's issue tracker: [#95](https://github.com/zerodha/kite-mcp-server/issues/95) (27 Oct), and [#96](https://github.com/zerodha/kite-mcp-server/issues/96) and [#98](https://github.com/zerodha/kite-mcp-server/issues/98) (30 Oct, briefly reported fixed at 12:10 IST). |

## Where it fails inside Kite's server

Kite's server code (`kc/manager.go` on the `master` branch) shows that the callback returns this message only when `CompleteSession` fails. That can happen in two places:

- **Session lookup** (`GetSession`). This part works: the session was found every time.
- **Token exchange** (`GenerateSession(request_token, apiSecret)`). Zerodha's server swaps the one-time token for an access token, using Zerodha's own `kitemcp` API secret. This is the step that fails, and only Zerodha can fix it.

An expired link gives a *different* error, "invalid authorize session". The link signature is valid for 5 minutes (`DefaultSignatureExpiry`).

## What the app does meanwhile

- **Prices:** the Nifty 50 ticker and other prices come from NSE. Stocks with no price are left out, never shown blank.
- **Login screen:** after "I have approved it" without a confirmed login, the app explains this failure and offers **Start over with a new link**.
- **Replicas:** `infra/azure/deploy.sh` keeps the app on one replica, so a Kite login can't be split across replicas.

## Re-run the proof

```bash
backend/.venv/bin/python -I scripts/kite_login_probe.py
```

The script prints a link. Open it within 5 minutes and sign in. The script then reports `LOGGED IN` or keeps saying `not logged in yet`.

## A lasting fix (needs a decision)

Stop depending on the shared hosted server: sign in directly through **Kite Connect** with your own developer app. That needs your own API key and secret, with the redirect going to this app.

This isn't built yet. It needs:

- a Kite Connect app at developers.kite.trade;
- its key and secret, kept in Key Vault;
- the redirect URL `https://<app>/api/v1/sources/kite/callback`;
- a check of Zerodha's current pricing for market data.

Kite access tokens still expire once a day, so a daily login remains.
