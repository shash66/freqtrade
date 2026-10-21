# Deployment

Requirements: Docker Engine 24+ with Compose v2, 4 CPU / 8 GB RAM recommended, RPC endpoints for Base and
Arbitrum (archive endpoints for historical replay).

```bash
git clone <repo> && cd flashliquidator-lab
./scripts/bootstrap.sh          # creates .env with a generated DB password and your admin password hash
#   then edit .env: BASE_RPC_HTTP, ARBITRUM_RPC_HTTP, *_ARCHIVE_RPC
docker compose up -d --build    # starts postgres, redis, anvil, migrate, api, indexer, worker, scheduler, frontend
open http://localhost:3000
```

- Only the frontend is published (port 3000). PostgreSQL, Redis, Anvil, the API and workers sit on an
  `internal: true` network. Admin endpoints use HTTP Basic; put Caddy in front (`infra/caddy/Caddyfile`)
  for TLS before exposing it beyond localhost.
- Containers run as non-root with read-only filesystems, `no-new-privileges`, and CPU/memory limits.
- Development: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up` mounts the source
  directories with hot reload and publishes the API on 8000 and Postgres on 5432.
- Backfill: `scripts/backfill.sh base <from_block> <to_block>`.
- Tests: `make test` (backend pytest needs a reachable Postgres and Redis; contracts need Foundry).
- Observability: `/api/metrics` (Prometheus, admin auth); `infra/prometheus/prometheus.yml` is a starting scrape config.
- Retention: raw monitoring tables are pruned after `RAW_DATA_RETENTION_DAYS`.

Without Docker (development): create a venv, `pip install -e backend[dev]`, run `alembic upgrade head`, then
`uvicorn app.main:app` and `celery -A app.workers.celery_app:celery worker -B`.
