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
- **Environment in which this build was produced.** No Docker daemon, no Foundry, and no blockchain RPC were
  reachable, so: Compose files were validated but not started; Solidity was written but not compiled; the
  indexer, reconciliation, candidate engine and replay service were exercised with unit tests and fakes, not
  against live chains. Each of these is a first thing to run after deployment.
