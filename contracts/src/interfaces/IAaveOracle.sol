// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IAaveOracle (minimal, local)
/// @notice The price-getter subset of Aave V3's AaveOracle. Used by the fork tests to read and
///         mock asset prices; the FlashLiquidator contract itself never reads the oracle.
interface IAaveOracle {
    /// @notice Price of `asset` in the market's base currency (USD, 8 decimals on Base).
    function getAssetPrice(address asset) external view returns (uint256);
    /// @notice Batched `getAssetPrice`.
    function getAssetsPrices(address[] calldata assets) external view returns (uint256[] memory);
    /// @notice One unit of the base currency (1e8 for USD markets).
    function BASE_CURRENCY_UNIT() external view returns (uint256);
}
