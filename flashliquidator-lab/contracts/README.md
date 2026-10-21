# FlashLiquidator Lab — contracts

Solidity package for the **Aave V3 liquidation research platform**. It contains one contract,
`FlashLiquidator`, its Foundry fork tests, and a deploy script that targets a **local Anvil fork only**.

> **Research tool. Fork-simulation only. No production audit. No mainnet deployment path exists in
> this repository.**

> **⚠️ This package has NOT been compiled or tested in the environment it was written in.**
> Foundry (`forge`, `anvil`) could not be installed there (download host blocked). The code targets
> `solc 0.8.24` / `forge` and was written carefully, but **you must run `forge build` and
> `forge test` yourself** before relying on anything here. See "Known compile risks" at the end.

## What `FlashLiquidator` does

`src/FlashLiquidator.sol` is an Aave V3 `flashLoanSimple` receiver that performs exactly one
fixed sequence per call:

1. `liquidate(LiquidationParams)` — **owner only**, reentrancy-guarded. Pre-checks deadline and the
   router/token allowlists, then calls `POOL.flashLoanSimple(this, debtAsset, debtToCover, abi.encode(p), 0)`.
2. `executeOperation(...)` — callable only by the Pool, only for loans this contract initiated.
   - records `startBalance = balanceOf(debtAsset) - amount` so pre-existing balances can never
     subsidise the trade;
   - `forceApprove(POOL, debtToCover)` and `POOL.liquidationCall(collateral, debt, user, debtToCover, false)`;
   - measures the collateral received (net of Aave's liquidation protocol fee) as a balance delta;
   - `forceApprove(router, received)` and swaps it back to the debt asset with Uniswap v3
     SwapRouter02 `exactInputSingle` (`amountOutMinimum` is the slippage bound, `sqrtPriceLimitX96 = 0`);
   - requires `balance - startBalance >= amount + premium + minProfit`, else reverts
     `InsufficientProfit(actual, required)`;
   - `forceApprove(POOL, amount + premium)` (the Pool pulls the repayment), forwards the surplus to
     `profitRecipient`, emits `SimulatedLiquidation(...)` and returns `true`.

There is **no** generic `execute(target, data)` and no arbitrary external call. Every revert path is a
custom error (`Unauthorized`, `NotPool`, `BadInitiator`, `Expired`, `TokenNotAllowed`,
`RouterNotAllowed`, `InsufficientProfit`, `SwapFailed`, plus `FlashLoanMismatch`,
`NoCollateralSeized`, `ZeroAddress`, `ZeroAmount`). Ownership uses OpenZeppelin `Ownable2Step`;
approvals/transfers use `SafeERC20` (`forceApprove` / `safeTransfer`).

### `LiquidationParams`

| field | meaning |
|---|---|
| `collateralAsset` | collateral to seize (must be allowlisted) |
| `debtAsset` | debt asset flash-borrowed and repaid (must be allowlisted) |
| `user` | borrower |
| `debtToCover` | flash-loan amount and `liquidationCall` amount |
| `swapFeeTier` | Uniswap v3 fee tier, e.g. `500` |
| `minProfit` | minimum surplus in debt-asset units, else `InsufficientProfit` |
| `amountOutMinimum` | swap slippage bound in debt-asset units |
| `deadline` | unix timestamp, else `Expired` |
| `router` | SwapRouter02 address (must be allowlisted) |

### Owner functions

| function | purpose |
|---|---|
| `setRouterAllowed(router, bool)` | manage the router allowlist |
| `setTokenAllowed(token, bool)` | manage the token allowlist (collateral and debt assets) |
| `setProfitRecipient(addr)` | change where surplus goes |
| `rescueToken(token, to)` | **research convenience**: sweep the contract's full balance of `token` to `to`. On a fork, tokens may be `deal`t to the contract or a partial liquidation may leave dust; the contract never relies on holding a balance, so sweeping cannot break a later run. Owner only; reverts `ZeroAmount` if there is nothing to sweep. |
| `transferOwnership` / `acceptOwnership` | two-step ownership transfer (OpenZeppelin) |

`liquidate` reverts with the package's own `Unauthorized(caller)`; the administrative setters use
OpenZeppelin's `onlyOwner` (`OwnableUnauthorizedAccount`).

## Layout

```
contracts/
├── foundry.toml            solc 0.8.24, optimizer on, evm cancun, via_ir off, no fs permissions, no pinned RPCs
├── remappings.txt          @openzeppelin/contracts, aave-v3-origin, forge-std
├── src/
│   ├── FlashLiquidator.sol
│   └── interfaces/         minimal local interfaces (IPool, IFlashLoanSimpleReceiver,
│                           IPoolAddressesProvider, IERC20, ISwapRouter02, IAaveOracle)
├── test/FlashLiquidator.t.sol   Base fork tests
└── script/Deploy.s.sol          local-Anvil-only deployment
```

Only three OpenZeppelin files are imported by the contract (`SafeERC20`, `ReentrancyGuard`,
`Ownable2Step`, plus `Ownable` which `Ownable2Step` extends). `aave-v3-origin` is installed for
reference and for anyone who wants to cross-check the local interfaces, but nothing imports it.

## Install, build, test

```bash
cd contracts

# 1. dependencies (git submodules under lib/; add --no-git if you do not want submodules)
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts@v5.0.2 aave-dao/aave-v3-origin

# 2. build
forge build

# 3. fork tests — require a Base ARCHIVE RPC (the pinned block is ~May 2025)
export BASE_ARCHIVE_RPC="https://<your-base-archive-endpoint>"
forge test --fork-url $BASE_ARCHIVE_RPC -vvv

# optional: pin a different fork block
BASE_FORK_BLOCK=30000000 forge test --fork-url $BASE_ARCHIVE_RPC -vvv
```

If `BASE_ARCHIVE_RPC` is **not** set, every test is reported as **skipped** (`vm.skip(true)`) with a
console message — never as passed. This follows the project rule "mark as skipped with a clear
explanation, not as passed".

### What the tests cover

All tests fork Base at block `30_000_000` (override with `BASE_FORK_BLOCK`) and use the Aave V3 Base
market (`PoolAddressesProvider 0xe20f…ad64D`, `Pool 0xA238…d1c5`, `Oracle 0x2Cc0…e156`), WETH
`0x4200…0006`, USDC `0x8335…2913` and Uniswap v3 SwapRouter02 `0x2626…e481`.

Because no specific unhealthy borrower is known in advance, `setUp` creates one: a fresh address is
`deal`t 10 WETH, supplies it and borrows USDC at ~98 % of the reserve's LTV. Tests that need a
liquidatable position then mock the oracle (`vm.mockCall` on `getAssetPrice(WETH)` and
`getAssetsPrices([WETH])`) so that the health factor lands at 0.90 and assert `healthFactor < 1e18`.

| test | checks |
|---|---|
| `test_Liquidate_PaysSurplusToRecipient` | surplus > 0 goes to `profitRecipient`; contract holds nothing afterwards; borrower HF improved |
| `test_Liquidate_RepaysPrincipalPlusPremium` | event premium == `amount.percentMul(FLASHLOAN_PREMIUM_TOTAL)`; aUSDC balance delta == `debtToCover + premium` |
| `test_Liquidate_SwapsAllCollateral` | collateral balance zero, debt asset increased, `swapOut == premium + surplus` |
| `test_Liquidate_ProtocolFeeAccounting` | received collateral == seized-with-bonus minus protocol fee (bits 32-47 and 152-167 of `getConfiguration`), within 1 wei |
| `test_RevertWhen_MinProfitNotMet` | `InsufficientProfit` |
| `test_RevertWhen_UserIsHealthy` | Aave rejects the liquidation without the oracle mock |
| `test_RevertWhen_InitiatorIsNotThisContract` / `…ExecuteOperationNotFromPool` / `…LiquidateCalledByNonOwner` | `BadInitiator`, `NotPool`, `Unauthorized` |
| `test_RevertWhen_TokenNotAllowed` / `…RouterNotAllowed` | allowlists |
| `test_RevertWhen_SlippageTooHigh` | router's `"Too little received"` bubbles up through the flash loan |
| `test_RevertWhen_DeadlineExpired` | `Expired` |
| `test_RescueToken`, `test_OwnerOnlySettersAndTwoStepOwnership`, `test_ConstructorWiresPoolFromProvider` | administration |

**Interpretation caveat:** the oracle mock lowers Aave's view of the WETH price while Uniswap still
trades at the real market price, so the surplus the tests observe is deliberately unrealistic. The
tests verify *mechanics and accounting*, not profitability.

## Deploy to a local Anvil fork (only)

```bash
# terminal 1
anvil --fork-url $BASE_ARCHIVE_RPC --fork-block-number 30000000

# terminal 2
export ANVIL_RPC=http://127.0.0.1:8545
# an Anvil fork keeps chain id 8453, so the production-chain guard must be acknowledged explicitly:
FORK_SIMULATION_ONLY=true forge script script/Deploy.s.sol:Deploy --rpc-url $ANVIL_RPC --broadcast --unlocked
```

`script/Deploy.s.sol`:

- refuses to run if `ANVIL_RPC` is unset (`AnvilRpcNotSet`);
- refuses to run on chain ids `1`, `8453`, `42161` unless `FORK_SIMULATION_ONLY=true` (`ProductionChainRefused`);
- never reads a private key: it broadcasts with `vm.startBroadcast(address)` impersonating Anvil's
  default account #0 (`0xf39F…2266`), which only works against a node that has that account unlocked
  (hence `--unlocked`);
- optional env overrides: `AAVE_POOL_ADDRESSES_PROVIDER`, `SWAP_ROUTER02`, `WETH`, `USDC`,
  `ANVIL_DEPLOYER`, `PROFIT_RECIPIENT`.

## Known compile risks (unverified — please check when you build)

1. `FlashLiquidator.sol` uses OpenZeppelin's `IERC20` (because `SafeERC20` is typed on it) while
   tests and scripts use the minimal local `src/interfaces/IERC20.sol`. No single file imports both,
   so there is no symbol clash, but if you add a file that does, alias one of them.
2. `executeOperation` is split into `_run` / `_liquidateAndSwap` / `_settle` to stay under the
   16-slot stack limit with `via_ir = false`. If `forge build` still reports "stack too deep", set
   `via_ir = true` in `foundry.toml`.
3. The fork tests rely on `vm.envOr(string,string)`, `vm.skip(bool)`, `vm.mockCall`, `vm.recordLogs`
   and `deal` from forge-std ≥ 1.7. `vm.skip` is called from a modifier inside each test (not from
   `setUp`) so it works on older forge versions too.
4. `IPool.getReserveData` is declared with Aave's ABI-stable "legacy" 15-field struct; it is only used
   in one test to find the aUSDC address.
5. `test_RevertWhen_SlippageTooHigh` expects the exact revert string `"Too little received"` from
   SwapRouter02; `test_RevertWhen_UserIsHealthy` uses a generic `expectRevert()` because Aave's error
   encoding differs between 3.x versions.
6. The synthetic borrower liquidates 40 % of its debt to stay clear of Aave 3.3's close-factor and
   minimum-leftover rules; if the fork block runs a different Aave version with different constants,
   adjust `DEBT_SHARE_PCT` / `WETH_SUPPLY`.
