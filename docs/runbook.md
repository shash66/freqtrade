# Replication runbook

How to stand this project up on a fresh server and reproduce every result in `phase2-results.md` and
`phase3-results.md`. Written from the sessions that produced those results (21–22 Oct 2026); everything
here was actually run. Where a step depends on a provider's quota or product, the limit observed is stated.

## 1. What you need

| Item | Minimum | Notes |
|---|---|---|
| Server | 4 CPU, 8 GB RAM, 40 GB disk, Linux | Three to four Anvil forks plus Postgres fit comfortably; the Base replay batch is network-bound, not CPU-bound. |
| Docker Engine 24+ with Compose v2 | for the stack | The sandbox used for development had no NAT (`--iptables=false`); `docker-compose.sandbox.yml` exists for that case. A normal host does not need it. |
| Python 3.13, Node 20+, Foundry | for running tools outside Docker | Foundry **1.8.5** was built from source (`cargo install --git https://github.com/foundry-rs/foundry --tag v1.8.5 forge cast anvil`, rustc 1.99). v1.5.0 failed to build (`svm-rs-builds` duplicate constants); the release-binary download was blocked in the sandbox. Builds need `binaries.soliditylang.org` reachable. |
| PostgreSQL 16, Redis 7 | the compose stack provides them | Use **two databases**: one for tests (`flashliquidator`) and one for validation data (`flashliquidator_live`). Tests pin their own database through `TEST_DATABASE_URL`. |
| Archive-capable RPC for Base and Arbitrum | required for replays, reconstruction, theoretical valuation | Alchemy free tier was enough for everything here (one key, `base-mainnet` and `arb-mainnet` hosts). |
| Live RPC for Base and Arbitrum | required for indexing, reconciliation, the live benchmark | PublicNode and dRPC public endpoints were used; see the provider matrix. |

### Outbound hosts to allow (if the server has an egress policy)

`base-mainnet.g.alchemy.com`, `arb-mainnet.g.alchemy.com`, `base-rpc.publicnode.com`,
`arbitrum-one-rpc.publicnode.com`, `base.drpc.org`, `arbitrum.drpc.org`, `mainnet.base.org`,
`arb1.arbitrum.io`, `binaries.soliditylang.org` (solc for Foundry), `github.com` and `objects.githubusercontent.com`
(Foundry source and releases), `ghcr.io` (Anvil image) or `mirror.gcr.io` (Docker base images),
`pypi.org`/`files.pythonhosted.org`, `registry.npmjs.org`, `crates.io`/`static.crates.io` (only if building Foundry).

### Provider capabilities measured

| Endpoint | `eth_getLogs` | Historical (archive) state | Other | Used for |
|---|---|---|---|---|
| Alchemy free (`*.g.alchemy.com/v2/<key>`) | spans ≤ 10 blocks | yes, Base and Arbitrum | `eth_getBlockReceipts` and `eth_getBlockByNumber` with transactions work; no `trace_*`/`debug_*`; ~330 compute units/s shared by everything using the key; 429 under load | archive forks, theoretical valuation, reconstruction, Forge fork tests |
| `base.drpc.org`, `arbitrum.drpc.org` (public) | 100-block spans, any age | Base yes; Arbitrum **no** | strict per-minute quota (429) | indexing/backfill |
| `base-rpc.publicnode.com`, `arbitrum-one-rpc.publicnode.com` | 2,000-block spans within ~10k blocks of head | last ~128 blocks only; pinned-block reads return HTTP 403 "archive requests require a personal token" within seconds, which also breaks Anvil forks at the live head | live reads, reconciliation, the benchmark's head polling and Multicall3 reads |
| `mainnet.base.org` | rate-limited (429) under indexing load | yes | | not used |
| keyed `lb.drpc.live` (Arbitrum) | 100-block spans | no | | not usable for Arbitrum replays |

Rule that follows from the table: **forks always use the archive endpoint** (`replay.py` does this), live
polling uses PublicNode, log backfills use dRPC with `INDEXER_MAX_BLOCK_RANGE=100`.

## 2. Install

```bash
git clone <this repository> && cd flashliquidator-lab
./scripts/bootstrap.sh                  # writes .env with a DB password and your admin password hash
# edit .env: BASE_RPC_HTTP, BASE_ARCHIVE_RPC, ARBITRUM_RPC_HTTP, ARBITRUM_ARCHIVE_RPC,
#            INDEXER_MAX_BLOCK_RANGE=100 (public log endpoints), RPC_MIN_INTERVAL_MS=250 (optional throttle)
docker compose up -d --build            # postgres, redis, anvil, migrate, api, indexer, worker, scheduler, frontend
```

Without Docker (what the research sessions used):

```bash
python3.13 -m venv .venv && . .venv/bin/activate && pip install -e "backend[dev]"
cd backend && alembic upgrade head       # DATABASE_URL must point at the database you intend to use
cd ../contracts && forge build && forge test --fork-url "$BASE_ARCHIVE_RPC"   # 16 fork tests, ~10 s
cd .. && ./scripts/compile-contracts.sh  # solc-js artifact used by the simulator (bytecode == forge build except metadata hash)
cd frontend && npm ci && npm run build
```

Anvil forks used by the tools (one per concurrent job; all pin `--compute-units-per-second` so a shared
Alchemy key is not exhausted):

```bash
anvil --host 127.0.0.1 --port 8545 --fork-url "$BASE_ARCHIVE_RPC" --steps-tracing --retries 8 --fork-retry-backoff 2000 --timeout 180000 --compute-units-per-second 250 --quiet
anvil --host 127.0.0.1 --port 8546 --fork-url "$BASE_ARCHIVE_RPC" --steps-tracing --retries 8 --fork-retry-backoff 2000 --timeout 180000 --compute-units-per-second 120 --quiet
anvil --host 127.0.0.1 --port 8548 --fork-url "$ARBITRUM_ARCHIVE_RPC" --steps-tracing --retries 8 --fork-retry-backoff 2000 --timeout 120000 --compute-units-per-second 200 --silent
```

Set `ANVIL_RPC_URL` for each tool to the fork it should use. Two jobs on one Anvil corrupt each other's
state; never share one.

## 3. Environment file used for the research runs

Values that differ from `.env.example` (keys redacted):

```
DATABASE_URL=postgresql+psycopg://fll:<pw>@127.0.0.1:5432/flashliquidator_live
BASE_RPC_HTTP=https://base-rpc.publicnode.com
BASE_ARCHIVE_RPC=https://base-mainnet.g.alchemy.com/v2/<key>
ARBITRUM_RPC_HTTP=https://arbitrum.drpc.org
ARBITRUM_ARCHIVE_RPC=https://arb-mainnet.g.alchemy.com/v2/<key>
INDEXER_MAX_BLOCK_RANGE=100
RPC_MIN_INTERVAL_MS=250
INDEXER_START_BLOCK_BASE=52560000        # ~1.5 days before the run; the 7-day backfill stopped at 52,640,886 under quota
INDEXER_START_BLOCK_ARBITRUM=516163364   # 3 days before the run
ANVIL_RPC_URL=http://127.0.0.1:8545
LOG_LEVEL=WARNING
```

## 4. Reproduce the results, in order

Each step is idempotent and resumable; persisted replay runs are reused unless `--fresh` is given.

```bash
cd backend && . ../.venv/bin/activate && set -a && . ../.env && set +a

# 4.1 Index history (Base ~1.5 days took ~3 h on dRPC public; Arbitrum 1,000,000 blocks ≈ 3 days took ~55 min)
python -m app.indexer.cli backfill base 52560000 52640886
python -m app.indexer.cli backfill arbitrum 516163364 517163364

# 4.2 Phase 2: deployment verification, live indexing, reconciliation, replays through the receiver
python -m app.validation.phase2 --network base --blocks 20000 --replays 25 --out ../docs/phase2-results-base.md --json ../docs/phase2-results-base.json
python -m app.validation.phase2 --network arbitrum --blocks 20000 --replays 25 --out ../docs/phase2-results-arbitrum.md --json ../docs/phase2-results-arbitrum.json

# 4.3 Phase 3 (or run scripts/phase3-research.sh base, which chains all of this through docker compose)
python -m app.validation.theoretical --network base --out ../docs/phase3-theoretical-base.json
python -m app.validation.phase2 --network base --blocks 0 --replays 40 --batch phase3-base --out ../docs/phase3-replays-base.md --json ../docs/phase3-replays-base.json   # 24 events ≈ 45 min
ANVIL_RPC_URL=http://127.0.0.1:8548 python -m app.validation.phase2 --network arbitrum --blocks 0 --replays 40 --batch phase3-arbitrum --out ../docs/phase3-replays-arbitrum.md --json ../docs/phase3-replays-arbitrum.json
python -m app.simulation.reconstruct --network base --block 52620870 --borrower 0x66bb6c2949b20503ADF3621db9903Ee52F92ffB5 \
  --collateral 0x4200000000000000000000000000000000000006 --debt 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 \
  --tx 0xa0927afbf117e86277c56cb823e841851177643d189e5b5e6a00cf29214f4c91 --debt-to-cover 66106884515 \
  --anvil http://127.0.0.1:8546 --simulate --relevant-only --out ../docs/phase3-reconstruction-base-52620870.json   # ≈ 20 min
# time-gate probe for every recorded event: loop `python -m app.simulation.reconstruct ... --time-gate-only` per (block, collateral, debt)
python -m app.competition.benchmark --network base --minutes 150 --hf-max 1000000 --poll 0.5 --anvil http://127.0.0.1:8545 --sim-every 100 --max-sims 12 --out ../docs/phase3-benchmark-base.json
python -m app.competition.bidmodel --network base --replays-json ../docs/phase3-replays-base.json --benchmark-json ../docs/phase3-benchmark-base.json --eth-usd 4000 --out ../docs/phase3-bidmodel-base.json
python -m app.validation.phase3_report --docs ../docs --networks base,arbitrum --out ../docs/phase3-results.md
```

Expected outcomes (what the committed artefacts show): Base 24 events / 15 txs / 26 attempts, 6 succeed with
complete costs (sum $3,120.80, of which $3,120.36 only reachable inside the winning block); Arbitrum 4 events,
1 succeeds, unprofitable after costs; 18 of 24 Base events and 1 of 4 Arbitrum events have a time-gated oracle
round at N−1; benchmark detection latency ≈ 1.3 s median, fork simulation ≈ 17 s median.

## 5. Operational pitfalls (all hit during the runs)

- **Fixture rows in the validation database.** `pytest` once ran in a shell whose `DATABASE_URL` pointed at
  the validation database and inserted fixture rows (block numbers < 1000, borrower `0x1111…1111`, pool
  addresses ending in `00000000`). Tests now always use `TEST_DATABASE_URL`, and every validation query is
  restricted to the address-book Pool deployment, but the rows still exist in that database. Clean-up:
  `delete from historical_liquidations where block_number < 1000; delete from pool_events where block_number < 1000; delete from protocol_deployments where id in (3,4,5,6);`
- **`pkill -f` / `pgrep -f` self-kill.** A shell command whose own text contains the pattern matches itself.
  Put the pattern in a script file and run the file.
- **Anvil + public endpoint.** Forking PublicNode at the live head fails with HTTP 403 after a few seconds.
  Fork the archive endpoint; use PublicNode only for direct live reads.
- **Alchemy `eth_getLogs`** refuses spans over 10 blocks; the indexer halves ranges automatically but it is
  far cheaper to backfill from dRPC with 100-block spans.
- **Stuck replays** are almost always the archive provider rate-limiting Anvil's lazy state fetches while
  another fork shares the key. Run one heavy fork job at a time, or raise the plan.
- **Foundry version.** 1.8.5 works; `vm.expectRevert(bytes4)` on an error with arguments needs
  `vm.expectPartialRevert`.
- **Compose interpolation.** Every `$` in a bcrypt hash inside `.env` must be `$$` (bootstrap does this);
  `${VAR:?}` in inactive profiles still errors, use `${VAR:-}`.
- **Next.js standalone** binds to the container hostname; the image sets `HOSTNAME=0.0.0.0`.

## 6. Where things are

| What | Where |
|---|---|
| Results and raw artefacts | `docs/phase2-results*.md/json`, `docs/phase3-*.json`, `docs/phase3-results.md` |
| Method and caveats | `docs/simulation-methodology.md`, `docs/limitations.md`, `docs/test-results.md` |
| Research tools | `backend/app/validation/{phase2,theoretical,phase3_report}.py`, `backend/app/simulation/{replay,reconstruct,accounting}.py`, `backend/app/competition/{benchmark,bidmodel,montecarlo}.py` |
| Receiver contract and fork tests | `contracts/src/FlashLiquidator.sol`, `contracts/test/FlashLiquidator.t.sol`, artifact at `backend/app/simulation/artifacts/FlashLiquidator.json` |
| Database schema | `backend/migrations/versions/` (3 migrations; `alembic upgrade head`) |
| Notes for future AI-assisted sessions | `CLAUDE.md` in the project root |

## 7. Safety invariants

`LIVE_TRADING_ENABLED` must stay `false` (any other value aborts startup). There is no signer, no key
storage and no broadcast path anywhere in the code; every tool above executes only on a private Anvil fork
or reads the public chain. Keep it that way when extending the project; building an executor is a separate
decision with its own review.
