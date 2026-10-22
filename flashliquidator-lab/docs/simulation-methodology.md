# Simulation methodology

## Candidate math (theoretical)
Formulas mirror `LiquidationLogic.sol` of aave-v3-origin:

- Eligible when health factor < 1 (WAD).
- Close factor: 100% when HF < 0.95, or (v3.3+) when the user's debt or collateral **in the chosen reserve** is
  below 2,000 USD; otherwise 50%. Rules are parameters of `LiquidationRules`, not assumptions.
- Collateral seized = debtToCover × debtPrice / collateralPrice × liquidationBonus (percentMul rounding as in
  the protocol), capped by the user's collateral balance (then debtToCover is reduced with percentDiv).
- Protocol fee = (seized − seized/bonus) × liquidationProtocolFee, taken from the liquidator's collateral.
- v3.3 dust rule: when both sides are partially liquidated, remaining debt and collateral must each be ≥ 1,000 USD.
- Column semantics: `liquidation_candidates.collateral_seized_raw` is the total taken from the borrower
  (liquidator share + protocol fee, with `protocol_fee_raw` alongside), while the on-chain `LiquidationCall`
  event's `liquidatedCollateralAmount` stored in `historical_liquidations` is the liquidator's share after
  the fee. Compare predicted vs. historical using `collateral_seized_raw − protocol_fee_raw`.
- E-mode: when the borrower is in an e-mode category, the category's liquidation bonus for that collateral is
  read from the Pool; if it cannot be read the candidate is marked incomplete rather than priced with the
  reserve bonus.
- Net profit = swapProceeds − debtToCover − flashPremium − gas − extra costs. Any missing input makes the
  candidate `incomplete`; it is never filled with an estimate.

## Fork simulation (simulated)
1. Anvil forks the archive RPC at block N−1 (historical) or the latest block (candidate).
2. **Execution path `flash_loan_receiver`** (default): the compiled `FlashLiquidator` receiver is deployed on
   the fork, allow-listed for the two tokens and Uniswap SwapRouter02, and `liquidate()` is called. The
   receiver takes a real `flashLoanSimple`, calls `Pool.liquidationCall`, swaps the collateral on-chain and
   repays principal plus premium. Premium, swap output, collateral received, gas and surplus are **measured**
   from the transaction and its `SimulatedLiquidation` event. `simplified = false` on these results.
3. **Execution path `direct_liquidation_call`** (fallback only; `simplified = true`, warning attached): used
   when the artifact or SwapRouter02 is unavailable on the fork or `force_direct_path` is requested. The
   simulator account is funded from the reserve's aToken, `liquidationCall` runs for real, the swap is quoted
   (QuoterV2, not executed) and the premium is added analytically.
3b. **Same-asset liquidations** (collateral token == debt token, seen for USDbC, cbBTC, USDT and WETH
   positions in live data): the receiver skips the swap, reports `swapOut = collateralReceived`, and
   measures the collateral delta net of the principal the Pool pulled during `liquidationCall`; the
   replay skips the pre-quote. Both were found by Phase 3 replays (router revert, then a reconciliation
   mismatch) and fixed in the contract.
4. Fees: L2 execution = measured gas × gas price (historical effective gas price when replaying, otherwise
   the fork's, flagged). L1 data fee = chain fee-oracle estimate on the actual calldata (`source: estimated`);
   verified L1 fees come only from mined receipts (`source: verified`, stored on historical liquidations).
   A missing figure is `unknown`, never zero.

Every historical result is labeled `block_boundary_approximation = true`: forking at N−1 does not reproduce
transaction ordering inside block N. A borrower who was only liquidatable because of an earlier transaction
in the same block shows as "not eligible at fork" and is reported, not dropped.

5. **Eligibility is read twice** (Phase 3): `eligible_at_fork` at the state of N−1 (what a boundary-polling
   bot observes) and `eligible_at_execution` after an empty block pinned to block N's timestamp (interest
   accrued, none of block N's transactions applied). Both are stored; neither is inferred from the other.
6. **Profit reconciliation** (`app/simulation/accounting.py`): the surplus is recomputed from the measured
   balances (`swapOut − repaid − premium`) and compared with the receiver event; the fee is the sum of the
   measured L2 execution cost and the L1 data fee; `net_profit_debt_raw`, `fee_usd` and `net_profit_usd` are
   derived from those and stored with `profit_complete`. A result whose L1 fee (or any other material cost)
   is unknown keeps `profit_complete = false` and is **never** counted as net profitable, whatever its
   surplus. Tests: `tests/test_accounting.py`.
7. **Reporting normalisation**: unique historical transactions, liquidation events (one per `LiquidationCall`
   log) and simulation attempts (fork attempts including retries) are counted separately; every outcome is
   the final attempt of the event's run in the batch, so retries cannot inflate success or profit counts
   (`summarize_replays`, tests in `tests/test_report_normalization.py`).

## Intra-block reconstruction (simulated, per transaction)
`app/simulation/reconstruct.py` forks at N−1, pins block N's timestamp, then re-executes block N's
transactions in their original order by impersonating each sender (base fee 0, each transaction in its own
fork block), reading the borrower's health factor and the oracle prices of the two reserves after each one.
The first transaction after which HF < 1 is the **trigger**; it is classified from the on-chain receipt logs
(Chainlink `AnswerUpdated`/`NewTransmission` → oracle update; Pool events naming the borrower → borrower
action; the Pool's `LiquidationCall` → liquidation). `--relevant-only` replays only transactions whose logs
touch the Pool, the oracle, a reserve token or a Chainlink feed (the only ways the position's health factor
or prices can change); the full mode replays everything. Optionally the receiver is executed on a snapshot
taken right before the winning liquidation (`simulated_at_position`), i.e. at the exact intra-block state.

**Time-gate probe.** Before replaying, the tool forks at N−1, applies no transaction, advances only the
block timestamp from block N's timestamp in 5 s steps and reads the oracle prices of the two reserves. A price
that changes with time alone proves the feed already stored a newer round at N−1 and reveals it only after a
delay (time-gated / SVR-style aggregator); the reveal delay is recorded and a later price change during the
replay that matches the hidden round is classified `time_gated_oracle_reveal`, not attributed to the
transaction after which it was first read. `--time-gate-only` runs just this probe for any block.

## Verified theoretical profit (theoretical)
`app/validation/theoretical.py` values the amounts of every recorded `LiquidationCall` at the Aave oracle
prices of the winning block (archive read) and subtracts the winner's receipt-verified fee once per
transaction. It is an upper bound for a free, instant exit of the collateral and is reported next to, never
mixed with, simulated and modeled figures.

## Competitive benchmark (observed) and bid model (modeled)
`app/competition/benchmark.py` runs read-only against the live chain and records, with a monotonic clock,
block observation lag, detection latency (one Multicall3 read of every watched position), simulation latency
on a private Anvil, candidate lifetime (blocks until liquidated by a competitor or healed) and every
competitor `LiquidationCall` in the window with its position, gas price and distance from our detection.
`app/competition/bidmodel.py` turns the observed winning gas prices and liquidator concentration into a
priority-auction win probability (`P(win) = F(bid)^k` over the empirical distribution of winning bids) and the
observed reaction times into an FCFS win probability, and values each simulated-profitable event as
`p_win × (profit − our cost) − (1 − p_win) × failed-attempt cost`. Events that were only liquidatable inside
the winning block are reported in their own bucket and excluded from capturable totals. Every output states
which numbers are observed and which are modeled.

## Competition model (modeled_capturable)
A seeded Monte Carlo draws our detection/simulation/RPC delays per strategy and competitor delays, applies
an exponential opportunity lifetime, and resolves inclusion first-come-first-served (L2 sequencers) or by
priority bid (Ethereum). It reports win rate with a Wilson 95% interval, expected profit per attempt with a
normal-approximation interval, failed-attempt exposure and delay sensitivity. All inputs are adjustable and
echoed back with the result. These are modeled estimates, never measured execution results.

## Phase 4 additions

**Two simulator paths.** The fast path (`app/simulation/fast_path.py`, `POST /api/simulations/fast`) reads every
protocol input in one Multicall3 call, runs the pure profitability math, and quotes every Uniswap fee tier plus
the L1 data fee in a second Multicall3 call: a theoretical candidate with complete costs in three round trips,
each stage timed. The full path (`app/simulation/replay.py`, `POST /api/simulations`) forks and executes the real
receiver. They never share a result; the fast path is labeled `theoretical`, the full path `simulated`.

**Observability categories** (`app/research/observability.py`), per recorded event at the state of N−1:
`publicly_observable` (liquidatable with the Pool's own oracle prices), `predictable_not_yet_eligible` (the
aggregator behind the Pool's price source, read as an ordinary caller, already carried a newer round under which
the position is liquidatable, verified by re-pointing the oracle's source on a private fork; or interest alone
makes it liquidatable at block N's timestamp), `intra_block_only` (no public price or time signal before block N),
`unknown` (a probe failed). The lead is the time between the aggregator first carrying the round and the winning
block; whether a public bot can act in that window depends on the proxy's reveal rule (Phase 3 time gate).

**Protocol adapters** (`app/protocols/`): `MarketSpec` + `ProtocolAdapter` (verify, position, value_liquidation,
historical_liquidations). Aave V3 adapter over the 16 markets of the official address book; Compound V3 (Comet)
adapter on Base and Arbitrum with eligibility from `isLiquidatable`, value from `quoteCollateral`'s storefront
discount and history from `AbsorbCollateral`. Markets whose endpoint is not configured or reachable report
`not_configured`/`blocked`; nothing is assumed about them.

**Attainable net profit** (`app/competition/attainable.py`): `p_capture × net − (1 − p_capture) × failed-attempt
cost`, where `p_capture` is the observability-class weight (publicly observable 1, predictable = measured share
still contestable, intra-block 0, unknown 0) times the priority-auction win probability at the chosen bid over the
Pool's observed winning bids. Candidates are ranked by it; unknown inputs leave it null.

**Strategy comparison** (`app/competition/compare.py`): strategies A (poll + fast path + fork gate), B (poll + fast
path), C (read the aggregators, precompute, submit at the reveal block) and D (ceiling, no competition) on exactly
the same events and seed. Readiness is a log-normal draw fitted to the measured median/p90 latencies against the
event's window (one block, or the measured lead); win probability from the empirical winning bids; availability of
predictable events measured from the reveal timing; profit per captured event is the Phase 3 simulated net profit
with complete costs. Bootstrap intervals over events. A verdict is positive only when the whole interval is.
