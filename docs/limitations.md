# Limitations and honest caveats

- **No execution path exists.** There is no signer, no key storage, no broadcast. `LIVE_TRADING_ENABLED=true`
  aborts startup. Deciding to build an executor is a separate project (spec §19).
- **Block-boundary replay.** Historical replays fork at N−1 and cannot reproduce intra-block ordering, private
  order flow or sequencer timing. A "profitable simulation" is not evidence the opportunity was winnable.
- **Latency is modeled, not measured.** Competitor delays, inclusion behavior and expiry are Monte Carlo
  assumptions. Block timestamps are never treated as millisecond execution latency.
- **Quotes, not swaps.** Swap proceeds come from QuoterV2 quotes on the fork; execution could differ (MEV,
  pool state changes). L1 data fees on Base/Arbitrum are not included in gas costs.
- **Funding path.** The fork simulator borrows liquidity from the reserve aToken rather than taking a real flash
  loan; the premium is added analytically. The Solidity flash-loan path lives in `contracts/` and must be
  compiled and run with Foundry by the operator.
- **Archive dependency.** Historical replay and deep backfill need an archive RPC, usually paid.
- **Sample sizes.** Liquidation counts in a 7–90 day window on Base/Arbitrum may be well below the 100–500
  target; the dashboard reports the actual sample size and never extrapolates.
- **e-mode bonuses.** Candidates use the reserve liquidation bonus unless an e-mode override is supplied; the
  assumption is recorded on each candidate.
- **Funding path in simulations.** The default simulation path deploys the compiled receiver and takes a real
  Aave flash loan on the fork. The simplified direct-call path (impersonated funding, quoted swap, analytical
  premium) remains only as a labeled fallback (`simplified = true`).
- **Phase 2 live validation (Base).** Executed with public endpoints under strict quotas: 19 of 24 replays done,
  5 pending; Arbitrum replays need an archive provider. Details and per-replay data in `phase2-results.md`.
- **Phase 3 (profit verification and competition).** Results in `phase3-results.md`. What it does not do:
  no transaction is ever built or sent, so every "win probability" is a model over observed winners' bids
  and reactions, not a measured execution record; the live benchmark window is short (hours) and liquidations
  are rare, so competitor-reaction samples may be zero (reported as "not estimable", never as 0 or 100 %);
  the intra-block reconstruction re-executes transactions on a fork with base fee 0 and one block per
  transaction, so contracts reading `block.number`/`basefee` see different values; `--relevant-only` skips
  transactions that cannot touch Aave or oracle state; the theoretical figure assumes a free instant exit of
  the collateral at oracle prices. A replay whose L1 fee or any other material cost is unknown is reported as
  `profit_complete = false` and excluded from every profit total. The 150-minute live benchmark observed no
  liquidation on the Pool and no position crossing HF < 1 in its watch set, so candidate lifetime and the
  FCFS win probability are "not estimable" for that window; simulation latency was measured in a separate
  short run on an Alchemy-forked Anvil because the PublicNode fork refused the fork's state calls (HTTP 403).
- **Phase 4.** The dataset is 24 Base and 4 Arbitrum events; intervals are wide and the verdicts are about this
  sample, not the market in general. Observability is judged from the aggregator behind the Pool's price source
  read as an ordinary caller; feeds without an `aggregator()` are reported with type `?` and no lead. The win
  model compounds the empirical winning-bid distribution over the number of liquidators with ≥ 5 % of recent
  events; the failed-attempt cost is a stated input. The fast path uses the reserve liquidation bonus (not the
  e-mode bonus) and the configured gas estimate; the full path corrects both. Compound V3 valuation covers the
  storefront discount only (no execution path, swap or gas model). Markets on chains without an allowed endpoint
  in this environment are registered but unverified.
- **Time-gated oracle feeds.** On Base (and for one Arbitrum event) the feed Aave reads already held the
  price that made a position liquidatable at N−1 but revealed it only after a delay; the winner's own
  transaction revealed it early. The public health factor this system computes lags that hidden round. A bot
  that only reads public oracle state cannot see these opportunities before the winner acts; reading the
  pending round from raw storage, or participating in the feed's reveal mechanism, is a separate research
  question this project has not pursued and that may involve the feed operator's own auction.
- **Environment in which this build was produced.** No blockchain RPC was reachable, Foundry could not be
  installed (its build needs `binaries.soliditylang.org`), and the sandbox's Docker daemon had no NAT. So: the
  contract was compiled with solc-js, not `forge`; Forge fork tests and any fork simulation were not run; the
  Docker stack was started and verified (migrations, health checks, workers, persistence, browser tests) but
  host-port publishing and outbound RPC traffic from containers could not be exercised. `docs/test-results.md`
  lists every check with its actual result.
- **Phase 5 (multi-strategy).** Compound V3 discovery looks at accounts that withdrew in the last 5 h of chain
  time (plus any configured list), not at every borrower; a borrower idle for days is invisible to it until a
  full backfill exists. The Compound competitor history covers the Phase 3 window (1.9 days on Base, extended
  backwards when the slow 10-block Alchemy scan finishes) and holds a handful of absorbs, all dust; there is no
  Arbitrum Comet history. DEX arbitrage samples block boundaries: spreads that open and close inside a block
  (back-running) are not observed, and the registered pool set is small (WETH/USDC, USDC/USDbC, cbBTC pairs on
  Base; WETH/USDC, USDC/USDT, USDC/USDC.e on Arbitrum). Capture probability for arbitrage is a persistence proxy,
  not a model of searcher competition; when no spread was ever executable the proxy is undefined and reported as
  such. Yield figures are today's rates held for 30 days; they float with utilisation and are passive, with
  smart-contract, depeg and (for loops) liquidation risk that the numbers do not price. Infrastructure cost is an
  explicit estimate ($69/month) and not measured. Shadow windows are 30 minutes; replays cover days, not months.
