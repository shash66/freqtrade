# FlashLiquidator Lab: implementation plan

Status legend: **done** / **partial** / **planned** / **blocked (reason)**.
This file is updated as work lands; it records what actually exists, not what is intended.

## Ground rules carried through every milestone

- Simulation only. No private keys, no signing, no broadcast code path. `LIVE_TRADING_ENABLED` exists only so
  the API can refuse to start if anyone sets it to anything but `false`.
- Money is never a float. Raw on-chain amounts are `int` (base units) in Python and `NUMERIC(78,0)` in
  PostgreSQL; USD values are `Decimal`.
- Four distinct outcome classes are kept apart in the schema and the UI: *theoretical*, *simulated*,
  *modeled-capturable*, *on-chain actual*. Nothing sums across them.
- Addresses come from `@bgd-labs/aave-address-book` (npm, version pinned in `backend/app/aave/address_book.py`),
  and are re-verified on startup against chain ID and contract bytecode. Nothing is hardcoded from memory.
- Every data gap is reported as a gap. No synthetic rows are labeled as chain data.

## Environment facts that shape the plan (recorded 2026-10-21)

| Capability | In this build environment | Consequence |
|---|---|---|
| Docker daemon | not running (CLI only) | Compose files are validated with `docker compose config`; services were tested natively |
| Base / Arbitrum RPC | blocked by network policy | Indexer and monitor are implemented and unit-tested against recorded fixtures; live acceptance needs an RPC host allowed |
| Foundry (forge/anvil) | not installable (download host blocked) | Solidity contract and Forge tests are written; they are **not** compiled or run here |
| PostgreSQL 16, Redis 7, Node 22, Python 3.13 | available natively | Backend, migrations and frontend are built and tested |

## Milestones

### M1: Docker application foundation
- Repository scaffold per spec layout. **planned**
- `docker-compose.yml`, `docker-compose.dev.yml`, `.env.example`, `Makefile`, `scripts/`. **planned**
- FastAPI backend: settings, structured logging, `/api/health`, `/api/networks`, `/api/overview`, `/api/metrics`,
  basic-auth admin, rate limiting, Prometheus metrics, WebSocket `/ws/updates`. **planned**
- PostgreSQL schema (all 18 tables from spec §12) via SQLAlchemy models + Alembic migration. **planned**
- Celery worker + beat scheduler skeleton with job-run auditing (`worker_job_runs`). **planned**
- Next.js 15 + Tailwind + shadcn-style components + Recharts; Overview page showing real health data. **planned**
- Tests: pytest unit tests for settings, auth, health, money types; Playwright smoke test. **planned**

Acceptance: `docker compose up -d --build` starts the platform; dashboard reports actual connection health
(including "RPC unreachable" when that is the truth).

### M2: Real blockchain monitoring
- Chain adapters (Base 8453, Arbitrum 42161, Ethereum 1 optional) with startup validation. **planned**
- Aave V3 ABIs (Pool, PoolDataProvider, Oracle, UiPoolDataProvider) and typed read layer. **planned**
- Event indexer: Supply/Borrow/Repay/Withdraw/LiquidationCall/ReserveUsedAsCollateral*, checkpointed
  backfill, adaptive ranges, retries, reorg handling, idempotent writes. **planned**
- Borrower reconciliation via `getUserAccountData` + per-reserve reads. **planned**
- Historical liquidation ingestion (`LiquidationCall` → `historical_liquidations`). **planned**
- Opportunity explorer page. **planned**

### M3: Profitability engine
- Liquidation rule evaluation from live reserve configuration (close factor, HF thresholds, dust rules,
  liquidation protocol fee, e-mode). **planned**
- Flash-loan premium from `FLASHLOAN_PREMIUM_TOTAL`, gas model per chain, DEX quote adapter
  (Uniswap v3 Quoter v2, on-chain) with explicit *incomplete* state when a quote is unavailable. **planned**
- Full profit breakdown and sensitivity figures per spec §6. **planned**

### M4: Historical fork simulation
- Solidity `FlashLiquidator.sol` receiver + Forge tests. **planned**
- Anvil orchestration service, replay with block-boundary labeling. **planned**

### M5: Competitive benchmarking
- Strategies A/B/C, competitor statistics from `historical_liquidations`, seeded Monte Carlo with
  confidence intervals, Competition Lab page. **planned**

### M6: Live shadow mode
- Continuous read-only loop, latency metrics, outcome tracking. **planned**

## Known dependencies on paid or external services

- Archive RPC (historical state) is required for M4 replay and for M2 backfill older than a provider's
  free window. Public endpoints generally do not serve archive state; Alchemy/QuickNode/dRPC style plans do.
- DEX quotes are read on-chain (no API key) but need a working RPC.
