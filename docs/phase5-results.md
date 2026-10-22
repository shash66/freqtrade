# FlashLiquidator Lab — Phase 5 results: multi-strategy profitability research

Generated 2026-10-22 15:19 UTC by `app.validation.phase5_report` from `docs/phase5-*.json`. Simulation only: the lab has no wallet, signer or transaction path; every “execution” is a private Anvil fork.

## Question and answer

**Does any strategy show credible evidence of positive, realistically attainable net profit?** No transaction strategy qualifies on the evidence gathered; passive yield is positive but is not transaction revenue.

- Transaction strategies with positive modeled attainable net profit: none.
- Passive-yield strategies with positive net yield (not transaction revenue): yield_opt/base, yield_opt/arbitrum.

## Plugin framework

Each strategy is a plugin (`backend/app/plugins/`) with the same interface: `discover` (opportunities now or at a block), `state`, `simulate` (the complete transaction on a fork), `competition` (capture probability with its model and assumptions), `replay` (historical evaluation) and `shadow_tick` (live read-only observation). `app.plugins.run` drives one strategy in one mode and writes the artefact; `app.plugins.ranking` builds the unified table; `/api/research/strategies` and the dashboard “Strategies” page read it. Configuration: `STRATEGY_<NAME>_<FIELD>` environment variables. Tests: `tests/test_plugins_*.py`.

## Strategy A — Compound V3 liquidations

Market discovery uses the Phase 4 protocol registry (Comet markets per network); liquidation eligibility is Comet's own `isLiquidatable`, not Aave's health factor. Profit comes only from buying seized collateral at the `quoteCollateral` discount (the absorber is unpaid).

### Base

Discovery at block `latest` (2026-10-22T14:06:24 UTC, 14.3 s): borrowers are the senders of `Withdraw` events in the last 9,000 blocks (plus any configured accounts), checked with `isLiquidatable` and `borrowBalanceOf` through Multicall3; seized collateral for sale comes from `getCollateralReserves` priced at the Comet oracle with the `quoteCollateral` discount.

| Market | Accounts scanned | Borrowers | Liquidatable now | Reserve lots | Largest reserve lot (gross at discount) |
|---|---|---|---|---|---|
| compound_v3_base_usdbc | 2 | 0 | **0** | 2 | $0.0009 (989 bps) |
| compound_v3_base_usdc | 32 | 8 | **0** | 4 | $0.0000 (309 bps) |
| compound_v3_base_weth | 34 | 6 | **0** | 2 | $0.0000 (178 bps) |

Fork simulations (1; `CometLiquidator.sol`: flash loan of the base asset → `absorb` → `buyCollateral` → Uniswap v3 swap → repay):

| Opportunity | Result | Gross | Net (complete?) | Measured | Time |
|---|---|---|---|---|---|
| `compound_v3_base_usdbc:reserve:0x420000000000000` | revert: dust: collateral for sale worth 0.010305 USD at a 989 bps di | $0.0009 | — (incomplete: not attempted) | gas — | 8 s |

Historical replay over blocks 52,560,000–52,640,886: 2 recorded absorb transactions in the window; first 2 replayed.

| Absorb tx | Block | Liquidatable at N−1? | Our receiver | Gross | Net (complete?) | Note |
|---|---|---|---|---|---|---|
| `0x593a…a736` | 52611468 | True | revert: dust: collateral for sale worth 0.000053 USD at a 309 bps discount, be | — | — (incomplete) |  |
| `0xf557…0c6a` | 52620875 | True | revert: dust: collateral for sale worth 0.004303 USD at a 309 bps discount, be | — | — (incomplete) |  |

Competitor analysis from Comet events, blocks 52,480,000–52,640,886 (3.7 days on Base):

| Figure | Value |
|---|---|
| Absorb transactions (unique) | **2** |
| Distinct absorbers | 1 |
| `buyCollateral` transactions | **8** (buyers: 1) |
| Absorb effective gas price: median / max | 0.0154 gwei / 0.0247 gwei |
| Absorb gas used: median | 145,824 |
| Absorb position in block (tx index): median | 504 |

| Absorb tx | Block | Market | Absorber | Collateral seized (USD) | Gas price |
|---|---|---|---|---|---|
| `0x593a…a736` | 52,611,468 | base_usdc | `0x6c7C…148e` | $0.0000 | 0.0060 gwei |
| `0xf557…0c6a` | 52,620,875 | base_usdc | `0x6c7C…148e` | $0.0043 | 0.0247 gwei |

| Buy tx | Block | Market | Buyer | Base paid (raw) | Collateral (raw) | Gas price |
|---|---|---|---|---|---|---|
| `0x4de2…0c70` | 52,482,201 | base_weth | `0x16e4…7b19` | 10 | 8 | 0.0060 gwei |
| `0xa410…0f34` | 52,482,218 | base_weth | `0x16e4…7b19` | 10 | 8 | 0.0060 gwei |
| `0x598f…14eb` | 52,500,592 | base_weth | `0x16e4…7b19` | 632215232800 | 2 | 0.0060 gwei |
| `0xe5ec…05b2` | 52,501,389 | base_weth | `0x16e4…7b19` | 4312731 | 3845943 | 0.0060 gwei |
| `0x221f…2e30` | 52,505,362 | base_weth | `0x16e4…7b19` | 15967450807 | 14239216331 | 0.0060 gwei |
| `0xbce1…44e5` | 52,510,830 | base_weth | `0x16e4…7b19` | 89826481 | 80104126 | 0.0060 gwei |
| `0xd88b…0fd7` | 52,511,085 | base_weth | `0x16e4…7b19` | 63672 | 56780 | 0.0060 gwei |
| `0x9cc1…8845` | 52,596,150 | base_weth | `0x16e4…7b19` | 10 | 8 | 0.0060 gwei |

Reading: on Compound V3 the absorber is not paid; the protocol takes the collateral into reserves and anyone may buy it at the `quoteCollateral` discount. The race is therefore for `buyCollateral` right after the absorb (often in the same transaction, which is what `CometLiquidator.sol` does). Aave's close-factor / liquidation-bonus formulas do not apply; the discount is `storeFrontPriceFactor × (1 − liquidationFactor)` per asset.

### Arbitrum

Discovery at block `latest` (2026-10-22T14:06:15 UTC, 534.3 s): borrowers are the senders of `Withdraw` events in the last 9,000 blocks (plus any configured accounts), checked with `isLiquidatable` and `borrowBalanceOf` through Multicall3; seized collateral for sale comes from `getCollateralReserves` priced at the Comet oracle with the `quoteCollateral` discount.

| Market | Accounts scanned | Borrowers | Liquidatable now | Reserve lots | Largest reserve lot (gross at discount) |
|---|---|---|---|---|---|
| compound_v3_arbitrum_usdc | 4 | 2 | **0** | 8 | $0.0046 (638 bps) |

Fork simulations (1; `CometLiquidator.sol`: flash loan of the base asset → `absorb` → `buyCollateral` → Uniswap v3 swap → repay):

| Opportunity | Result | Gross | Net (complete?) | Measured | Time |
|---|---|---|---|---|---|
| `compound_v3_arbitrum_usdc:reserve:0x5979D7b546E3` | revert: dust: collateral for sale worth 0.076247 USD at a 638 bps di | $0.0046 | — (incomplete: not attempted) | gas — | 4 s |

Historical replay: **NOT RUN** (needs the event history and an Anvil fork).

## Strategy B — DEX arbitrage

Pools: Base WETH/USDC (Uniswap v3 0.05 % and 0.3 %, Aerodrome volatile), USDC/USDbC (Uniswap v3 0.01 %, Aerodrome stable), cbBTC pairs; Arbitrum WETH/USDC (0.05 %, 0.3 %), USDC/USDT and USDC/USDC.e (0.01 %). Only pairs with two or more registered venues can arbitrage; spreads are live quotes at a block, never hypothetical.

### Base

Discovery at block `latest` (2026-10-22T14:06:16 UTC, 16.9 s): every directed venue pair of each registered pool set is quoted through Multicall3 (Uniswap v3 `QuoterV2`, Aerodrome `getAmountsOut`) on a 7-point geometric size grid from $50 to $50,000; the best quoted round trip net of the 5 bps Aave flash premium is kept.

| Directed pair | Size | Quoted gross | Premium | Net before gas | Executable? |
|---|---|---|---|---|---|
| `WETH/USDC:Uniswap v3 0.05%->Uniswap v3 0.3%:ab` | 0.0208 | −$0.1478 | $0.0250 | −$0.1728 | no |
| `WETH/USDC:Uniswap v3 0.3%->Uniswap v3 0.05%:ba` | 50.0083 | −$0.1523 | $0.0250 | −$0.1773 | no |
| `USDC/USDbC:Aerodrome sAMM->Uniswap v3 0.01%:ab` | 50.0083 | −$0.0188 | $0.0250 | −$0.0438 | no |
| `USDC/USDbC:Uniswap v3 0.01%->Aerodrome sAMM:ba` | 50.0083 | −$0.0188 | $0.0250 | −$0.0438 | no |

Fork simulations (2; `FlashArb.sol`: `flashLoanSimple(tokenIn)` → leg 1 → leg 2 → repay; validation runs are the best non-executable rows, run to measure what an attempt costs):

| Directed pair | Validation? | Result | Round trip P&L | Flash premium | Gas (units, L2 cost, L1 fee) | Net of everything | Complete? |
|---|---|---|---|---|---|---|---|
| `USDC/USDbC:Aerodrome sAMM->Uniswap v3 0.01%:ab` | yes | reverted at settlement | −$0.0188 | $0.0250 | 576568, $0.0083, $0.0002 | **−$0.0523** | yes |
| `USDC/USDbC:Uniswap v3 0.01%->Aerodrome sAMM:ba` | yes | reverted at settlement | −$0.0188 | $0.0250 | 574445, $0.0083, $0.0002 | **−$0.0523** | yes |

Historical sampling over blocks 52,560,000–52,640,886: 203 sampled blocks every 400 blocks; 0 with a quoted spread above the 0.05 USD gas floor (before competition); expected figure = mean best net-before-gas spread per sampled block; worst case = cost of a failed attempt (see simulate), not the quoted shortfall. Mean best net-before-gas spread per sampled block −$0.0531; worst —.

Of 203 sampled blocks, 0 had any positive net-before-gas spread (max −$0.0308); the gas floor used was the fork-measured attempt cost.

Shadow monitoring (read-only, 30 min, 98 blocks observed): 0 blocks with an executable spread; 0 distinct executable opportunities; best quoted gross over the window −$0.0188, median −$0.0188.

### Arbitrum

Discovery at block `latest` (2026-10-22T14:15:16 UTC, 7.3 s): every directed venue pair of each registered pool set is quoted through Multicall3 (Uniswap v3 `QuoterV2`, Aerodrome `getAmountsOut`) on a 7-point geometric size grid from $50 to $50,000; the best quoted round trip net of the 5 bps Aave flash premium is kept.

| Directed pair | Size | Quoted gross | Premium | Net before gas | Executable? |
|---|---|---|---|---|---|
| `WETH/USDC:Uniswap v3 0.3%->Uniswap v3 0.05%:ab` | 0.0208 | −$0.1466 | $0.0250 | −$0.1716 | no |
| `WETH/USDC:Uniswap v3 0.05%->Uniswap v3 0.3%:ba` | 50.0050 | −$0.1466 | $0.0250 | −$0.1716 | no |

Fork simulations (2; `FlashArb.sol`: `flashLoanSimple(tokenIn)` → leg 1 → leg 2 → repay; validation runs are the best non-executable rows, run to measure what an attempt costs):

| Directed pair | Validation? | Result | Round trip P&L | Flash premium | Gas (units, L2 cost, L1 fee) | Net of everything | Complete? |
|---|---|---|---|---|---|---|---|
| `WETH/USDC:Uniswap v3 0.3%->Uniswap v3 0.05%:ab` | yes | reverted at settlement | −$0.1399 | $0.0250 | 406375, $0.0196, $0.0010 | **−$0.1855** | yes |
| `WETH/USDC:Uniswap v3 0.05%->Uniswap v3 0.3%:ba` | yes | reverted at settlement | −$0.1399 | $0.0250 | 407586, $0.0197, $0.0010 | **−$0.1856** | yes |

Historical sampling over blocks 516,700,000–517,350,000: 173 sampled blocks every 3250 blocks; 0 with a quoted spread above the 0.05 USD gas floor (before competition); expected figure = mean best net-before-gas spread per sampled block; worst case = cost of a failed attempt (see simulate), not the quoted shortfall. Mean best net-before-gas spread per sampled block −$0.1283; worst —.

Of 173 sampled blocks, 0 had any positive net-before-gas spread (max −$0.0690); the gas floor used was the fork-measured attempt cost.

Shadow monitoring (read-only, 30 min, 153 blocks observed): 0 blocks with an executable spread; 0 distinct executable opportunities; best quoted gross over the window −$0.0592, median −$0.1387.

## Strategy C — Yield optimisation

Stablecoin lending across Aave V3 and Compound V3 and liquid-staking-token loops on Aave (supply LST, borrow WETH); staking yield is measured as the growth of the LST/WETH oracle ratio over 7 days. Frozen or paused reserves are shown but never chosen.

### Base

Live reads at block `latest` (2026-10-22T13:50:48 UTC). Figures are **passive yield** for a 10,000 USD position held 30 days at today's rates, net of an estimated $0.60 entry/exit gas; they are not transaction revenue and are never added to the simulated figures above.

| Opportunity | Venue rates | 30-day gross | 30-day net | Risk | Notes |
|---|---|---|---|---|---|
| `stable_lending:0x833589fCD6eDb6E08f4c7C32D4f` | aave_v3 3.80 %; compound_v3_base_usdc 5.48 % | $45.03 | **$44.43** | low | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |
| `stable_lending:0xd9aAEc86B65D86f6A7B5B1b0c42` | aave_v3 1.47 %; compound_v3_base_usdbc 0.60 % | $12.11 | **$11.51** | medium | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |
| `lst_loop:wstETH` | staking 2.25 % / WETH borrow 2.04 % → net 2.92 % at 4.17× | $77.03 | **$21.49** | high | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |
| `lst_loop:weETH` | staking 2.30 % / WETH borrow 2.04 % → net 3.04 % at 3.85× | $72.67 | **$22.69** | high | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |
| `lst_loop:cbETH` | staking 2.29 % / WETH borrow 2.04 % → net 3.09 % at 4.17× | $78.46 | **$22.93** | high | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |

Historical rates over blocks 52,641,467–52,943,867: 8 daily samples; expected profit = 10,000 USD × average best stablecoin APY × 30/365 − entry/exit gas; worst-case loss not sampled (a depeg or exploit, not a rate path, is the loss scenario). Best stablecoin APY avg / min / max: 4.924986 / 3.857812 / 5.936891 %; expected 30-day net at the average: $39.88.

### Arbitrum

Live reads at block `latest` (2026-10-22T13:51:56 UTC). Figures are **passive yield** for a 10,000 USD position held 30 days at today's rates, net of an estimated $0.60 entry/exit gas; they are not transaction revenue and are never added to the simulated figures above.

| Opportunity | Venue rates | 30-day gross | 30-day net | Risk | Notes |
|---|---|---|---|---|---|
| `stable_lending:0xaf88d065e77c8cC2239327C5EDb` | aave_v3 2.46 %; compound_v3_arbitrum_usdc 3.22 % | $26.43 | **$25.83** | low | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |
| `stable_lending:0xFd086bC7CD5C481DCC9C85ebE47` | aave_v3 2.99 % | $24.54 | **$23.94** | low | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |
| `lst_loop:wstETH` | staking 2.58 % / WETH borrow 1.99 % → net 4.46 % at 4.17× | $88.31 | **$34.15** | high | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |
| `lst_loop:weETH` | staking 2.59 % / WETH borrow 1.99 % → net 4.31 % at 3.85× | $81.88 | **$33.14** | high | smart-contract risk of the lending protocol (audited, battle-tested, not zero); rates float with utilisation; the quoted APY is the current  |

Historical rates over blocks 514,956,180–517,375,380: 8 daily samples; expected profit = 10,000 USD × average best stablecoin APY × 30/365 − entry/exit gas; worst-case loss not sampled (a depeg or exploit, not a rate path, is the loss scenario). Best stablecoin APY avg / min / max: 3.185574 / 3.171261 / 3.215611 %; expected 30-day net at the average: $25.58.

## Unified ranking (dashboard “Strategies” section)

Every profit cell names its class: `theoretical` (protocol math or quotes), `simulated` (our receiver on a fork), `modeled` (capture probability applied), `passive_yield` (lending/staking rates). Classes are never summed. “Complete net profit” counts only simulations in which every material cost was measured.

| Strategy | Network | Capital | Flash loan | Gross profit | Complete net profit | Capture probability | Attainable profit | Frequency (observed) | Risk | Evidence | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| yield_opt | base | $10,000.00 | no | $45.03 (passive_yield) | $44.43 (passive_yield) | 100 % (none passive) | $44.43 (passive_yield) | continuous: a positive rate on 8 of 8 sampled days (blocks 52,641,467–52,943,867) | low | historical, live_read | **positive passive** |
| yield_opt | arbitrum | $10,000.00 | no | $88.31 (passive_yield) | $34.15 (passive_yield) | 100 % (none passive) | $34.15 (passive_yield) | continuous: a positive rate on 7 of 8 sampled days (blocks 514,956,180–517,375,380) | high | historical, live_read | **positive passive** |
| dex_arb | base | $0.0000 | yes | −$0.0188 (simulated) | −$0.0523 (simulated) | — | — | 0 in 1.9 days (blocks 52,560,000–52,640,886); shadow: 0 in 98 observed blocks over 30 min | medium | historical, fork, shadow, live_read | **negative** |
| dex_arb | arbitrum | $0.0000 | yes | −$0.1399 (simulated) | −$0.1855 (simulated) | — | — | 0 in 1.9 days (blocks 516,700,000–517,350,000); shadow: 0 in 153 observed blocks over 30 m | medium | historical, fork, shadow, live_read | **negative** |
| compound_v3 | arbitrum | $0.0000 | yes | $0.0046 (simulated) | — (theoretical) | — | — | not measured | low | fork, live_read | **negative** |
| compound_v3 | base | $0.0000 | yes | $0.0009 (simulated) | — (theoretical) | — | — | 2 in 1.9 days (blocks 52,560,000–52,640,886) | low | historical, fork, live_read | **negative** |

## Evaluation (§7)

| Strategy / network | Opportunities (latest run) | Executable | Simulated (success) | Net profitable, complete | Lost to competition | Expected profit / attempt | Capital efficiency | Worst-case loss | Infra cost / month | Repeatability |
|---|---|---|---|---|---|---|---|---|---|---|
| yield_opt / base | 5 (+8 historical) | 5 | 0 (0) | 5 | — | $39.88 | 0.444272% of capital per 30 days | — | $69 (est.) | passive: repeats every day the rate holds; rates float with utilisation |
| yield_opt / arbitrum | 4 (+8 historical) | 4 | 0 (0) | 4 | — | $25.58 | 0.341472% of capital per 30 days | — | $69 (est.) | passive: repeats every day the rate holds; rates float with utilisation |
| dex_arb / base | 4 (+0 historical) | 0 | 2 (0) | 0 | — | −$0.0531 | flash-financed: no own capital at risk beyond gas | $0.0335 | $69 (est.) | shadow persistence: 0 opportunities, median lifetime 0 blocks |
| dex_arb / arbitrum | 2 (+0 historical) | 0 | 2 (0) | 0 | — | −$0.1283 | flash-financed: no own capital at risk beyond gas | $0.0456 | $69 (est.) | shadow persistence: 0 opportunities, median lifetime 0 blocks |
| compound_v3 / arbitrum | 9 | 0 | 1 (0) | 0 | — | — | — | — | $69 (est.) | not measured |
| compound_v3 / base | 11 (+2 historical) | 0 | 1 (0) | 0 | — | — | — | — | $69 (est.) | 0 of 2 historical opportunities reproduced on the fork (2 not attempted: dust) |

Uncertainty and sample sizes:

- **yield_opt / base**: passive yield 44.427248 USD per 30 days on 10000.000000 USD, net of entry/exit gas; not transaction revenue; risks listed. Samples: 5 opportunities, 0 simulations (0 complete), 8 historical, no shadow ticks. risk ranges low–high across the rows; the table shows the ranked row's; small sample: 5 opportunities at the latest run; small historical sample: 8 daily rate samples
- **yield_opt / arbitrum**: passive yield 34.147229 USD per 30 days on 10000.000000 USD, net of entry/exit gas; not transaction revenue; risks listed. Samples: 4 opportunities, 0 simulations (0 complete), 8 historical, no shadow ticks. risk ranges low–high across the rows; the table shows the ranked row's; small sample: 4 opportunities at the latest run; small historical sample: 8 daily rate samples
- **dex_arb / base**: simulated net profit -0.052264 USD ≤ 0 at the best opportunity. Samples: 4 opportunities, 2 simulations (2 complete), 0 historical, 98 shadow ticks. capture probability not estimated: no shadow persistence measurement or no complete net profit; small sample: 4 opportunities at the latest run; small historical sample: 0 replay opportunities
- **dex_arb / arbitrum**: simulated net profit -0.185498 USD ≤ 0 at the best opportunity. Samples: 2 opportunities, 2 simulations (2 complete), 0 historical, 153 shadow ticks. capture probability not estimated: no shadow persistence measurement or no complete net profit; small sample: 2 opportunities at the latest run; small historical sample: 0 replay opportunities
- **compound_v3 / arbitrum**: nothing executable: the best opportunity is dust (0.004573 USD gross, below the 0.05 USD floor). Samples: 9 opportunities, 1 simulations (0 complete), no historical, no shadow ticks. no opportunity with a complete net profit (every cost measured or the strategy carries none); capture probability not estimated: no observed absorb bids or no complete net profit: capture probability not estimated; small sample: 9 opportunities at the latest run
- **compound_v3 / base**: nothing executable: the best opportunity is dust (0.000927 USD gross, below the 0.05 USD floor). Samples: 11 opportunities, 1 simulations (0 complete), 2 historical, no shadow ticks. no opportunity with a complete net profit (every cost measured or the strategy carries none); capture probability not estimated: no observed absorb bids or no complete net profit: capture probability not estimated; small historical sample: 2 replay opportunities

## What this does and does not establish

- Compound V3 on Base and Arbitrum had no liquidatable account and only dust reserves at every discovery; the historical window holds very few absorbs, so the competition model has almost no bids to learn from. The replay shows whether our receiver reproduces the recorded absorbs; it does not show a win rate.
- DEX spreads between the registered venues were negative before gas at every quoted block, every size and in both directions; the validation simulations measure the full cost of an attempt (both legs executed, premium charged, gas of the reverted transaction). A spread that exists only inside a block (back-running a large swap) is invisible to this block-boundary sampling, and capturing it requires MEV infrastructure the lab does not model as attainable.
- Yield figures are passive: they need capital on deposit, carry smart-contract, depeg and (for loops) liquidation risk, and float with utilisation. They are the only positive net figures in this phase and they are not transaction revenue.
- Sample sizes are small (days, not months) and all figures come from public endpoints on free tiers; see `docs/limitations.md`.

## Reproduce

```bash
scripts/phase5-research.sh base      # discover / simulate / replay / shadow for the three strategies, ranking, this report
scripts/phase5-research.sh arbitrum
```
