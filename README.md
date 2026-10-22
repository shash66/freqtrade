# FlashLiquidator Lab

Self-hosted research, simulation and benchmarking platform for Aave V3 flash-loan liquidations on Base and
Arbitrum. It exists to answer one question with evidence instead of hope:

> Can a liquidation strategy consistently identify and realistically capture opportunities that produce
> positive net profit after fees, gas and competition?

**It is simulation-only.** There is no wallet, no private key, no transaction broadcast, and
`LIVE_TRADING_ENABLED=true` aborts startup. Four kinds of numbers are kept strictly apart and labeled
everywhere: theoretical candidates, fork simulations, modeled capturability, and actual on-chain liquidations.

## What it does

- Indexes Aave V3 Pool events to discover borrowers, with checkpoints, adaptive block ranges and reorg rollback.
- Reconciles positions against authoritative contract reads, prioritizing borrowers near liquidation.
- Computes liquidation opportunities with the protocol's own math (close factor, protocol fee, v3.3 dust rules),
  flash-loan premium, on-chain DEX quotes and gas; anything it cannot source is marked incomplete.
- Replays recorded liquidations on an isolated Anvil fork and reports a full financial breakdown, labeled as a
  block-boundary approximation.
- Benchmarks three strategies (polling, event-driven, predictive) with a seeded Monte Carlo and confidence intervals.
- Runs an unattended read-only shadow mode that records what happened to every candidate it saw.
- Dashboard: Overview, Opportunity Explorer, Historical Backtesting, Competition Lab, Flash-Loan Simulator, Settings.

## Quick start

```bash
./scripts/bootstrap.sh      # .env with generated DB password and your admin password hash
# add RPC endpoints to .env (BASE_RPC_HTTP, ARBITRUM_RPC_HTTP; *_ARCHIVE_RPC for historical replay)
docker compose up -d --build
open http://localhost:3000
```

Docs: [implementation plan](docs/implementation-plan.md), [architecture](docs/architecture.md),
[data sources](docs/data-sources.md), [simulation methodology](docs/simulation-methodology.md),
[deployment](docs/deployment.md), [limitations](docs/limitations.md), [Phase 2 results](docs/phase2-results.md),
[Phase 3 results](docs/phase3-results.md), [replication runbook](docs/runbook.md) (fresh server, providers, exact
commands, pitfalls). Contracts: [contracts/README.md](contracts/README.md). Notes for AI-assisted sessions: `CLAUDE.md`.

## Status

`docs/implementation-plan.md` has the per-milestone state and `docs/test-results.md` the deployment-readiness
results. Live results against the real chains: `docs/phase2-results.md` (deployment verification, indexing,
replays through the real receiver) and `docs/phase3-results.md` (profit verification with complete fee
accounting, intra-block reconstruction, live competitive benchmark, bid/win-probability model). Phase 3 is
reproduced with `scripts/phase3-research.sh <network>`.

## Tests

```bash
make test-backend     # pytest (needs Postgres + Redis reachable)
make test-contracts   # forge fork tests (needs Foundry and BASE_ARCHIVE_RPC)
make test-frontend    # Playwright
```
