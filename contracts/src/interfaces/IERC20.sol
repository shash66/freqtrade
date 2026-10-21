// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IERC20 (minimal, local)
/// @notice The subset of the ERC-20 interface used by FlashLiquidator Lab tests and scripts.
/// @dev FlashLiquidator.sol itself uses OpenZeppelin's IERC20 because SafeERC20 is typed on it;
///      this local copy exists so the test-suite and scripts do not depend on external sources.
interface IERC20 {
    /// @notice Emitted when `value` tokens move from `from` to `to`.
    event Transfer(address indexed from, address indexed to, uint256 value);
    /// @notice Emitted when `owner` sets the allowance of `spender` to `value`.
    event Approval(address indexed owner, address indexed spender, uint256 value);

    /// @notice Total token supply.
    function totalSupply() external view returns (uint256);
    /// @notice Token balance of `account`.
    function balanceOf(address account) external view returns (uint256);
    /// @notice Transfers `amount` to `to`.
    function transfer(address to, uint256 amount) external returns (bool);
    /// @notice Remaining allowance `spender` has over `owner`'s tokens.
    function allowance(address owner, address spender) external view returns (uint256);
    /// @notice Sets the allowance of `spender` to `amount`.
    function approve(address spender, uint256 amount) external returns (bool);
    /// @notice Transfers `amount` from `from` to `to` using the caller's allowance.
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    /// @notice Number of decimals the token uses.
    function decimals() external view returns (uint8);
}
