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
