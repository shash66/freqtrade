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
  `profit_complete = false` and excluded from every profit total.
- **Environment in which this build was produced.** No blockchain RPC was reachable, Foundry could not be
  installed (its build needs `binaries.soliditylang.org`), and the sandbox's Docker daemon had no NAT. So: the
  contract was compiled with solc-js, not `forge`; Forge fork tests and any fork simulation were not run; the
  Docker stack was started and verified (migrations, health checks, workers, persistence, browser tests) but
  host-port publishing and outbound RPC traffic from containers could not be exercised. `docs/test-results.md`
  lists every check with its actual result.
