# Operations

## Deployment outline

- Services: `web` (nginx + static build), `api`, `worker`, PostgreSQL, Redis. See `docker-compose.yml`.
- Run `alembic upgrade head` before starting a new version (`migrate` service). Set
  `AUTO_CREATE_SCHEMA=false`.
- Run exactly one `worker`, and set `SCHEDULER_ENABLED=false` on the API. Redis must be configured
  when API and worker are separate processes, so job locks and cache invalidation are shared.
- Terminate TLS in front of `web`. Set `API_AUTH_TOKEN`, restrict `CORS_ORIGINS`, and keep the API
  port private.
- Secrets (`LLM_API_KEY`, `WEB_SEARCH_API_KEY`, `API_AUTH_TOKEN`, database password)
  come from the environment or a secrets manager. Only `.env.example` is committed.

## Health and monitoring

- `GET /health/live` (process), `GET /health/ready` (database and cache; upstream sources are
  reported but do not fail readiness, because stored data is still served).
- `GET /metrics` for Prometheus; alert rules in `infra/monitoring/alerts.yml`.
- Logs are JSON with a request or job id; tokens and secret-named fields are redacted.
- The Data status page shows connector state, latency, error rate, last success, advertised and
  blocked tools, stored coverage, schedule and job history.

## Backup and restore (PostgreSQL)

```bash
docker compose exec -T postgres pg_dump -U markets -Fc markets > backup_$(date +%F).dump
docker compose stop api worker
docker compose exec -T postgres pg_restore -U markets -d markets --clean --if-exists < backup_2026-10-12.dump
docker compose start api worker
```

Also back up `backend/var/fundamentals/` (uploaded statement files). Prices, metrics and snapshots
can be rebuilt by a refresh, except historical theme and rotation snapshots, which exist only in
the database. Test a restore before relying on backups.

## Routine procedures

| Situation | What to do |
|---|---|
| Broker session expired (daily) | Data status → Check broker session → open the Kite link → Refresh all data |
| NSE MCP down or timing out | Nothing is lost; stored data is served and marked stale after 4 days. The circuit opens after 5 failures and retries after 60 s. Refresh when it recovers |
| A server changed its tools | Data status → "Reconnect and re-read advertised tools", then `scripts/check_connectors.py`. Capabilities whose arguments cannot be bound show as not supported |
| A refresh job failed | Job history shows the error. Earlier snapshots are unchanged. Fix the cause and rerun |
| Rotate `API_AUTH_TOKEN` or `LLM_API_KEY` | Change the secret and restart `api` and `worker` |
| Suspected leaked secret | Rotate it at the provider first, then as above; check `audit_events` and logs by request id |
| LLM budget reached | Research and concall analysis fall back to rule-based output until the next UTC day, or raise `LLM_DAILY_BUDGET` |

## Before any public or commercial use

Review NSE, broker and search-provider licensing and redistribution terms; applicable
investment-advice, financial-promotion and data-protection rules; and add user accounts and
per-user authorisation.
