# FlashLiquidator Lab — project notes for AI-assisted sessions

Read this before touching the project. It records what the project is, the rules it follows, the state of
the research, and the operational facts that cost hours to learn. Human-facing docs are in `docs/`;
`docs/runbook.md` is the replication guide.

## What this is

Simulation-only research platform for Aave V3 flash-loan liquidations on Base and Arbitrum: FastAPI +
web3.py backend, PostgreSQL + Redis + Celery, Next.js dashboard, a Solidity flash-loan receiver with Forge
fork tests, Anvil-based replay/simulation tools, and a competition research toolkit. It answers one
question with evidence: can a liquidation strategy capture positive net profit after fees, gas and
competition? The current answer, with data, is in `docs/phase3-results.md`.

## Non-negotiable rules

- **No execution path, ever.** No wallet, no signer, no key storage, no public-chain transaction submission.
  `LIVE_TRADING_ENABLED` must stay `false`; the Settings validator aborts startup on any other value. Every
  "execution" runs on a private Anvil fork. Do not add signing or broadcasting without a separate review.
- **Four outcome classes stay apart and labeled**: `theoretical` (protocol math), `simulated` (our receiver
  on a fork), `modeled_capturable` (Monte Carlo / bid model), `onchain_actual` (what competitors did).
  Never add them together or present one as another.
- **Never invent a number.** Missing inputs make a result `incomplete`/`unknown`, never zero or an estimate
  presented as measured. A replay with any unknown material cost has `profit_complete = false` and is never
  counted as net profitable. Latencies are measured with monotonic clocks; block timestamps are never
  reported as execution latency.
- **Counts are normalised**: unique historical transactions, liquidation events (one per `LiquidationCall`
  log) and simulation attempts (fork attempts incl. retries) are reported separately; an event's outcome is
  the final attempt of its latest run in the batch.
- **Tests never touch the validation database.** `tests/conftest.py` forces `TEST_DATABASE_URL`
  (default `.../flashliquidator`); validation data lives in `.../flashliquidator_live`.

## Layout

```
backend/app/
  aave/           address book (v4.44.22), ABIs, reads (AaveReader), reserve-config bitmap decoding
  chains/         RpcClient (429 backoff, throttle), l1fees (OP-stack GasPriceOracle, Arbitrum NodeInterface), validation
  indexer/        checkpointed Pool-event indexer with reorg rollback; reconcile (positions at latest); cli (backfill/once/reconcile)
  strategies/     profitability engine, quotes (Uniswap QuoterV2), predictive, shadow mode
  simulation/     anvil (fork lifecycle), receiver (deploy + liquidate()), replay (historical replay), accounting (profit reconciliation),
                  reconstruct (intra-block replay + oracle time-gate probe), artifacts/FlashLiquidator.json
  competition/    strategies A/B/C, montecarlo, analysis, benchmark (live read-only), bidmodel (observed vs modeled)
  validation/     phase2 runner (--batch/--only-tx/--fresh/--blocks 0), theoretical valuation, phase3_report
  api/ workers/ database/ core/
contracts/        FlashLiquidator.sol (Foundry 1.8.5, solc 0.8.24), 16 fork tests (need BASE_ARCHIVE_RPC)
frontend/         Next.js 15 dashboard (6 pages, admin basic auth), Playwright tests
docs/             methodology, limitations, deployment, data sources, test results, phase2/phase3 results + raw JSON, runbook
scripts/          bootstrap, backfill, compile-contracts (solc-js artifact), phase2-validate, phase3-research
```

## Commands

```bash
cd backend && ruff check app tests && python -m pytest -q          # 75 tests; needs Postgres + Redis
cd contracts && forge test --fork-url "$BASE_ARCHIVE_RPC"            # 16 fork tests at Base block 30,000,000
./scripts/compile-contracts.sh                                       # refresh the simulator artifact after any .sol change
scripts/phase3-research.sh base                                      # whole Phase 3 pipeline through docker compose
```

Alembic migrations: `19ea7575a02f` initial, `9de9d0ca965e` fee accounting, `c55ca9144780` reconciliation/eligibility.
After a Solidity change: `forge build`, `scripts/compile-contracts.sh`, compare bytecode (identical except the
CBOR metadata hash), rerun fork tests, rerun affected replays with `--fresh`.

## Research state (22 Oct 2026)

- Base: 24 liquidation events in 15 transactions indexed (~1.5 days), all replayed through the real receiver on
  Alchemy archive forks; 6 succeed with complete costs (sum $3,120.80), 2 Aave-rule reverts while eligible, 16
  not liquidatable at N−1. Arbitrum: 4 events, 1 succeeds (unprofitable after costs).
- **Key finding**: the Aave price feeds on Base are time-gated. For 18 of 24 Base events (1 of 4 Arbitrum)
  the feed already stored the round that made the position liquidatable at N−1 and revealed it 0/15/30 s
  later; the $3,120 winner revealed the round inside its own transaction and liquidated in the same call
  (block 52,620,870, tx index 653, trigger invisible to public state). Boundary-polling bots cannot see
  these; the capturable simulated profit from public state was $0.44 over the sample.
- Live benchmark (150 min, 4,380 Base blocks, 348 watched positions): block lag 0.32 s median, detection 1.33 s
  median (3 Multicall3 trips), fork simulation 17.2 s median; 0 liquidations in the window, so FCFS win
  probability is "not estimable" (never 0 or 100 %).
- Competitors on Base: 8 liquidators, top address 0xD128…4b3f with 46 % of events; winning effective gas
  price median 0.04 gwei, p90 0.30 gwei.
- Known gap: four test-fixture rows remain in the validation DB (see runbook §5); all queries exclude them.

## Phase 4 state (22 Oct 2026)

- Oracle mechanism: Aave's Base/Arbitrum price sources are Chainlink `DualAggregator` proxies. The aggregator
  returns the newest round to any ordinary caller as soon as it lands (usually block N−1); the proxy the Pool reads
  returns it only inside the auction winner's transaction or after a 30 s gate. Base: 3 publicly observable, 17
  predictable (16 via the aggregator, lead median 2 s, max 32 s), 4 intra-block only; 83 % observable before the
  winner. 10 of 17 predictable events were still contestable at the reveal (share 0.59).
- Latency (Base, PublicNode): fast path 373 ms median (reads 211, quotes 220, math 0.04), fork simulation 10.6 s.
  Arbitrum 343 ms / 11.1 s. A fork gate can never be ready inside a 2 s block.
- Strategy verdicts (same 24 Base events, seed 42): no public-data strategy positive; ceiling $3,120.80.
- Markets: 20 registered (Aave V3 ×16, Compound V3 ×4), 6 verified live, 14 need endpoints for other chains.
- New modules: `simulation/fast_path.py`, `competition/{latency,attainable,compare}.py`, `research/observability.py`,
  `protocols/{base,aave_v3,compound_v3,registry}.py`, `validation/phase4_report.py`, `api/routes_research.py`,
  frontend `/research`. Migration `ecdc7637b17c` (attainable columns).

## Operational facts (learned the hard way)

- Forks must use the archive endpoint (PublicNode returns 403 for pinned-block reads within seconds).
- Alchemy free: `eth_getLogs` ≤ 10 blocks, ~330 CU/s per key shared by every Anvil using it; two heavy forks on
  one key stall each other. dRPC public: 100-block logs, 429 quotas, no Arbitrum archive. PublicNode: 2,000-block
  logs within ~10k blocks, state for ~128 blocks.
- Anvil does not emulate Arbitrum's NodeInterface: L1 fee estimates for Arbitrum come from the real node.
- One Anvil per job. Ports used: 8545 Base (batch), 8546 Base (reconstruction), 8548 Arbitrum.
- `pgrep -f`/`pkill -f` patterns in a command line match the command itself: put them in a script file.
- Same-asset liquidations (collateral token == debt token) exist in live data; the receiver skips the swap and
  corrects the collateral delta for the principal pulled by `liquidationCall`.
- Foundry 1.8.5 from source works; `vm.expectRevert(bytes4)` needs `expectPartialRevert` for errors with args.
- Compose: `$` in bcrypt hashes must be `$$`; use `${VAR:-}` not `${VAR:?}` for inactive profiles;
  Next standalone needs `HOSTNAME=0.0.0.0`.

## Conventions when extending

- Record every assumption on the result object (`assumptions`, `warnings`); say "estimated" vs "verified".
- Classify failures (`validation/phase2.py` triage) rather than dropping them; a crash is a result too.
- New research tools: pure functions with unit tests for the math/classification, a CLI that writes JSON to
  `docs/`, and a section in `validation/phase3_report.py` so the report regenerates from artefacts.
- Keep `docs/limitations.md` honest; update `docs/test-results.md` with what actually ran.
