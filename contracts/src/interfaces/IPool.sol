// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IPoolAddressesProvider} from "./IPoolAddressesProvider.sol";

/// @title IPool (minimal, local)
/// @notice The subset of the Aave V3 Pool interface used by FlashLiquidator Lab.
/// @dev Function signatures and struct layouts match aave-v3-origin (`IPool.sol`, `DataTypes.sol`).
///      Only what the contract, the fork tests and the deploy script call is declared here.
interface IPool {
    /// @notice Packed reserve configuration. Bit layout (see Aave `ReserveConfiguration.sol`):
    ///         0-15 LTV, 16-31 liquidation threshold, 32-47 liquidation bonus, 48-55 decimals,
    ///         56 active, 57 frozen, 58 borrowing enabled, 60 paused, 64-79 reserve factor,
    ///         152-167 liquidation protocol fee, 168-175 e-mode category (legacy), ...
    struct ReserveConfigurationMap {
        uint256 data;
    }

    /// @notice Reserve data as returned by `getReserveData` (ABI-stable "legacy" layout).
    struct ReserveData {
        ReserveConfigurationMap configuration;
        uint128 liquidityIndex;
        uint128 currentLiquidityRate;
        uint128 variableBorrowIndex;
        uint128 currentVariableBorrowRate;
        uint128 currentStableBorrowRate;
        uint40 lastUpdateTimestamp;
        uint16 id;
        address aTokenAddress;
        address stableDebtTokenAddress;
        address variableDebtTokenAddress;
        address interestRateStrategyAddress;
        uint128 accruedToTreasury;
        uint128 unbacked;
        uint128 isolationModeTotalDebt;
    }

    /// @notice The PoolAddressesProvider this Pool is registered with.
    function ADDRESSES_PROVIDER() external view returns (IPoolAddressesProvider);

    /// @notice Total flash-loan premium in basis points (e.g. 5 = 0.05 %).
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);

    /// @notice Single-asset flash loan. The receiver must implement `IFlashLoanSimpleReceiver`
    ///         and must approve the Pool for `amount + premium` before returning.
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;

    /// @notice Liquidates a position whose health factor is below 1.
    /// @param collateralAsset Collateral to seize.
    /// @param debtAsset Debt to repay.
    /// @param user Borrower being liquidated.
    /// @param debtToCover Debt amount to repay (capped by the protocol's close factor).
    /// @param receiveAToken True to receive aTokens, false to receive the underlying collateral.
    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;

    /// @notice Supplies `amount` of `asset` on behalf of `onBehalfOf` (used by fork tests only).
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;

    /// @notice Borrows `amount` of `asset` (interestRateMode 2 = variable) (used by fork tests only).
    function borrow(
        address asset,
        uint256 amount,
        uint256 interestRateMode,
        uint16 referralCode,
        address onBehalfOf
    ) external;

    /// @notice Aggregate account data; `healthFactor` is scaled by 1e18.
    function getUserAccountData(address user)
        external
        view
        returns (
            uint256 totalCollateralBase,
            uint256 totalDebtBase,
            uint256 availableBorrowsBase,
            uint256 currentLiquidationThreshold,
            uint256 ltv,
            uint256 healthFactor
        );

    /// @notice Packed configuration bitmap of a reserve.
    function getConfiguration(address asset) external view returns (ReserveConfigurationMap memory);

    /// @notice Reserve state including the aToken address (used by fork tests only).
    function getReserveData(address asset) external view returns (ReserveData memory);
}
