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

## Networking and exposure

Two Docker networks:

| Network | `internal: true` | Members | Purpose |
|---|---|---|---|
| `internal` | yes (no route out) | postgres, redis, anvil, migrate, api, indexer, worker, scheduler, frontend, caddy | service-to-service traffic; databases and Anvil exist only here |
| `egress` | no | api, indexer, worker, scheduler, caddy | outbound HTTPS/WebSocket to RPC providers and ACME; nothing publishes a port on it |

Published ports: the dashboard on **127.0.0.1:3000 only** by default (`FRONTEND_BIND`/`FRONTEND_PORT`), so a
plain `docker compose up` is reachable from the host itself or over an SSH tunnel and from nowhere else.
PostgreSQL, Redis, Anvil and the API never publish a port.

For remote access use the `edge` profile, which adds Caddy with automatic HTTPS for `PUBLIC_HOSTNAME` and
basic auth in front of the entire dashboard (pages, `/api` and `/ws`):

```bash
# in .env: PUBLIC_HOSTNAME=lab.example.com  EDGE_BASICAUTH_USER=admin
#          EDGE_BASICAUTH_HASH=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your password')
docker compose --profile edge up -d
```

Caddy is the only service that publishes 80/443. The API's own admin login (write endpoints) stays in place
behind it. Do not set `FRONTEND_BIND=0.0.0.0` on an internet-facing host without the edge profile.
- Containers run as non-root with read-only filesystems, `no-new-privileges`, and CPU/memory limits.
- Hosts that cannot pull `ghcr.io/foundry-rs/foundry` can build Anvil from a local binary
  (`docker build -f infra/anvil/Dockerfile --build-arg ANVIL_BIN=./anvil -t fll-anvil:local infra/anvil`)
  and set `ANVIL_IMAGE=fll-anvil:local`. Hosts rate-limited by Docker Hub can pull the base images from
  `mirror.gcr.io/library/<image>` and tag them.
- Contracts: `scripts/compile-contracts.sh` compiles the receiver with solc-js and installs the artifact the
  simulator deploys; `forge build` output in `contracts/out/` is used instead when present.
- Development: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up` mounts the source
  directories with hot reload and publishes the API on 8000 and Postgres on 5432.
- Backfill: `scripts/backfill.sh base <from_block> <to_block>`.
- Tests: `make test` (backend pytest needs a reachable Postgres and Redis; contracts need Foundry).
- Observability: `/api/metrics` (Prometheus, admin auth); `infra/prometheus/prometheus.yml` is a starting scrape config.
- Retention: raw monitoring tables are pruned after `RAW_DATA_RETENTION_DAYS`.

Without Docker (development): create a venv, `pip install -e backend[dev]`, run `alembic upgrade head`, then
`uvicorn app.main:app` and `celery -A app.workers.celery_app:celery worker -B`.
