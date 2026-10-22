# Phase 3: profit verification and competitive execution research (2026-10-22)

Read-only throughout: no signer, no broadcast path; every execution happened on a private Anvil fork. Four outcome classes are kept apart: **theoretical** (protocol math at the winning block), **simulated** (our receiver on a fork), **modeled** (competition-adjusted expectation from stated assumptions) and **onchain_actual** (what competitors did). Raw data for every table is in the JSON files named in each section.

## Headline figures

| Network | Verified theoretical gross / net (winners, oracle-valued) | Successfully simulated profit (complete costs) | Competition-adjusted expected profit (priority auction / FCFS) | Simulated profit only reachable inside the winning block |
|---|---|---|---|---|
| base | $4,034.25 / $4,025.32 | $3,120.80 (6 of 24 events) | $-0.0312 / not estimable | $3,120.36 |
| arbitrum | $108.50 / $108.24 | $-0.0169 (0 of 4 events) | — / not estimable | — |

The three profit figures answer different questions: what the protocol paid the actual winners; what our receiver realised on a fork with every cost known; and what a boundary-polling bot could expect after competition. None of them is money earned.

## Verified theoretical profit of the actual winners (Base)

Outcome class **theoretical**: seized collateral and repaid debt from the on-chain event, valued at the Aave oracle prices of the winning block (archive read), minus the winner's receipt-verified fee (once per transaction). This is what the protocol paid out before any swap; it is an upper bound, not something this system earned or simulated.

| Figure | Value |
|---|---|
| Events / unique transactions | 24 / 15 |
| Theoretical gross (oracle value of bonus) | **$4,034.25** |
| Theoretical net after winners' verified fees | **$4,025.32** (fees verified on 24 events) |

Largest events:

| Tx | Block | Repaid (USD) | Seized (USD) | Gross | Winner fee | Net |
|---|---|---|---|---|---|---|
| `0xa092…4c91` | 52,620,870 | $66,100.83 | $69,075.36 | $2,974.54 | $2.25 | $2,972.29 |
| `0x58cb…70c0` | 52,620,840 | $5,098.16 | $5,327.58 | $229.42 | $1.22 | $228.19 |
| `0x51c3…6d40` | 52,620,810 | $4,255.48 | $4,446.98 | $191.50 | $3.40 | $188.10 |
| `0xa092…4c91` | 52,620,870 | $3,344.52 | $3,495.02 | $150.50 | $0.0000 | $150.50 |
| `0x6afc…b026` | 52,621,005 | $1,304.15 | $1,392.18 | $88.03 | $0.0000 | $88.03 |

## Replays through the real flash-loan receiver (Base)

Batch `phase3-base`, generated 2026-10-22T08:45:35Z. Every row is one `LiquidationCall` **event**; a transaction with several events yields several replays, and each event's outcome is taken from the final fork attempt of its run.

| Count | Value |
|---|---|
| Unique historical transactions | 15 |
| Liquidation events (replays) | 24 |
| Simulation attempts (fork attempts incl. retries) | 26 (events with a retry: 2) |
| Execution paths | {'flash_loan_receiver': 24} |
| Succeeded on the fork | 6 |
| … of which net profitable with **complete** costs | **6** |
| … unprofitable after complete costs | 0 |
| … succeeded but a material cost unknown (never labeled profitable) | 0 |
| Reverted | 18 |
| Not liquidatable at N−1 (block boundary) | 21 (liquidatable at block N's timestamp before its transactions: 4) |
| Eligibility (fork, execution) | {'fork=False,execution=True': 4, 'fork=True,execution=True': 3, 'fork=False,execution=False': 17} |
| Net simulated profit, complete costs only | **$3,120.80** |
| Fee sources | {'estimated': 6, 'unknown': 18} |

Failure triage: {'profitable': 6, 'not_eligible_at_block_boundary': 16, 'aave_rule_revert_while_eligible_at_boundary': 2}

Successful replays (measured on the fork; all costs listed; `complete` = every material cost known):

| Tx | Block | Eligible N−1 / exec | Repaid (raw) | Swap out (raw) | Premium (raw) | Surplus (raw) | Gas | L2+L1 fee (USD) | Net profit | Complete |
|---|---|---|---|---|---|---|---|---|---|---|
| `0xd103…1df1` | 52,639,120 | False / True | 965705 | 1011101 | 483 | 44913 | 682,132 | $0.0129 | **$0.0375** | True |
| `0x473a…1a2b` | 52,621,716 | False / True | 550 | 586 | 1 | 35 | 565,190 | $0.0133 | **$0.0154** | True |
| `0x7ed3…3f3c` | 52,621,006 | True / True | 9149694 | 9653389 | 4575 | 499120 | 569,713 | $0.0633 | **$0.4358** | True |
| `0xa092…4c91` | 52,620,870 | False / False | 66106884515 | 69261008785 | 33053443 | 3121070827 | 684,995 | $0.5029 | **$3,120.28** | True |
| `0x9366…713f` | 52,593,994 | False / True | 533562 | 557572 | 267 | 23743 | 401,522 | $0.0100 | **$0.0138** | True |
| `0xcf07…9112` | 52,592,297 | False / True | 533556 | 557565 | 267 | 23742 | 401,522 | $0.0124 | **$0.0114** | True |

## Verified theoretical profit of the actual winners (Arbitrum)

Outcome class **theoretical**: seized collateral and repaid debt from the on-chain event, valued at the Aave oracle prices of the winning block (archive read), minus the winner's receipt-verified fee (once per transaction). This is what the protocol paid out before any swap; it is an upper bound, not something this system earned or simulated.

| Figure | Value |
|---|---|
| Events / unique transactions | 4 / 4 |
| Theoretical gross (oracle value of bonus) | **$108.50** |
| Theoretical net after winners' verified fees | **$108.24** (fees verified on 4 events) |

Largest events:

| Tx | Block | Repaid (USD) | Seized (USD) | Gross | Winner fee | Net |
|---|---|---|---|---|---|---|
| `0xf365…cc03` | 517,085,787 | $1,707.30 | $1,814.86 | $107.56 | $0.1700 | $107.39 |
| `0xef1d…bc7a` | 516,716,800 | $18.36 | $19.19 | $0.8262 | $0.0441 | $0.7821 |
| `0x4ec4…7340` | 516,296,225 | $2.53 | $2.64 | $0.1139 | $0.0236 | $0.0903 |
| `0xaac6…533d` | 516,312,371 | $0.0758 | $0.0792 | $0.0034 | $0.0206 | $-0.0171 |

## Replays through the real flash-loan receiver (Arbitrum)

Batch `phase3-arbitrum`, generated 2026-10-22T09:14:16Z. Every row is one `LiquidationCall` **event**; a transaction with several events yields several replays, and each event's outcome is taken from the final fork attempt of its run.

| Count | Value |
|---|---|
| Unique historical transactions | 4 |
| Liquidation events (replays) | 4 |
| Simulation attempts (fork attempts incl. retries) | 5 (events with a retry: 1) |
| Execution paths | {'flash_loan_receiver': 4} |
| Succeeded on the fork | 1 |
| … of which net profitable with **complete** costs | **0** |
| … unprofitable after complete costs | 1 |
| … succeeded but a material cost unknown (never labeled profitable) | 0 |
| Reverted | 3 |
| Not liquidatable at N−1 (block boundary) | 2 (liquidatable at block N's timestamp before its transactions: 1) |
| Eligibility (fork, execution) | {'fork=False,execution=False': 1, 'fork=True,execution=True': 2, 'fork=False,execution=True': 1} |
| Net simulated profit, complete costs only | **$-0.0169** |
| Fee sources | {'unknown': 3, 'estimated': 1} |

Failure triage: {'not_eligible_at_block_boundary': 2, 'aave_rule_revert_while_eligible_at_boundary': 1, 'unprofitable_after_costs': 1}

Successful replays (measured on the fork; all costs listed; `complete` = every material cost known):

| Tx | Block | Eligible N−1 / exec | Repaid (raw) | Swap out (raw) | Premium (raw) | Surplus (raw) | Gas | L2+L1 fee (USD) | Net profit | Complete |
|---|---|---|---|---|---|---|---|---|---|---|
| `0xaac6…533d` | 516,312,371 | True / True | 75926 | 79342 | 38 | 3378 | 418,808 | $0.0202 | **$-0.0169** | True |

## Intra-block reconstruction

### Block 52,620,870 (base), liquidation `0xa092…4c91`

Fork at N−1 with block N's timestamp pinned; 61 of 1748 transactions re-executed in order (1 system tx skipped, 592 skipped as unable to touch Aave/oracle state, 19 status mismatches).

| Observation | Value |
|---|---|
| Health factor at N−1 (boundary) | 1.003592791053931016 (eligible: False) |
| Health factor at block N's timestamp, before its transactions | 1.003592788820781813 (eligible: False) |
| Prices at N−1 (collateral / debt, oracle raw) | 237115930000 / 99990834 |
| Trigger transaction | index 424 `0x2669…4ce9` — **time_gated_oracle_reveal** ['price change matches the time-gated round of 0x4200000000000000000000000000000000000006; fork timestamp 1792031117 (block N + 30 s); not caused by this transaction'] |
| Health factor before → after trigger | 1.003592757582201148 → 0.99450125162639521 |
| Collateral price before → after trigger | 237115930000 → 234932418798 |
| Winning liquidation index in block | 653 |
| Transactions between trigger and liquidation | 228 |
| Health factor right before the liquidation | 0.994501217360278636 (eligible: True) |
| Time-gated round at N−1 for `0x4200…0006` | public price 237115930000 → hidden round 234932418798, revealed 30 s after block N's timestamp with no transaction |
| Our receiver executed at that exact position | status 1 surplus 3120952171 gas 684995  |

Transactions that changed the position or the two prices (health factor and oracle prices read after each):

| # | Tx | Kind | HF after | Collateral price | Debt price | Note |
|---|---|---|---|---|---|---|
| 2 | `0xd86c…f8ea` | other  | 1.003592787704238608 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 6 | `0xc107…ce6b` | other  | 1.003592786587695406 | 237115930000 | 99990834 |  |
| 8 | `0x1579…b8c3` | other  | 1.003592785471089426 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 45 | `0x9914…341a` | other  | 1.003592784354546232 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 49 | `0x6386…0156` | other  | 1.003592783237927128 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 53 | `0xe316…fac0` | other  | 1.00359278212728392 | 237115930000 | 99990834 |  |
| 55 | `0xe647…7cf1` | other  | 1.003592781010753867 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 59 | `0x8815…d2b8` | other  | 1.003592779894210686 | 237115930000 | 99990834 |  |
| 65 | `0x69b8…5e85` | other  | 1.003592778777591596 | 237115930000 | 99990834 |  |
| 71 | `0x5af6…6944` | other  | 1.003592777661048422 | 237115930000 | 99990834 |  |
| 77 | `0x6039…80e5` | other  | 1.003592776544505252 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 79 | `0x3771…9b14` | other  | 1.003592775427899303 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 81 | `0x36dc…9362` | other  | 1.00359277431129714 | 237115930000 | 99990834 |  |
| 89 | `0x948e…84c1` | other  | 1.00359277320065396 | 237115930000 | 99990834 |  |
| 90 | `0x760b…b21b` | other  | 1.003592772084110803 | 237115930000 | 99990834 |  |
| 94 | `0x7d23…ef53` | other  | 1.003592770967504869 | 237115930000 | 99990834 |  |
| 97 | `0xc4fb…ac55` | other  | 1.003592769858552972 | 237115930000 | 99990834 |  |
| 105 | `0xcb71…6d0b` | other  | 1.003592768741933913 | 237115930000 | 99990834 |  |
| 316 | `0x533e…1e2e` | other  | 1.003592767625390771 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 320 | `0xa466…62f0` | other  | 1.003592766508847632 | 237115930000 | 99990834 |  |
| 329 | `0xca7b…174b` | other  | 1.003592765392241714 | 237115930000 | 99990834 |  |
| 352 | `0xbc82…c508` | other  | 1.003592764281598562 | 237115930000 | 99990834 |  |
| 394 | `0x0ed7…62d7` | other  | 1.003592763165055433 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 396 | `0x42a9…e7ee` | other  | 1.003592762048512308 | 237115930000 | 99990834 |  |
| 410 | `0x9e1a…93ca` | other  | 1.003592760931906405 | 237115930000 | 99990834 |  |
| 417 | `0xd55c…653b` | other  | 1.003592759815439199 | 237115930000 | 99990834 |  |
| 422 | `0xa294…a696` | other  | 1.00359275869874426 | 237115930000 | 99990834 |  status fork=0 chain=1 |
| 423 | `0xb6cc…2b20` | other  | 1.003592757582201148 | 237115930000 | 99990834 |  |
| 424 | `0x2669…4ce9` | time_gated_oracle_reveal price change matches the time-gated round of 0x4200000000000000000000000000000000000006; fork timestamp 1792031117 (block N + 30 s); not caused by this transaction | 0.99450125162639521 | 234932418798 | 99990834 |  |
| 425 | `0xe3ba…d445` | other  | 0.994501250520057997 | 234932418798 | 99990834 |  |
| 446 | `0x99bd…1dff` | other  | 0.994501249419558083 | 234932418798 | 99990834 |  |
| 447 | `0x0475…64d2` | other  | 0.994501248313220877 | 234932418798 | 99990834 |  status fork=0 chain=1 |
| 451 | `0x7002…10e9` | other  | 0.994501247206896118 | 234932418798 | 99990834 |  status fork=0 chain=1 |
| 459 | `0x6a73…bbda` | other  | 0.99450124610055892 | 234932418798 | 99990834 |  status fork=0 chain=1 |
| 464 | `0xbc21…6be2` | other  | 0.994501244994158941 | 234932418798 | 99990834 |  |
| 467 | `0x3298…f3b4` | other  | 0.994501243887746524 | 234932418798 | 99990834 |  status fork=0 chain=1 |
| 479 | `0xe4d5…e724` | other  | 0.994501242781346553 | 234932418798 | 99990834 |  status fork=0 chain=1 |
| 528 | `0xbc61…18ba` | other  | 0.994501241675009368 | 234932418798 | 99990834 |  |
| 529 | `0xcf4c…9d53` | other  | 0.994501240574572264 | 234932418798 | 99990834 |  |
| 535 | `0xd026…dce7` | other  | 0.994501239475770137 | 234932418798 | 99990834 |  |

Assumptions: fork at N-1 with block N's timestamp pinned; each replayed transaction mined in its own block (+1 s); relevant-only mode: only transactions whose on-chain receipt logs touch the Pool, the oracle, a reserve token or a Chainlink feed are replayed; other transactions cannot change this position's health factor or prices and are skipped; replayed transactions use base fee 0 and gas price 0 so senders need no ETH for gas; value transfers are real; OP-stack system (deposit) transactions are skipped: they set L1 attributes only; classification uses the on-chain receipt logs of block N; health factor and prices are read from the fork after each transaction

Warnings: oracle for 0x4200000000000000000000000000000000000006 reveals a newer round 30 s after block N's timestamp with no transaction: time-gated feed (price 237115930000 -> 234932418798); 19 transaction(s) had a different status on the fork; state after them is approximate

## Time-gated oracle rounds at N−1 (Base)

Probe: fork at N−1, apply **no** transaction, advance only the block timestamp from block N's timestamp in 5 s steps and read the Aave oracle prices of the event's two reserves. A price that moves with time alone means the feed already stored a newer round at N−1 and only reveals it after a delay (a time-gated / SVR-style aggregator). `reveal_after_seconds = 0` means the round became public exactly at block N's timestamp.

| Figure | Value |
|---|---|
| Events / unique blocks probed | 24 / 17 |
| Events whose collateral or debt feed held a hidden newer round at N−1 | **18** |
| Reveal delays observed (s after block N's timestamp) | [0, 15, 30] |

| Tx | Block | Asset | Price at N−1 (public) | Hidden round price | Public after (s) |
|---|---|---|---|---|---|
| `0xd103…1df1` | 52,639,120 | `0x4200…0006` | 234842549109 | 234324677000 | 0 |
| `0x473a…1a2b` | 52,621,716 | `0xcbB7…33Bf` | 8106673000000 | 8121032000000 | 0 |
| `0x6afc…b026` | 52,621,005 | `0x4200…0006` | 235914168890 | 234672414683 | 0 |
| `0x6afc…b026` | 52,621,005 | `0x4200…0006` | 235914168890 | 234672414683 | 0 |
| `0x6afc…b026` | 52,621,005 | `0x04C0…150A` | 260861753922 | 259488685988 | 0 |
| `0x6afc…b026` | 52,621,005 | `0xcbB7…33Bf` | 8061527815094 | 8047283000000 | 15 |
| `0x6afc…b026` | 52,621,005 | `0x4200…0006` | 235914168890 | 234672414683 | 0 |
| `0xa092…4c91` | 52,620,870 | `0x4200…0006` | 237115930000 | 234932418798 | 30 |
| `0xa092…4c91` | 52,620,870 | `0x4200…0006` | 237115930000 | 234932418798 | 30 |
| `0xa092…4c91` | 52,620,870 | `0x4200…0006` | 237115930000 | 234932418798 | 30 |
| `0x8ba8…682e` | 52,620,840 | `0x4200…0006` | 238421000000 | 237115930000 | 30 |
| `0x58cb…70c0` | 52,620,840 | `0x2Ae3…Ec22` | 272174129691 | 270684301650 | 30 |
| `0x58cb…70c0` | 52,620,840 | `0x4200…0006` | 238421000000 | 237115930000 | 30 |
| `0x51c3…6d40` | 52,620,810 | `0x4200…0006` | 241028000000 | 240603000000 | 0 |
| `0x51c3…6d40` | 52,620,810 | `0x4200…0006` | 241028000000 | 240603000000 | 0 |
| `0x51c3…6d40` | 52,620,810 | `0x4200…0006` | 241028000000 | 240603000000 | 0 |
| `0x5c7e…b5be` | 52,599,798 | `0x4200…0006` | 241883000000 | 241513145251 | 30 |
| `0x0339…b2d2` | 52,598,898 | `0x4200…0006` | 243808000000 | 243131124289 | 30 |

## Time-gated oracle rounds at N−1 (Arbitrum)

Probe: fork at N−1, apply **no** transaction, advance only the block timestamp from block N's timestamp in 5 s steps and read the Aave oracle prices of the event's two reserves. A price that moves with time alone means the feed already stored a newer round at N−1 and only reveals it after a delay (a time-gated / SVR-style aggregator). `reveal_after_seconds = 0` means the round became public exactly at block N's timestamp.

| Figure | Value |
|---|---|
| Events / unique blocks probed | 4 / 4 |
| Events whose collateral or debt feed held a hidden newer round at N−1 | **1** |
| Reveal delays observed (s after block N's timestamp) | [30] |

| Tx | Block | Asset | Price at N−1 (public) | Hidden round price | Public after (s) |
|---|---|---|---|---|---|
| `0xf365…cc03` | 517,085,787 | `0x2f2a…5B0f` | 8366988428562 | 8361558397217 | 30 |

## Competitive shadow benchmark (live, read-only)

Window: 150.0 min from 2026-10-22T08:10:44Z on base via `base-rpc.publicnode.com`; watch set 348 positions (health factor < 1000000 at last reconcile); blocks observed 4380 (52,933,648 → 52,938,148).

| Measure (observed) | Value |
|---|---|
| Block observation lag (wall clock − block timestamp; includes clock skew + polling) | n=4380, median 0.318 s, p90 0.517 s, max 1.986 s |
| Detection latency (head seen → all watched health factors re-read, 3 Multicall3 round trip(s)) | n=4380, median 1.331 s, p90 1.512 s, max 4.761 s |
| Simulation latency (fork at head + receiver deploy + liquidate on private Anvil) | n=8, median 17.158 s, p90 18.860 s, max 20.610 s |
| Candidates that became liquidatable while watching | 0 (liquidated by a competitor: 0, healed: 0, still open: 0) |
| Blocks alive before a competitor took them | [] |
| Competitor `LiquidationCall` transactions in the window | 0 (same block as our detection: 0; before we ever flagged the borrower: 0; unique liquidators: 0) |
| Competitor priority fee / effective gas price (median, wei) | None / None |

Simulation latency probes:

| Block | Borrower | Seconds | Outcome |
|---|---|---|---|
| 52938946 | `0xA6Ec…1AF2` | 20.61 | ('0x930bb771', '0x930bb771') [HealthFactorNotBelowThreshold] |
| 52938970 | `0xA6Ec…1AF2` | 17.16 | ('0x930bb771', '0x930bb771') [HealthFactorNotBelowThreshold] |
| 52938991 | `0xA6Ec…1AF2` | 16.86 | ('0x930bb771', '0x930bb771') [HealthFactorNotBelowThreshold] |
| 52939013 | `0xA6Ec…1AF2` | 18.86 | ('0x930bb771', '0x930bb771') [HealthFactorNotBelowThreshold] |
| 52939035 | `0xA6Ec…1AF2` | 18.04 | ('0x930bb771', '0x930bb771') [HealthFactorNotBelowThreshold] |
| 52939057 | `0xA6Ec…1AF2` | 15.64 | ('0x930bb771', '0x930bb771') [HealthFactorNotBelowThreshold] |
| 52939077 | `0xA6Ec…1AF2` | 16.28 | ('0x930bb771', '0x930bb771') [HealthFactorNotBelowThreshold] |
| 52939098 | `0xA6Ec…1AF2` | 15.85 | ('0x930bb771', '0x930bb771') [HealthFactorNotBelowThreshold] |

Assumptions: block lag = wall clock at first observation minus block timestamp; includes sequencer clock skew and the polling interval; detection latency = monotonic time from first observation of the head to one Multicall3 read of every watched position; watch set = borrowers with debt whose last reconciled health factor was below the threshold; positions outside it are not detected; no transaction is ever built or sent; 'blocks after our detection' is a lower bound on how much faster a competitor was; simulation latency = fork reset at the live head + receiver deploy + liquidate() on a private Anvil forked from the archive endpoint, measured in a separate short run (see simulation_probe); the call reverts when the probed position is not liquidatable and the time is still what is measured

## Execution bids and win probability (observed vs modeled)

**Observed** (on-chain winners on the verified Pool):

| Measure | Value |
|---|---|
| Liquidation transactions / events / unique liquidators | 15 / 24 / 8 |
| Liquidator shares (top 5) | [0.2, 0.2, 0.2, 0.133, 0.067] |
| Winning effective gas price (wei): p25 / median / p75 / p90 / max | 12302484 / 40444595 / 95814445 / 300283674 / 309615000 |
| Winning gas used: median / p90 | 763377 / 3432596 |
| Winning L1 data fee (wei, median, receipt-verified) | 6753201748 |

**Modeled** (assumptions listed below):

| Parameter | Value |
|---|---|
| Effective competitors ready per opportunity | 8.0 |
| Our gas units (median measured on successful replays) | 565,190 |
| Our ready time assumed (detection p90 + simulation median from the benchmark) | 18.67 s |
| P(win) FCFS vs observed competitor reactions (0 samples) | None |
| Median simulated profit per profitable event | $0.0264 |

Bid curve (priority-auction model; expected profit per attempt at the median simulated profit):

| Bid | gwei | Our gas cost | P(win) | E[profit per attempt] |
|---|---|---|---|---|
| p25 | 0.0123 | $0.0278 | 0.000 | $-0.0097 |
| median | 0.0404 | $0.0915 | 0.002 | $-0.0321 |
| p75 | 0.0958 | $0.2166 | 0.039 | $-0.0803 |
| p90 | 0.3003 | $0.6789 | 0.318 | $-0.3697 |
| max | 0.3096 | $0.7000 | 0.576 | $-0.4918 |
| 2x_max | 0.6192 | $1.40 | 1.000 | $-1.37 |

Per-event valuation totals:

| Bucket | Events | Simulated profit | Competition-adjusted (FCFS) | Competition-adjusted (priority auction) |
|---|---|---|---|---|
| Observable at the block boundary | 1 | $0.4358 | not estimable (no reaction samples) | $-0.0312 |
| Only liquidatable inside the winning block | 5 | $3,120.36 | $0 (not capturable by boundary polling) | $0 |
| Not simulated profitably / cost incomplete | 18 | — | — | — |

Assumptions: competitor bids ~ empirical distribution of *winning* effective gas prices on this Pool (conservative: losers bid less); effective competitors ready per opportunity = number of liquidators with >= 5% share of observed wins (+ extra_competitors); our gas = median measured receiver gas on successful replays; L1 fee = median verified L1 fee of winners; failed attempt (lost race, reverted) costs failed_attempt_cost_share_of_gas of a full attempt's gas (revert happens early in the call); FCFS win probability = share of observed competitor reactions (blocks after our detection) slower than our assumed ready time; null when no reactions were observed; positions only liquidatable inside the winning block (eligible_at_fork=false) are not capturable by boundary polling; their profit is reported separately and excluded from capturable totals; profit per opportunity is the simulated receiver profit (complete costs only); theoretical profit is reported elsewhere

Warnings: no competitor reaction samples from the benchmark window: the FCFS win probability is not estimated (reported as null, not 0)

