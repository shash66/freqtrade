// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ISwapRouter02 (minimal, local)
/// @notice The `exactInputSingle` entry point of Uniswap v3 SwapRouter02.
/// @dev Note that SwapRouter02's params struct has NO `deadline` field (unlike the original
///      SwapRouter). The deadline is enforced by FlashLiquidator itself before swapping.
interface ISwapRouter02 {
    /// @notice Parameters for a single-hop exact-input swap.
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    /// @notice Swaps `amountIn` of `tokenIn` for as much `tokenOut` as possible on one pool.
    /// @return amountOut Amount of `tokenOut` received; reverts with "Too little received" if below `amountOutMinimum`.
    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}
