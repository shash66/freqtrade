# Architecture

FlashLiquidator Lab is a simulation-only research platform. Services (Docker Compose):

| Service | Role | Network exposure |
|---|---|---|
| `frontend` (Next.js) | Dashboard; proxies `/api` and `/ws` to the API | host port 3000 |
| `api` (FastAPI) | REST + WebSocket, admin auth, Prometheus metrics | internal + edge |
| `indexer` (Celery, queue `indexer`) | Aave pool event ingestion with checkpoints and reorg handling | internal only |
| `worker` (Celery, queues `celery`, `simulation`) | reconciliation, candidate building, fork simulations, backtests, shadow tracking | internal only |
| `scheduler` (Celery beat) | periodic jobs | internal only |
| `postgres`, `redis` | state, cache/queue/pub-sub | internal only |
| `anvil` | isolated fork simulator | internal only, never published |

Modules (backend/app):

- `chains/`: network configs, RPC client (timeouts, retries, health), startup verification of chain id, bytecode and
  `ADDRESSES_PROVIDER` round-trip.
- `aave/`: address book (pinned npm version), ABIs, typed exact reads (reserve bitmap decoding, account data, prices).
- `indexer/`: `pool_indexer.py` (events → `pool_events`, borrowers, `historical_liquidations`), `reconcile.py`
  (authoritative on-chain reads, adaptive detail for near-liquidation borrowers, snapshots, prices).
- `strategies/`: `profitability.py` (pure liquidation math), `quotes.py` (Uniswap v3 QuoterV2), `engine.py`
  (candidates with full breakdown and incomplete states), `shadow.py` (outcome tracking, latency metrics).
- `simulation/`: `anvil.py` (fork control), `replay.py` (liquidation on a fork), `service.py` (runs/results, backtests).
- `competition/`: strategies A/B/C, competitor statistics, seeded Monte Carlo.
- `workers/`: Celery app, beat schedule, job-run auditing.

Failure isolation: every periodic job fans out per network; one network's RPC failure fails only that job run,
which is recorded in `worker_job_runs`. The API serves with any subset of networks reachable and reports
`degraded` when none is.

Outcome classes (never summed together): `theoretical` (candidate math), `simulated` (fork results),
`modeled_capturable` (Monte Carlo), `onchain_actual` (recorded liquidations by others).
