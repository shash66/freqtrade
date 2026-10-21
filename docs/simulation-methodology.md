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
2. The simulator account is funded with the debt asset from the reserve's aToken (impersonated). This
   replaces the flash loan; the premium is added from `FLASHLOAN_PREMIUM_TOTAL` read on the fork. When the
   compiled `FlashLiquidator` contract is available the real flash-loan path can be exercised with Forge
   (see `contracts/`).
3. `Pool.liquidationCall` is executed for real on the fork; results come from balances and events.
4. Swap proceeds are quoted (QuoterV2) on the fork, not executed. Price impact is reported against oracle value.
5. Gas = measured `liquidationCall` gas + modeled flash-loan/swap overhead; priced at the historical
   effective gas price when replaying, otherwise the fork's gas price. L1 data fees on L2s are not included
   and are listed as an assumption.

Every historical result is labeled `block_boundary_approximation = true`: forking at N−1 does not reproduce
transaction ordering inside block N. A borrower who was only liquidatable because of an earlier transaction
in the same block shows as "not eligible at fork" and is reported, not dropped.

## Competition model (modeled_capturable)
A seeded Monte Carlo draws our detection/simulation/RPC delays per strategy and competitor delays, applies
an exponential opportunity lifetime, and resolves inclusion first-come-first-served (L2 sequencers) or by
priority bid (Ethereum). It reports win rate with a Wilson 95% interval, expected profit per attempt with a
normal-approximation interval, failed-attempt exposure and delay sensitivity. All inputs are adjustable and
echoed back with the result. These are modeled estimates, never measured execution results.
