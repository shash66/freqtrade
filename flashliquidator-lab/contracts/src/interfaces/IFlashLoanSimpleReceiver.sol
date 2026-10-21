// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IPool} from "./IPool.sol";
import {IPoolAddressesProvider} from "./IPoolAddressesProvider.sol";

/// @title IFlashLoanSimpleReceiver (minimal, local)
/// @notice Interface a contract must implement to receive an Aave V3 `flashLoanSimple`.
/// @dev Mirrors aave-v3-origin `IFlashLoanSimpleReceiver.sol`.
interface IFlashLoanSimpleReceiver {
    /// @notice Called by the Pool after the flash-loaned `amount` has been transferred to the receiver.
    /// @param asset Flash-loaned asset.
    /// @param amount Flash-loaned amount.
    /// @param premium Fee owed on top of `amount`.
    /// @param initiator Account that called `flashLoanSimple` on the Pool.
    /// @param params Arbitrary bytes passed through by the initiator.
    /// @return True if the operation succeeded; the Pool reverts otherwise.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);

    /// @notice The addresses provider of the market this receiver is bound to.
    function ADDRESSES_PROVIDER() external view returns (IPoolAddressesProvider);

    /// @notice The Pool this receiver accepts flash loans from.
    function POOL() external view returns (IPool);
}
