# Phase 4: competitive strategy optimization (2026-10-22)

Read-only throughout; no signer, no broadcast path. Builds on the Phase 3 dataset (every recorded liquidation on the verified Pools, replayed through the real receiver with complete costs). New here: a separate low-latency candidate path, per-stage latency, the observability of each opportunity from legitimate public data, modular protocol adapters, ranking by attainable profit, and a strategy comparison with intervals.

## Headline

| Network | Events | Publicly observable | Predictable | Intra-block only | Observable before winner | Fast path median | Fork simulation median | Best public-data strategy (expected profit) |
|---|---|---|---|---|---|---|---|---|
| base | 24 | 3 | 17 | 4 | 83 % | 373 ms | 10.6 s | none positive (best `A_boundary_polling` $0.0000) |
| arbitrum | 4 | 2 | 2 | 0 | 100 % | 343 ms | 11.1 s | none positive (best `A_boundary_polling` $0.0000) |

**Finding.** On Base the price feeds Aave reads are Chainlink `DualAggregator` contracts: the aggregator returns a new round to ordinary callers immediately, while the proxy the Pool reads returns it only to the auction winner's transaction or after a 30 s gate. Eligibility is therefore predictable from public data, usually one block ahead; whether it is contestable depends on the reveal: when the winner unlocks the round inside the window, nothing is left for public bots. With the measured latencies and the observed winning bids, no public-data strategy shows positive expected profit on this dataset; the ceiling without competition is the sum of simulated profits.

## Observability of recorded liquidations (Base)

Per event, at the state of N−1: the Pool's oracle price (proxy), the aggregator behind it read as an ordinary caller, the borrower's health factor under each, interest-only eligibility at block N's timestamp, and the resulting category.

| Figure | Value |
|---|---|
| Events | 24 |
| Publicly observable (liquidatable at N−1 with the Pool's own prices) | **3** |
| Predictable, not yet eligible (aggregator already carried the round, or time alone) | **17** |
| Intra-block only (no public signal before block N) | **4** |
| Unknown (a probe failed) | 0 |
| Observable before the winning transaction | **20 of 24 (83 %)** |
| Lead of the aggregator over the Pool's price (s): n / min / median / max | 15 / 2 / 2 / 32 |
| Events where the aggregator led the proxy | 16 |
| Aggregator types seen | ?, DualAggregator 1.0.0 |

| Tx | Block | Category | HF (Pool prices) | HF (aggregator prices) | Eligible at N ts | Lead (s) | Proxy reveal (s) |
|---|---|---|---|---|---|---|---|
| `0xd103…1df1` | 52,639,120 | predictable_not_yet_eligible | 1.001673 | 0.999910 | True | 32 | 0 |
| `0x473a…1a2b` | 52,621,716 | predictable_not_yet_eligible | 1.000846 | 0.999939 | True | 32 | 0 |
| `0x7ed3…3f3c` | 52,621,006 | publicly_observable | 0.990048 | — | True | — | — |
| `0x2dfa…d383` | 52,621,005 | intra_block_only | 1.000726 | — | False | — | — |
| `0x6afc…b026` | 52,621,005 | predictable_not_yet_eligible | 1.013156 | 0.997552 | False | 2 | 0 |
| `0x6afc…b026` | 52,621,005 | predictable_not_yet_eligible | 1.008524 | 0.992991 | False | 2 | 0 |
| `0x6afc…b026` | 52,621,005 | intra_block_only | 1.011780 | — | False | — | 0 |
| `0x6afc…b026` | 52,621,005 | intra_block_only | 1.004595 | 1.004029 | False | — | 15 |
| `0x6afc…b026` | 52,621,005 | predictable_not_yet_eligible | 1.013138 | 0.997534 | False | 2 | 0 |
| `0xa092…4c91` | 52,620,870 | predictable_not_yet_eligible | 1.005422 | 0.996164 | False | 2 | 30 |
| `0xa092…4c91` | 52,620,870 | predictable_not_yet_eligible | 1.004783 | 0.998711 | False | 2 | 30 |
| `0xa092…4c91` | 52,620,870 | predictable_not_yet_eligible | 1.003592 | 0.994501 | False | 2 | 30 |
| `0x8ba8…682e` | 52,620,840 | predictable_not_yet_eligible | 1.002415 | 0.996928 | False | 2 | 30 |
| `0x58cb…70c0` | 52,620,840 | intra_block_only | 1.003083 | — | False | — | 30 |
| `0x58cb…70c0` | 52,620,840 | predictable_not_yet_eligible | 1.003592 | 0.998098 | False | 2 | 30 |
| `0x881a…6d86` | 52,620,811 | publicly_observable | 0.998664 | — | True | — | — |
| `0x51c3…6d40` | 52,620,810 | predictable_not_yet_eligible | 1.007101 | 0.996208 | False | 2 | 0 |
| `0x51c3…6d40` | 52,620,810 | predictable_not_yet_eligible | 1.008461 | 0.997855 | False | 2 | 0 |
| `0x51c3…6d40` | 52,620,810 | predictable_not_yet_eligible | 1.009031 | 0.998117 | False | 2 | 0 |
| `0xa49f…7890` | 52,610,291 | publicly_observable | 0.999036 | — | True | — | — |
| `0x5c7e…b5be` | 52,599,798 | predictable_not_yet_eligible | 1.000219 | 0.998689 | False | 2 | 30 |
| `0x0339…b2d2` | 52,598,898 | predictable_not_yet_eligible | 1.000985 | 0.998206 | False | 2 | 30 |
| `0x9366…713f` | 52,593,994 | predictable_not_yet_eligible | 1.000000 | — | True | — | — |
| `0xcf07…9112` | 52,592,297 | predictable_not_yet_eligible | 1.000001 | — | True | — | — |

## Component latency (Base)

Measured 2026-10-22T12:55:54Z against `base-rpc.publicnode.com`, 5 positions × 10 rounds; median / p90.

| Stage | Latency |
|---|---|
| RPC round trip (eth_chainId) | 65 / 73 ms (n=10) |
| Head poll (eth_blockNumber) | 61 / 69 ms (n=10) |
| Protocol reads (one Multicall3: account data, 2 user reserves, 2 configurations, prices, premium, e-mode) | 211 / 237 ms (n=50) |
| Profitability math (pure functions) | 0.050 / 0.076 ms (n=50) |
| Swap quotes on every fee tier + L1 fee (one Multicall3) | 221 / 240 ms (n=40) |
| **Fast path total** (reads + math + quotes + gas price) | **373 / 427 ms (n=50)** |
| Full path: fork at head + receiver deploy + liquidate on Anvil | 10566 / 10795 ms (n=3) |

## Strategy comparison on the same dataset (Base)

Seed 42, 2000 bootstrap samples over 24 events (categories {'publicly_observable': 3, 'predictable_not_yet_eligible': 17, 'intra_block_only': 4, 'unknown': 0}). Observed winning bids: 15 transactions, median 0.0404 gwei; effective competitors 8; failed attempt cost $0.2000; measured share of predictable events still contestable at the Pool's reveal: 0.588 (17 events).

| Strategy | Events in scope | With simulated profit | Bid (gwei) | P(win bid) | Ready in time | Modeled captures | Expected profit | 95 % interval | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `A_boundary_polling` | 3 | 1 | 0.1213 | 0.084 | 0.00 | 0.00 | **$0.0000** | $0.0000 … $0.0000 | no positive expected profit on this dataset |
| `B_event_driven` | 3 | 1 | 0.1213 | 0.084 | 1.00 | 0.25 | **$-0.1468** | $-0.4405 … $0.0000 | no positive expected profit on this dataset |
| `C_predictive` | 20 | 6 | 0.1213 | 0.084 | 1.00 | 1.09 | **$-0.8734** | $-1.60 … $-0.3270 | no positive expected profit on this dataset |
| `D_ceiling_no_competition` | 24 | 6 | 0.0404 | 0.002 | 1.00 | 24.00 | **$3,120.80** | $0.0512 … $9,361.79 | positive expected profit with 95 % confidence |

Strategies: `A_boundary_polling`: poll every head, evaluate on the fast path, confirm on a fork, then send; `B_event_driven`: poll every head, evaluate on the fast path, send without a fork gate; `C_predictive`: read the aggregators directly, precompute the transaction during the lead, submit at the reveal block; `D_ceiling_no_competition`: reference ceiling: every simulated-profitable event captured with certainty; not a strategy, the upper bound

Assumptions: latency: head poll + fast path + fork gate drawn log-normal from measured median/p90; window: one block (2.0 s) for publicly observable events; the measured lead for predictable events; bid chosen from the observed winning-bid grid to maximise expected profit over in-scope events (extra gas above the median bid charged at 565,190 units, ETH 4000 USD); P(win) = F(bid)^8 over observed winning bids; profit per captured event = Phase 3 simulated net profit with complete costs; events without one contribute 0; predictable events are multiplied by the measured share still open at the reveal block (0 when unmeasured and reported as such)

## Observability of recorded liquidations (Arbitrum)

Per event, at the state of N−1: the Pool's oracle price (proxy), the aggregator behind it read as an ordinary caller, the borrower's health factor under each, interest-only eligibility at block N's timestamp, and the resulting category.

| Figure | Value |
|---|---|
| Events | 4 |
| Publicly observable (liquidatable at N−1 with the Pool's own prices) | **2** |
| Predictable, not yet eligible (aggregator already carried the round, or time alone) | **2** |
| Intra-block only (no public signal before block N) | **0** |
| Unknown (a probe failed) | 0 |
| Observable before the winning transaction | **4 of 4 (100 %)** |
| Lead of the aggregator over the Pool's price (s): n / min / median / max | 1 / 2 / 2 / 2 |
| Events where the aggregator led the proxy | 1 |
| Aggregator types seen | ?, DualAggregator 1.0.0 |

| Tx | Block | Category | HF (Pool prices) | HF (aggregator prices) | Eligible at N ts | Lead (s) | Proxy reveal (s) |
|---|---|---|---|---|---|---|---|
| `0xf365…cc03` | 517,085,787 | predictable_not_yet_eligible | 1.000302 | 0.999652 | False | 2 | 30 |
| `0xef1d…bc7a` | 516,716,800 | publicly_observable | 0.999999 | — | True | — | — |
| `0xaac6…533d` | 516,312,371 | publicly_observable | 0.996321 | — | True | — | — |
| `0x4ec4…7340` | 516,296,225 | predictable_not_yet_eligible | 1.000000 | — | True | — | — |

## Component latency (Arbitrum)

Measured 2026-10-22T13:07:23Z against `arbitrum-one-rpc.publicnode.com`, 4 positions × 10 rounds; median / p90.

| Stage | Latency |
|---|---|
| RPC round trip (eth_chainId) | 54 / 62 ms (n=10) |
| Head poll (eth_blockNumber) | 52 / 56 ms (n=10) |
| Protocol reads (one Multicall3: account data, 2 user reserves, 2 configurations, prices, premium, e-mode) | 171 / 193 ms (n=40) |
| Profitability math (pure functions) | 0.047 / 0.091 ms (n=40) |
| Swap quotes on every fee tier + L1 fee (one Multicall3) | 0 / 221 ms (n=40) |
| **Fast path total** (reads + math + quotes + gas price) | **343 / 392 ms (n=40)** |
| Full path: fork at head + receiver deploy + liquidate on Anvil | 11112 / 12117 ms (n=3) |

## Strategy comparison on the same dataset (Arbitrum)

Seed 42, 2000 bootstrap samples over 4 events (categories {'publicly_observable': 2, 'predictable_not_yet_eligible': 2, 'intra_block_only': 0, 'unknown': 0}). Observed winning bids: 4 transactions, median 0.0383 gwei; effective competitors 4; failed attempt cost $0.2000; measured share of predictable events still contestable at the Pool's reveal: 0.5 (2 events).

| Strategy | Events in scope | With simulated profit | Bid (gwei) | P(win bid) | Ready in time | Modeled captures | Expected profit | 95 % interval | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `A_boundary_polling` | 2 | 1 | 0.0421 | 1.000 | 0.00 | 0.00 | **$0.0000** | $0.0000 … $0.0000 | no positive expected profit on this dataset |
| `B_event_driven` | 2 | 1 | 0.0421 | 1.000 | 0.00 | 0.00 | **$0.0000** | $0.0000 … $0.0000 | no positive expected profit on this dataset |
| `C_predictive` | 4 | 1 | 0.0421 | 1.000 | 0.25 | 0.00 | **$0.0000** | $0.0000 … $0.0000 | no positive expected profit on this dataset |
| `D_ceiling_no_competition` | 4 | 1 | 0.0383 | 0.062 | 1.00 | 4.00 | **$-0.0169** | $-0.0506 … $0.0000 | no positive expected profit on this dataset |

Strategies: `A_boundary_polling`: poll every head, evaluate on the fast path, confirm on a fork, then send; `B_event_driven`: poll every head, evaluate on the fast path, send without a fork gate; `C_predictive`: read the aggregators directly, precompute the transaction during the lead, submit at the reveal block; `D_ceiling_no_competition`: reference ceiling: every simulated-profitable event captured with certainty; not a strategy, the upper bound

Assumptions: latency: head poll + fast path + fork gate drawn log-normal from measured median/p90; window: one block (0.25 s) for publicly observable events; the measured lead for predictable events; bid chosen from the observed winning-bid grid to maximise expected profit over in-scope events (extra gas above the median bid charged at 565,190 units, ETH 4000 USD); P(win) = F(bid)^4 over observed winning bids; profit per captured event = Phase 3 simulated net profit with complete costs; events without one contribute 0; predictable events are multiplied by the measured share still open at the reveal block (0 when unmeasured and reported as such)

## Markets and protocols

20 markets registered across 14 chains and protocols aave_v3, compound_v3: 6 verified live, 0 failed, 0 blocked, 14 not configured (no endpoint for that chain in this environment).

| Market | Protocol | Chain | Status | Liquidation events in the last 100 blocks | Note |
|---|---|---|---|---|---|
| `aave_v3_base` | aave_v3 | 8453 | verified | 0 |  |
| `aave_v3_arbitrum` | aave_v3 | 42161 | verified | 0 |  |
| `aave_v3_ethereum` | aave_v3 | 1 | not_configured | — | set ETHEREUM_RPC_HTTP / ETHEREUM_ARCHIVE_RPC |
| `aave_v3_ethereum_lido` | aave_v3 | 1 | not_configured | — | set ETHEREUM_RPC_HTTP / ETHEREUM_ARCHIVE_RPC |
| `aave_v3_ethereum_etherfi` | aave_v3 | 1 | not_configured | — | set ETHEREUM_RPC_HTTP / ETHEREUM_ARCHIVE_RPC |
| `aave_v3_optimism` | aave_v3 | 10 | not_configured | — | set OPTIMISM_RPC_HTTP / OPTIMISM_ARCHIVE_RPC |
| `aave_v3_polygon` | aave_v3 | 137 | not_configured | — | set POLYGON_RPC_HTTP / POLYGON_ARCHIVE_RPC |
| `aave_v3_avalanche` | aave_v3 | 43114 | not_configured | — | set AVALANCHE_RPC_HTTP / AVALANCHE_ARCHIVE_RPC |
| `aave_v3_scroll` | aave_v3 | 534352 | not_configured | — | set SCROLL_RPC_HTTP / SCROLL_ARCHIVE_RPC |
| `aave_v3_bnb` | aave_v3 | 56 | not_configured | — | set BNB_RPC_HTTP / BNB_ARCHIVE_RPC |
| `aave_v3_gnosis` | aave_v3 | 100 | not_configured | — | set GNOSIS_RPC_HTTP / GNOSIS_ARCHIVE_RPC |
| `aave_v3_linea` | aave_v3 | 59144 | not_configured | — | set LINEA_RPC_HTTP / LINEA_ARCHIVE_RPC |
| `aave_v3_sonic` | aave_v3 | 146 | not_configured | — | set SONIC_RPC_HTTP / SONIC_ARCHIVE_RPC |
| `aave_v3_celo` | aave_v3 | 42220 | not_configured | — | set CELO_RPC_HTTP / CELO_ARCHIVE_RPC |
| `aave_v3_soneium` | aave_v3 | 1868 | not_configured | — | set SONEIUM_RPC_HTTP / SONEIUM_ARCHIVE_RPC |
| `aave_v3_plasma` | aave_v3 | 9745 | not_configured | — | set PLASMA_RPC_HTTP / PLASMA_ARCHIVE_RPC |
| `compound_v3_base_usdc` | compound_v3 | 8453 | verified | 0 |  |
| `compound_v3_base_weth` | compound_v3 | 8453 | verified | 0 |  |
| `compound_v3_base_usdbc` | compound_v3 | 8453 | verified | 0 |  |
| `compound_v3_arbitrum_usdc` | compound_v3 | 42161 | verified | 0 |  |

Same-window comparison on base (last 9,000 blocks, 52,933,665–52,942,665, via `base-rpc.publicnode.com`):

| Market | Liquidation events | Unique transactions | Unique liquidators | Event | Ranges that failed |
|---|---|---|---|---|---|
| `aave_v3_base` | 0 | 0 | 0 | — | 0 |
| `compound_v3_base_usdc` | 0 | 0 | 0 | — | 0 |
| `compound_v3_base_weth` | 0 | 0 | 0 | — | 0 |
| `compound_v3_base_usdbc` | 0 | 0 | 0 | — | 0 |

Same-window comparison on arbitrum (last 9,000 blocks, 517,226,162–517,235,162, via `arbitrum.drpc.org`):

| Market | Liquidation events | Unique transactions | Unique liquidators | Event | Ranges that failed |
|---|---|---|---|---|---|
| `aave_v3_arbitrum` | 0 | 0 | 0 | — | 0 |
| `compound_v3_arbitrum_usdc` | 0 | 0 | 0 | — | 0 |

## Simulator paths

| Path | What it does | Round trips | Outcome class | Where |
|---|---|---|---|---|
| fast | Multicall3 read of every protocol input, pure math, Multicall3 quotes + L1 fee, gas price | 3 | theoretical | `app/simulation/fast_path.py`, `POST /api/simulations/fast` |
| full | Anvil fork at N−1 or head, real receiver deployed, flash loan + liquidation + swap executed, every figure measured | fork reset + ~6 txs | simulated | `app/simulation/replay.py`, `POST /api/simulations` |

Validation: on the 0x7ed3 event at N−1 the fast path produced the same debt to cover (9,149,694), collateral to liquidator (4,115,949,317,907,073) and swap output (9,653,389) as the fork execution, to the unit; its gas figure is the configured estimate (972,842 units) rather than the 569,713 measured on the fork.

## Ranking by attainable profit

Candidates now carry `attainable_net_profit_usd = p_capture × net − (1 − p_capture) × failed-attempt cost` with `p_capture` from the observability class (live candidates are publicly observable) and the priority-auction win probability at the median observed winning bid over this Pool's recent liquidations; the Opportunity Explorer sorts by it (`app/competition/attainable.py`, migration `ecdc7637b17c`). Unknown inputs leave the field null; nothing is promoted by a missing number.

