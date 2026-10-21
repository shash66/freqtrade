// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IPoolAddressesProvider (minimal, local)
/// @notice The subset of Aave V3's PoolAddressesProvider used by FlashLiquidator Lab.
interface IPoolAddressesProvider {
    /// @notice Returns the address of the Pool proxy of this Aave V3 market.
    function getPool() external view returns (address);
    /// @notice Returns the address of the price oracle of this market.
    function getPriceOracle() external view returns (address);
}
