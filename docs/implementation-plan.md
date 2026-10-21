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
- Repository scaffold per spec layout. **done**
- `docker-compose.yml`, `docker-compose.dev.yml`, `.env.example`, `Makefile`, `scripts/`. **done** (validated with `docker compose config`; not started here, no daemon)
- FastAPI backend: settings, structured logging, `/api/health`, `/api/networks`, `/api/overview`, `/api/metrics`,
  basic-auth admin, rate limiting, Prometheus metrics, WebSocket `/ws/updates`. **done**
- PostgreSQL schema (all 18 tables from spec §12, plus `indexer_checkpoints` and `pool_events`) via SQLAlchemy models + Alembic migration. **done** (applied to a local PostgreSQL 16)
- Celery worker + beat scheduler with job-run auditing (`worker_job_runs`). **done**
- Next.js 15 + Tailwind + shadcn-style components + Recharts; six pages with in-dashboard admin sign-in; Overview shows real health data. **done**
- Tests: 51 pytest tests (money, config/auth, API, reads, indexer, profitability, Monte Carlo, predictive, replay skip path); 6 Playwright e2e tests. **done** (6/6 e2e green)

Acceptance: `docker compose up -d --build` starts the platform; dashboard reports actual connection health
(including "RPC unreachable" when that is the truth).

### M2: Real blockchain monitoring
- Chain adapters (Base 8453, Arbitrum 42161; Ethereum adapter slot, disabled) with startup validation. **done**
- Aave V3 ABIs (Pool, PoolDataProvider, Oracle, ERC-20, QuoterV2) and typed read layer with bitmap decoding. **done**
- Event indexer: Supply/Borrow/Repay/Withdraw/LiquidationCall/ReserveUsedAsCollateral*, checkpointed
  backfill, adaptive ranges, retries, reorg handling, idempotent writes. **done** (unit-tested with a fake chain; **blocked** for live acceptance: no RPC reachable)
- Borrower reconciliation via `getUserAccountData` + per-reserve reads, snapshots, priority scoring. **done** (blocked for live acceptance)
- Historical liquidation ingestion (`LiquidationCall` → `historical_liquidations`, receipts for gas). **done** (blocked for live acceptance)
- Opportunity explorer page; predictive scenarios endpoint (§5.4). **done**

### M3: Profitability engine
- Liquidation rule evaluation from live reserve configuration (close factor, HF thresholds, dust rules,
  liquidation protocol fee; e-mode bonus accepted as an explicit override). **done**
- Flash-loan premium from `FLASHLOAN_PREMIUM_TOTAL`, gas model per chain, DEX quote adapter
  (Uniswap v3 Quoter v2, on-chain) with explicit *incomplete* state when a quote is unavailable. **done**
- Full profit breakdown and sensitivity figures per spec §6. **done** (hand-checked worked examples in tests)

### M4: Historical fork simulation
- Solidity `FlashLiquidator.sol` receiver + Forge fork tests + local-only deploy script. **partial**: written, **not compiled** (no Foundry here); compile risks listed in `contracts/README.md`
- Anvil orchestration service, replay with block-boundary labeling, job API with CSV/JSON export. **done** (blocked for live acceptance: needs Anvil + archive RPC)

### M5: Competitive benchmarking
- Strategies A/B/C, competitor statistics from `historical_liquidations`, seeded Monte Carlo with
  confidence intervals, Competition Lab page. **done**

### M6: Live shadow mode
- Continuous read-only loop, latency metrics, outcome tracking, exports. **done** (24-hour unattended acceptance **blocked**: needs RPC)

## What was verified here, and what was not

Verified: 51 backend tests against a real PostgreSQL 16 and Redis 7; the API served live on :8000 with the
dashboard proxying to it; Compose configuration parses with all nine services; `npm run lint` and
`npm run build` clean; Playwright e2e against the production build.

An independent review of the financial core (liquidation math, indexer, replay, Monte Carlo) was run and its
findings fixed: stale per-reserve rows, placeholder prices, e-mode bonus, indexer upsert/hash ordering, per-attempt
Monte Carlo statistics, replay gas model.

Not verified (first things to run after deployment, in this order):
1. `docker compose up -d --build` on a machine with a Docker daemon.
2. With `BASE_RPC_HTTP`/`ARBITRUM_RPC_HTTP` set: the Overview must show `deployment_verified: true`; then
   `scripts/backfill.sh base <from> <to>` and watch `/api/liquidations/history` fill with real tx hashes.
3. With archive RPCs: one historical replay through the Backtesting page (milestone 4 acceptance).
4. `forge build && forge test --fork-url $BASE_ARCHIVE_RPC` in `contracts/`.

## Known dependencies on paid or external services

- Archive RPC (historical state) is required for M4 replay and for M2 backfill older than a provider's
  free window. Public endpoints generally do not serve archive state; Alchemy/QuickNode/dRPC style plans do.
- DEX quotes are read on-chain (no API key) but need a working RPC.
