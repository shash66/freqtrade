# Phase 2: live blockchain validation (2026-10-21)

Read-only throughout: no signer, no broadcast path, every transaction executed on a private Anvil fork.
Environment: cloud sandbox with chain access allowed to `base.drpc.org`, `arbitrum.drpc.org`,
`mainnet.base.org`, `arb1.arbitrum.io`, PublicNode and a keyed dRPC endpoint. No Alchemy/paid archive endpoint
was reachable during the run (variables added to the environment appear only in a fresh session container).
Status legend: EXECUTED (ran against the required chain state) · SKIPPED (tool reported skipped) · BLOCKED
(could not run; reason) · FAILED (ran and failed; reason).

## Provider capabilities measured

| Endpoint | eth_getLogs | Historical state | Notes |
|---|---|---|---|
| `base.drpc.org` (public) | ≤100-block spans, any age | yes (balances at block 16.7M served) | strict per-minute quota (HTTP 429); used as Base archive for forks |
| `base-rpc.publicnode.com` | 2,000-block spans, last ~10k blocks only | last ~128 blocks | used for Base live reads |
| `mainnet.base.org` | rate-limited (429) under indexing load | yes | not usable for indexing |
| `arbitrum-one-rpc.publicnode.com` | last ~10k blocks (≈50 min) | last ~128 blocks | reconciliation works; indexing falls behind |
| `arbitrum.drpc.org` / keyed `lb.drpc.live` | 100-block spans | **no** ("Unknown state. First available state is 1" beyond ~128 blocks) | Arbitrum replays impossible on these |

## 1. RPC connection and Aave deployment verification

| Network | Status | Evidence |
|---|---|---|
| Base (8453) | **EXECUTED** | chain id matched; Pool/Oracle/DataProvider bytecode present; `Pool.ADDRESSES_PROVIDER()` = address-book provider; `FLASHLOAN_PREMIUM_TOTAL` = 5 bps, `FLASHLOAN_PREMIUM_TO_PROTOCOL` = 100 %; head 52,902,092 at verification |
| Arbitrum One (42161) | **EXECUTED** | same checks passed; premium 5 bps / 100 % to protocol; head 516,955,190 |

## 2. Borrower discovery and liquidation records

| Network | Status | Evidence |
|---|---|---|
| Base indexing (last 20,000 blocks + 7-day backfill, partial) | **EXECUTED** | 157 live ranges + backfill ranges; 11,000+ Pool events; **4,057 genuine borrowers**; **24 real `LiquidationCall` transactions** with receipts (`fee_source = verified` on all 24, median gas used 3,092,787) |
| Base reconciliation | **EXECUTED** | 200 borrowers per run reconciled via `getUserAccountData` at the live head; 2–3 near-liquidation positions; 0 errors |
| Arbitrum indexing (100,000 blocks ≈ 7 h) | **EXECUTED** (then fell behind) | 925 ranges, 2,045 events, 476 borrowers, 0 liquidations in the window; later runs **FAILED** because PublicNode refuses log ranges older than ~10k blocks and dRPC has no archive there |
| Arbitrum reconciliation | **EXECUTED** | 200 borrowers reconciled at latest, 0 errors (after switching pinned-block reads to latest) |

Observed competing liquidators on Base (recorded on-chain, outcome class `onchain_actual`): 8 unique
liquidators over the 24 liquidations; top address `0xD128…4b3f` took 11 (46 %), `0x5AC9…95bd` and
`0xAB1c…A1aB` 3 each (12.5 %).

## 3–6. Replays through the real flash-loan receiver (Base)

Status: **EXECUTED** for 19 of the 24 recorded liquidations (the last 5 stalled on dRPC's quota after
~2.5 hours and are left for a provider with a request budget; the runner resumes from persisted results).
Raw data: `phase2-results-base.json` / `.md` (every replay with amounts, gas, fees, warnings, trace summary).

| Measure | Value |
|---|---|
| Execution path | `flash_loan_receiver` on all 19 (real `flashLoanSimple` → `liquidationCall` → SwapRouter02 swap → repayment); **0 simplified** |
| Succeeded on the fork | **3** (all profitable after costs) |
| Reverted | 16: 15 `HealthFactorNotBelowThreshold` / `MustNotLeaveDust` / swap-minimum on positions **not liquidatable at block N−1** (they became liquidatable inside block N, typically after an oracle update: a block-boundary limitation, not a defect); 1 `MustNotLeaveDust` on a position that *was* eligible at N−1, reproduced on a fresh fork, i.e. a genuine Aave v3.3 dust-rule revert for the historical amount at the earlier state |
| Net simulated profit (sum of the 3 successes) | **$3,120.76** (outcome class: simulated; not earned, not capturability-adjusted) |

Successful replays (measured on the fork; L1 fee estimated via the Base GasPriceOracle on the fork):

| Historical tx | Block | Debt repaid | Collateral received (fork / historical) | Swap out | Premium | Gas (fork / historical) | Net profit |
|---|---|---|---|---|---|---|---|
| `0x7ed3…3f3c` | 52,621,006 | 9.149694 USDC | 0.004116 WETH / identical | 9.653389 USDC | 0.004575 USDC (5 bps) | 569,713 / 362,177 | **+$0.4358** |
| `0xd103…1df1` | 52,639,120 | 0.965705 USDC | 0.000483 WETH / identical | 1.011101 USDC | 0.000483 USDC | 682,132 / 763,377 | **+$0.0374** |
| `0xa092…` | 52,620,870 | 66,106.884515 USDC | 29.402227 WETH / identical | 69,261.008785 USDC | 33.053443 USDC | 684,995 / 3,092,787 | **+$3,120.28** |

Fee figures (wei, from `fees` on each result): `0x7ed3` L2 execution 27,248,288,626,161 + L1 data 4,107,912,235;
`0xd103` 5,492,991,395,892 + 2,232,341,725; `0xa092` 212,084,726,925,000 + 1,354,829,502.

Fee completeness on successful replays (`fees.source = estimated`): L2 execution = measured gas × the
historical effective gas price; L1 data fee = `GasPriceOracle.getL1Fee` on the fork for the actual calldata;
flash premium = measured from the receiver event (exactly 5 bps of the repaid amount); protocol liquidation fee
= implicit in the measured collateral received (which matched the historical `liquidatedCollateralAmount`
to the wei); swap cost = measured on-chain through SwapRouter02. Receipt-verified L1 fees exist for all 24
historical transactions (`historical_liquidations.fee_source = verified`) and are reported alongside.

Observations worth stating plainly:
- Two of the three successes were positions that were **not** liquidatable at N−1 but became so in the pending
  block (interest accrual / the block's own oracle update); in a live race the window is the block itself.
- The large success (+$3,120) was captured on-chain by `0xD128…4b3f`, the dominant liquidator (46 % of
  recorded liquidations). Simulated profit says nothing about who would have won the block.
- Measured gas through the receiver (570k–685k) differs from the historical liquidators' (362k–3.09M);
  the receiver's cost model should use its own measured figures, not competitors' receipts.

Receiver compile status: `forge build` (Foundry 1.8.5 built from source here) compiles the contract and the
test suite; native `solc` 0.8.24 output is byte-identical to the solc-js artifact the simulator deploys.
Forge fork tests: **BLOCKED** at `setUp()` by dRPC's public quota (HTTP 429 while the fork fetched state);
they need an archive endpoint with a real request budget.

## 7. Initial report figures (Base, outcome classes kept apart)

| Figure | Value | Class |
|---|---|---|
| Real liquidations observed (≈1.4 days of history + 11 h live) | 24 | onchain_actual |
| Unique competing liquidators / top share | 8 / 46 % | onchain_actual |
| Replays executed through the receiver | 19 of 24 | simulated |
| Successful fork executions | 3 | simulated |
| Net simulated profit (sum) | $3,120.76 | simulated |
| Capturability | not measured in this phase (Competition Lab models it; no live race data) | modeled_capturable |

## Limitations of this phase

- Base only for replays; Arbitrum needs an archive provider (see provider table).
- Sample: 24 liquidations from a short window; the 7-day backfill stopped at block 52,640,886 under quota.
- Forks at N−1: 15 of 16 reverts are the block-boundary effect; intra-block replay would need per-tx state.
- Public-endpoint quotas dominated run time (hours); a keyed provider with a budget removes that.
- Forge fork tests not run (quota); `forge build` succeeded.

## Fixes made during this phase (committed)

- RPC client: exponential backoff on 429/quota responses, optional request throttle; health probes retry.
- Indexer: halves any failing `eth_getLogs` span down to 25 blocks regardless of provider wording.
- Reconciliation: reads at latest (pinned-block reads become archive requests within seconds on Arbitrum).
- Anvil: fork reset backs off on upstream rate limits.
- Replay: decodes Aave revert selectors; retries once on a fresh fork when an eligible position reverts with
  an eligibility/dust error (seen once under rate limiting, not reproducible); records measured vs historical
  amounts.
