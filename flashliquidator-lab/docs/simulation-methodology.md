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

## Competition model (modeled_capturable)
A seeded Monte Carlo draws our detection/simulation/RPC delays per strategy and competitor delays, applies
an exponential opportunity lifetime, and resolves inclusion first-come-first-served (L2 sequencers) or by
priority bid (Ethereum). It reports win rate with a Wilson 95% interval, expected profit per attempt with a
normal-approximation interval, failed-attempt exposure and delay sensitivity. All inputs are adjustable and
echoed back with the result. These are modeled estimates, never measured execution results.
