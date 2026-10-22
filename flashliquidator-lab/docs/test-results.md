# Deployment-readiness test results (2026-10-21)

Environment: cloud sandbox, Linux, 4 vCPU / 15 GB, Docker Engine 29.8.2 started inside the sandbox with
`--iptables=false` (no NAT: containers have no outbound internet and host-port publishing does not work; builds
used host networking through the sandbox's HTTP proxy via `docker-compose.sandbox.yml`). Outbound policy: npm
and PyPI allowed; Docker Hub rate-limited (base images pulled from `mirror.gcr.io`); **all blockchain RPC hosts,
GitHub release downloads, `binaries.soliditylang.org` and `ghcr.io` blocked**.

Legend: PASS = executed and passed here · SKIP = executed, reported skipped by the tool · BLOCKED = could not be
executed here, reason given · N/A = not applicable.

## 1. Docker networking

| Check | Result | Evidence |
|---|---|---|
| Workers can reach RPC providers | **FIXED, BLOCKED to verify** | `indexer`, `worker`, `scheduler`, `api` now join the `egress` network (no `internal: true`); `docker compose config` shows `networks: [egress, internal]` for them. Outbound traffic could not be exercised here (sandbox blocks RPC hosts and the daemon has no NAT). |
| PostgreSQL, Redis, Anvil not public | PASS | Only on `internal` (`internal: true`); no `ports:`; `docker compose ps` shows no published port for them. |
| API not published | PASS | `api` has no `ports:`; `curl 127.0.0.1:8000` from the host fails. |
| Compose config valid, base and `edge` profiles | PASS | `docker compose config -q` and `--profile edge config -q`. |

## 2. Transaction fee accounting

| Check | Result | Evidence |
|---|---|---|
| L1 data fee estimate (Base `GasPriceOracle.getL1Fee`, Arbitrum `NodeInterface.gasEstimateL1Component`) | PASS (unit) / BLOCKED (live) | `app/chains/l1fees.py`; serialized-tx shape test passes; live oracle calls need an RPC. |
| Verified fees from receipts (`l1Fee`, `gasUsedForL1`), no double counting on Arbitrum | PASS | `tests/test_l1fees.py` (4 tests). |
| Estimated vs verified kept apart | PASS | `fee_source` column on candidates (`estimated`) and historical liquidations (`verified`); `fees.source` on simulation results; a missing figure is `unknown`, never 0. Migration `9de9d0ca965e` applied by the `migrate` service. |

## 3. Solidity contracts

| Check | Result | Evidence |
|---|---|---|
| Compile `FlashLiquidator.sol` | PASS | `solc` 0.8.24 (solc-js, the official compiler's WebAssembly build), optimizer 200 runs, EVM cancun, OpenZeppelin 5.0.2: 0 errors, 0 warnings; 6,978-byte bytecode; artifact packaged at `backend/app/simulation/artifacts/FlashLiquidator.json`. Script: `scripts/compile-contracts.sh`. |
| `forge build` | BLOCKED | Foundry binaries: GitHub release downloads blocked. Source build (`cargo install --git`, rustc 1.99) fails in `svm-rs-builds` because it must fetch `binaries.soliditylang.org/linux-amd64/list.json`, which is blocked; `forge` also needs a native `solc` from that host. |
| `forge test --fork-url $BASE_ARCHIVE_RPC` (12 fork tests) | BLOCKED | No Foundry (above) and no archive RPC reachable. The suite is written to `vm.skip(true)` without `BASE_ARCHIVE_RPC`, so on a host with Foundry but no RPC it reports SKIPPED, not passed. |
| Anvil binary | BLOCKED | Same `svm-rs-builds` dependency; `ghcr.io/foundry-rs/foundry` image not pullable. `ANVIL_IMAGE` + `infra/anvil/Dockerfile` added for hosts in the same situation. |

## 4. Fork simulation uses the real receiver

| Check | Result | Evidence |
|---|---|---|
| Receiver path implemented and preferred | PASS (code) | `app/simulation/receiver.py`: deploys the compiled artifact on the fork, allow-lists tokens + SwapRouter02, calls `liquidate()`; figures come from the `SimulatedLiquidation` event. `app/simulation/replay.py` tries it first. |
| Simplified path labeled | PASS | Falls back only when the artifact/router is missing or `force_direct_path` is set; result carries `execution_path = direct_liquidation_call`, `simplified = true`, and a `SIMPLIFIED SIMULATION` warning. |
| Artifact loads, ABI has no arbitrary-call surface, `liquidate` calldata encodes | PASS | `tests/test_receiver.py`. |
| Receiver executed on a fork | BLOCKED | Needs Anvil and an archive RPC. |

## 5. Frontend exposure

| Check | Result | Evidence |
|---|---|---|
| Dashboard bound to localhost by default | PASS (config) / BLOCKED (host mapping) | `ports: 127.0.0.1:3000:3000` (`FRONTEND_BIND`/`FRONTEND_PORT`); binding present in the container's HostConfig; actual host reachability could not be tested (daemon without NAT). Verified through the container IP instead. |
| TLS + auth in front of everything | PASS (config) | `caddy` service (profile `edge`): automatic HTTPS for `PUBLIC_HOSTNAME`, `basic_auth` on all paths incl. `/api` and `/ws`, HSTS; the only service publishing 80/443. Not started here (no public hostname/ACME reachable). |
| Write endpoints still need the API admin login behind the edge | PASS | `/api/metrics` 401 without credentials, 200 with (through the frontend proxy, container IP). |

## 6. Docker stack end-to-end

| Check | Result | Evidence |
|---|---|---|
| Images build | PASS | `flashliquidator-lab-backend` (501 MB), `flashliquidator-lab-frontend` (345 MB) via `docker-compose.sandbox.yml`. |
| `migrate` runs Alembic to head | PASS | `Running upgrade -> 19ea7575a02f` then `-> 9de9d0ca965e`; exit 0. Found and fixed: blank `INDEXER_START_BLOCK_*=` made Settings fail; bcrypt `$` in `.env` must be `$$` (bootstrap/Makefile now escape it); backend services now share one explicit image. |
| Health checks | PASS | postgres, redis, api healthy; indexer/worker healthy via `celery inspect ping`; scheduler healthy via beat-process check; frontend healthy after `HOSTNAME=0.0.0.0` fix (Next standalone bound to the container hostname and failed its own check). |
| Background workers | PASS | In ~5 minutes: `record_rpc_health` 7 succeeded, `shadow_tick` 14 succeeded, `index_pool_events`/`reconcile_borrowers`/`build_candidates` recorded as **skipped** with `{"reason": "no RPC configured"}`; 28 `rpc_health_metrics` rows, all `ok=false` (truthful). Scheduler log: 15 "Sending due task". |
| Job round trip | PASS | `POST /api/backtests` (admin) → worker ran it → `strategy_runs` row `succeeded`, sample size 0 (no liquidations indexed). |
| Database persistence | PASS | `docker compose restart postgres`: 180 job rows and the backtest survived on volume `flashliquidator-lab_pgdata`; API served `/api/backtests/1` afterwards. |
| Browser end-to-end against the stack | PASS | Playwright 6/6 against the containerised frontend (admin sign-in with the stack password, backtest queued). |
| Anvil + fork simulation inside the stack | BLOCKED | No Anvil image/binary obtainable here (section 3). `worker` was started with `--no-deps`. |

## 7. Automated suites

| Suite | Result |
|---|---|
| Backend `pytest` (57 tests: money, config/auth, API, Aave reads, indexer reorg/idempotency, profitability, Monte Carlo, predictive, replay skip path, L1 fees, receiver artifact) | PASS 57/57 |
| Backend `ruff check` | PASS |
| Frontend `npm run lint`, `npm run build` | PASS |
| Frontend Playwright (local build and containerised) | PASS 6/6 both |
| Contracts `forge test` | BLOCKED (see 3) |

## Phase 2 (live chains) addendum

Executed later the same day once RPC hosts were allowed: see `phase2-results.md`. Headline: Base deployment
verified live; 4,226 borrowers and 24 liquidations indexed; 19 replays executed through the real flash-loan
receiver (3 succeeded, measured); Forge fork tests still blocked by the archive provider's quota; Arbitrum
replays blocked for lack of an archive provider.

## Phase 3 (profit verification and competition) addendum

Executed 2026-10-22 against Base and Arbitrum with an Alchemy archive key (Base and Arbitrum), PublicNode for
live reads, and three private Anvil forks. Full figures in `phase3-results.md`.

| Check | Result | Evidence |
|---|---|---|
| Profit reconciliation (balances vs event surplus, every cost) | PASS | `app/simulation/accounting.py`; `tests/test_accounting.py` (4); every Phase 3 replay carries `profit_complete`, `surplus_raw`, `reconciliation`; migration `c55ca9144780`. |
| No "net profitable" label with an unknown material cost | PASS | `summarize_replays` counts `succeeded_with_incomplete_costs` separately; `tests/test_report_normalization.py`. |
| Eligibility at observation (N−1) and at execution (block N timestamp) stored separately | PASS | `eligible_at_fork` / `eligible_at_execution` columns; Base batch: 3 eligible at both, 4 only at execution, 17 at neither. |
| Replay reporting normalised (unique txs / events / attempts) | PASS | Base: 15 txs / 24 events / 26 attempts; Arbitrum: 4 / 4 / 5. |
| Base replays complete on the archive provider | PASS | 24 of 24 events through `flash_loan_receiver`; 3 net profitable with complete costs. |
| Arbitrum replays | PASS (executed) | 4 of 4 events replayed on an Arbitrum archive fork; 0 succeeded (see triage in the report). |
| Intra-block reconstruction of block 52,620,870 | PASS | `app/simulation/reconstruct.py`: 61 relevant transactions re-executed; trigger = time-gated ETH/USD round revealed inside the winner's transaction; our receiver succeeded at the winner's position (surplus 3,120.95 USDC). |
| Time-gated oracle probe over all recorded events | PASS | Base 18 of 24 events, Arbitrum 1 of 4, had a newer hidden round at N−1 (reveal 0/15/30 s). |
| Live competitive benchmark (read-only) | PASS (executed) | `app/competition/benchmark.py`: 150 minutes, 4,380 Base blocks, 348 watched positions; block lag median 0.32 s, detection median 1.33 s (3 Multicall3 trips); 0 liquidations and 0 new eligible positions in the window (reported as not estimable, not as 0 %). Simulation latency from a separate short run on an Alchemy-forked Anvil (PublicNode fork: HTTP 403). |
| Bid / win-probability model with observed vs modeled separation | PASS | `app/competition/bidmodel.py`; `tests/test_bidmodel.py` (4). |
| Forge fork tests on real archive state | PASS 16/16 | `forge test --fork-url $BASE_ARCHIVE_RPC` (Foundry 1.8.5, fork block 30,000,000) after fixing two test bugs (a wrong accounting identity in `test_Liquidate_SwapsAllCollateral`, selector-only `expectRevert` → `expectPartialRevert`). |
| Receiver same-asset liquidation (collateral == debt) | FIXED | Found by the Arbitrum USDT/USDT replay (router reverted swapping a token into itself); the receiver now skips the swap; artifact recompiled with solc-js, bytecode identical to `forge build` except the metadata hash. |
| Backend `pytest` / `ruff` | PASS 75/75 / PASS | after Phase 3 changes. |
| Live trading still impossible | PASS | no signer, no broadcast path, `LIVE_TRADING_ENABLED=true` aborts startup; the benchmark and reconstruction never build a transaction for a public chain. |

## Not done, and why
- Nothing on a real chain: every RPC host is blocked in this sandbox. First action on the target host: set
  `BASE_RPC_HTTP` / `ARBITRUM_RPC_HTTP` (+ archive URLs), confirm `deployment_verified: true` on the Overview,
  then run one historical replay and check `execution_path = flash_loan_receiver` on the result.
- Live transactions remain impossible by construction (no signer, no broadcast path, `LIVE_TRADING_ENABLED=true` aborts startup).
