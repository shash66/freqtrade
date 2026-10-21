# Data sources

| Data | Source | Verification |
|---|---|---|
| Aave V3 addresses (Base, Arbitrum) | `@bgd-labs/aave-address-book` npm package, version pinned in `backend/app/aave/address_book.py` | chain id, non-empty bytecode, `Pool.ADDRESSES_PROVIDER()` must equal the book's provider |
| Borrower discovery | `Supply/Borrow/Repay/Withdraw/LiquidationCall/ReserveUsedAsCollateral*/UserEModeSet` events from the Pool | idempotent on (chain, tx, log index); reorg rollback via retained block hashes |
| Positions | `Pool.getUserAccountData`, `PoolDataProvider.getUserReserveData`, `Pool.getUserEMode` | authoritative reads, re-read every run for prioritized borrowers |
| Reserve parameters | `Pool.getConfiguration` bitmap, `PoolDataProvider.getReserveTokensAddresses` | decoded per aave-v3-origin layout; unit-tested |
| Prices | `AaveOracle.getAssetsPrices` (8 decimals, USD) | stored per block in `oracle_price_observations` |
| Flash-loan premium | `Pool.FLASHLOAN_PREMIUM_TOTAL` | read at verification and on every fork |
| Swap proceeds | Uniswap v3 QuoterV2 (`eth_call`), fee tiers 0.01/0.05/0.3/1% | bytecode check; `incomplete` when no pool quotes |
| Historical liquidations | `LiquidationCall` events + transaction receipts (gas used, effective gas price, index) | NULL when the receipt cannot be fetched; never estimated |
| Fork state | Anvil forking the configured archive RPC at N-1 | block hash recorded on every run |
| L1 data fee (estimate) | Base: `GasPriceOracle.getL1Fee` (0x4200…000F) on the serialized tx; Arbitrum: `NodeInterface.gasEstimateL1Component` (0x…C8) | labeled `estimated`; `unknown` on failure |
| L1 data fee (verified) | receipt `l1Fee` (OP stack) / `gasUsedForL1 × effectiveGasPrice` (Arbitrum) | labeled `verified`; stored on `historical_liquidations` |
| Swap execution on forks | Uniswap SwapRouter02 (Base 0x2626…e481, Arbitrum 0x68b3…Fc45) | bytecode checked on the fork before use |

No subgraph or third-party indexer is used. Nothing is cached across restarts except the database.

## RPC requirements and paid services

- Live monitoring: any JSON-RPC endpoint for Base and Arbitrum that supports `eth_getLogs` over ranges of
  ~2,000 blocks. Public endpoints usually work but rate-limit; the indexer halves ranges on errors.
- Historical replay and backfill beyond a provider's free window: an **archive** endpoint
  (`BASE_ARCHIVE_RPC`, `ARBITRUM_ARCHIVE_RPC`). Public endpoints rarely serve archive state; Alchemy,
  QuickNode, dRPC and similar paid plans do. Without one, historical simulations are recorded as skipped.
- Nothing in this application requires an API key beyond what is embedded in the RPC URL, which stays server-side.
