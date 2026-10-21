// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/*//////////////////////////////////////////////////////////////////////////////
//                                                                            //
//   FlashLiquidator Lab — RESEARCH TOOL                                      //
//                                                                            //
//   * Fork-simulation only. This contract is written to be deployed to a     //
//     local Anvil fork of an Aave V3 market so that liquidation mechanics    //
//     (flash-loan premium, liquidation bonus, protocol fee, DEX slippage)    //
//     can be measured. There is no mainnet deployment path in this repo.     //
//   * No production audit. Nothing here has been audited and it must not be  //
//     used with real funds.                                                  //
//   * Fixed execution path: liquidate -> flashLoanSimple -> liquidationCall  //
//     -> exactInputSingle -> repay. There is no generic `execute(target,     //
//     data)` and the contract makes no arbitrary external calls.             //
//                                                                            //
//////////////////////////////////////////////////////////////////////////////*/

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";

import {IPool} from "./interfaces/IPool.sol";
import {IPoolAddressesProvider} from "./interfaces/IPoolAddressesProvider.sol";
import {IFlashLoanSimpleReceiver} from "./interfaces/IFlashLoanSimpleReceiver.sol";
import {ISwapRouter02} from "./interfaces/ISwapRouter02.sol";

/// @title FlashLiquidator
/// @author FlashLiquidator Lab
/// @notice Aave V3 `flashLoanSimple` receiver that performs one liquidation per call:
///         borrow the debt asset, call `liquidationCall`, swap the seized collateral back to the
///         debt asset on Uniswap v3 (SwapRouter02), repay principal plus premium, and forward
///         any surplus to `profitRecipient`.
/// @dev Research tool; fork-simulation only; no production audit. See header comment.
///      Design notes:
///        - Only the owner may initiate (`liquidate`). The Pool is the only allowed caller of
///          `executeOperation`, and the initiator must be this contract, so a third party cannot
///          route a flash loan through this receiver.
///        - Routers and tokens are allowlisted by the owner; nothing else is ever called.
///        - Pre-existing balances cannot subsidise a trade: profit is measured against the
///          debt-asset balance observed before the flash-loaned amount arrived.
///        - `SafeERC20.forceApprove` is used for every approval (USDT-style tokens included).
contract FlashLiquidator is IFlashLoanSimpleReceiver, Ownable2Step, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ---------------------------------------------------------------------
    // Types
    // ---------------------------------------------------------------------

    /// @notice Full description of one liquidation attempt. Encoded into the flash-loan `params`.
    /// @param collateralAsset Collateral to seize from `user`.
    /// @param debtAsset Debt asset to flash-borrow and repay on behalf of `user`.
    /// @param user Borrower whose position is liquidated.
    /// @param debtToCover Amount of `debtAsset` to flash-borrow and pass to `liquidationCall`.
    /// @param swapFeeTier Uniswap v3 fee tier of the collateral/debt pool (e.g. 500 = 0.05 %).
    /// @param minProfit Minimum surplus, in `debtAsset` units, below which the whole call reverts.
    /// @param amountOutMinimum Slippage bound for the collateral -> debt swap, in `debtAsset` units.
    /// @param deadline Unix timestamp after which the attempt reverts with `Expired`.
    /// @param router Uniswap v3 SwapRouter02 address to use; must be allowlisted.
    struct LiquidationParams {
        address collateralAsset;
        address debtAsset;
        address user;
        uint256 debtToCover;
        uint24 swapFeeTier;
        uint256 minProfit;
        uint256 amountOutMinimum;
        uint256 deadline;
        address router;
    }

    // ---------------------------------------------------------------------
    // Errors (one per revert path)
    // ---------------------------------------------------------------------

    /// @notice `liquidate` was called by an account other than the owner.
    error Unauthorized(address caller);
    /// @notice `executeOperation` was called by an account other than the Aave Pool.
    error NotPool(address caller);
    /// @notice The flash loan was not initiated by this contract.
    error BadInitiator(address initiator);
    /// @notice `block.timestamp` is past the supplied deadline.
    error Expired(uint256 deadline, uint256 blockTimestamp);
    /// @notice The token is not on the owner-managed allowlist.
    error TokenNotAllowed(address token);
    /// @notice The router is not on the owner-managed allowlist.
    error RouterNotAllowed(address router);
    /// @notice The debt-asset balance gained is below principal + premium + minProfit.
    error InsufficientProfit(uint256 actual, uint256 required);
    /// @notice The swap returned less than `amountOutMinimum` (defensive; the router also enforces this).
    error SwapFailed(uint256 amountOut, uint256 amountOutMinimum);
    /// @notice The flash loan delivered a different asset/amount than the encoded params describe.
    error FlashLoanMismatch(address asset, uint256 amount);
    /// @notice `liquidationCall` returned no collateral to this contract.
    error NoCollateralSeized();
    /// @notice A zero address was supplied where a real address is required.
    error ZeroAddress();
    /// @notice A zero amount was supplied where a positive amount is required.
    error ZeroAmount();

    // ---------------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------------

    /// @notice Emitted once per successful simulated liquidation.
    /// @param user Liquidated borrower.
    /// @param collateralAsset Collateral seized.
    /// @param debtAsset Debt repaid (and flash-borrowed).
    /// @param debtToCover Debt amount requested from the flash loan and passed to `liquidationCall`.
    /// @param collateralReceived Collateral actually received (after Aave's liquidation protocol fee).
    /// @param swapOut Debt-asset amount received from the DEX swap.
    /// @param premium Flash-loan premium charged by the Pool.
    /// @param surplus Debt-asset amount forwarded to `profitRecipient`.
    event SimulatedLiquidation(
        address indexed user,
        address indexed collateralAsset,
        address indexed debtAsset,
        uint256 debtToCover,
        uint256 collateralReceived,
        uint256 swapOut,
        uint256 premium,
        uint256 surplus
    );
    /// @notice Emitted when the owner changes a router's allowlist status.
    event RouterAllowedSet(address indexed router, bool allowed);
    /// @notice Emitted when the owner changes a token's allowlist status.
    event TokenAllowedSet(address indexed token, bool allowed);
    /// @notice Emitted when the owner changes the profit recipient.
    event ProfitRecipientSet(address indexed previousRecipient, address indexed newRecipient);
    /// @notice Emitted when the owner sweeps a token balance out of the contract.
    event TokenRescued(address indexed token, address indexed to, uint256 amount);

    // ---------------------------------------------------------------------
    // Storage
    // ---------------------------------------------------------------------

    /// @inheritdoc IFlashLoanSimpleReceiver
    IPoolAddressesProvider public immutable override ADDRESSES_PROVIDER;
    /// @inheritdoc IFlashLoanSimpleReceiver
    IPool public immutable override POOL;

    /// @notice Account that receives the surplus of every successful liquidation.
    address public profitRecipient;
    /// @notice Owner-managed allowlist of DEX routers that may be called.
    mapping(address router => bool allowed) public routerAllowed;
    /// @notice Owner-managed allowlist of tokens that may be used as collateral or debt asset.
    mapping(address token => bool allowed) public tokenAllowed;

    // ---------------------------------------------------------------------
    // Modifiers
    // ---------------------------------------------------------------------

    /// @dev Restricts a function to the owner, reverting with the package's own `Unauthorized` error
    ///      (OpenZeppelin's `onlyOwner` is kept for the administrative setters).
    modifier onlyAuthorizedInitiator() {
        if (msg.sender != owner()) revert Unauthorized(msg.sender);
        _;
    }

    // ---------------------------------------------------------------------
    // Constructor
    // ---------------------------------------------------------------------

    /// @param provider Aave V3 PoolAddressesProvider of the target market; the Pool is read from it.
    /// @param initialOwner Account allowed to call `liquidate` and the administrative functions.
    /// @param profitRecipient_ Account that receives liquidation surplus.
    constructor(IPoolAddressesProvider provider, address initialOwner, address profitRecipient_)
        Ownable(initialOwner)
    {
        if (address(provider) == address(0) || profitRecipient_ == address(0)) revert ZeroAddress();
        address pool = provider.getPool();
        if (pool == address(0)) revert ZeroAddress();
        ADDRESSES_PROVIDER = provider;
        POOL = IPool(pool);
        profitRecipient = profitRecipient_;
        emit ProfitRecipientSet(address(0), profitRecipient_);
    }

    // ---------------------------------------------------------------------
    // Owner: initiate a liquidation
    // ---------------------------------------------------------------------

    /// @notice Requests a flash loan of `p.debtToCover` `p.debtAsset` and performs the liquidation in
    ///         `executeOperation`. Reverts (and so leaves no state change) unless the surplus is at
    ///         least `p.minProfit`.
    /// @dev The allowlist and deadline checks are repeated inside `executeOperation`; running them here
    ///      first fails fast without opening a flash loan.
    /// @param p Liquidation parameters; see `LiquidationParams`.
    function liquidate(LiquidationParams calldata p) external onlyAuthorizedInitiator nonReentrant {
        if (p.user == address(0)) revert ZeroAddress();
        if (p.debtToCover == 0) revert ZeroAmount();
        if (block.timestamp > p.deadline) revert Expired(p.deadline, block.timestamp);
        if (!routerAllowed[p.router]) revert RouterNotAllowed(p.router);
        if (!tokenAllowed[p.debtAsset]) revert TokenNotAllowed(p.debtAsset);
        if (!tokenAllowed[p.collateralAsset]) revert TokenNotAllowed(p.collateralAsset);

        POOL.flashLoanSimple(address(this), p.debtAsset, p.debtToCover, abi.encode(p), 0);
    }

    // ---------------------------------------------------------------------
    // Aave callback
    // ---------------------------------------------------------------------

    /// @inheritdoc IFlashLoanSimpleReceiver
    /// @dev Fixed sequence: validate -> record start balance -> liquidationCall -> swap -> profit check
    ///      -> approve repayment -> forward surplus -> emit. The Pool pulls `amount + premium` after
    ///      this function returns `true`.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        if (msg.sender != address(POOL)) revert NotPool(msg.sender);
        if (initiator != address(this)) revert BadInitiator(initiator);

        LiquidationParams memory p = abi.decode(params, (LiquidationParams));
        _validate(p, asset, amount);
        _run(p, asset, amount, premium);
        return true;
    }

    // ---------------------------------------------------------------------
    // Owner: configuration and maintenance
    // ---------------------------------------------------------------------

    /// @notice Allows or disallows a DEX router.
    function setRouterAllowed(address router, bool allowed) external onlyOwner {
        if (router == address(0)) revert ZeroAddress();
        routerAllowed[router] = allowed;
        emit RouterAllowedSet(router, allowed);
    }

    /// @notice Allows or disallows a token for use as collateral or debt asset.
    function setTokenAllowed(address token, bool allowed) external onlyOwner {
        if (token == address(0)) revert ZeroAddress();
        tokenAllowed[token] = allowed;
        emit TokenAllowedSet(token, allowed);
    }

    /// @notice Changes the account that receives liquidation surplus.
    function setProfitRecipient(address newRecipient) external onlyOwner {
        if (newRecipient == address(0)) revert ZeroAddress();
        emit ProfitRecipientSet(profitRecipient, newRecipient);
        profitRecipient = newRecipient;
    }

    /// @notice Sweeps the contract's entire balance of `token` to `to`.
    /// @dev Research convenience: on a fork, balances can end up here (e.g. tokens `deal`t to the
    ///      contract, or a partial liquidation leaving dust). The contract never relies on holding
    ///      a balance, so sweeping cannot break a later liquidation. Owner-only.
    /// @param token ERC-20 to sweep.
    /// @param to Recipient of the full balance.
    function rescueToken(address token, address to) external onlyOwner {
        if (token == address(0) || to == address(0)) revert ZeroAddress();
        uint256 balance = IERC20(token).balanceOf(address(this));
        if (balance == 0) revert ZeroAmount();
        IERC20(token).safeTransfer(to, balance);
        emit TokenRescued(token, to, balance);
    }

    // ---------------------------------------------------------------------
    // Internals
    // ---------------------------------------------------------------------

    /// @dev Parameter checks performed inside the flash-loan callback.
    function _validate(LiquidationParams memory p, address asset, uint256 amount) internal view {
        if (block.timestamp > p.deadline) revert Expired(p.deadline, block.timestamp);
        if (asset != p.debtAsset || amount != p.debtToCover) revert FlashLoanMismatch(asset, amount);
        if (!routerAllowed[p.router]) revert RouterNotAllowed(p.router);
        if (!tokenAllowed[p.debtAsset]) revert TokenNotAllowed(p.debtAsset);
        if (!tokenAllowed[p.collateralAsset]) revert TokenNotAllowed(p.collateralAsset);
    }

    /// @dev Liquidate, swap, settle and emit. Kept separate from `executeOperation` to limit the
    ///      number of live stack variables (the project compiles without `via_ir`).
    function _run(LiquidationParams memory p, address asset, uint256 amount, uint256 premium) internal {
        // Balance before the flash-loaned amount arrived: pre-existing funds never count as profit.
        uint256 startBalance = IERC20(asset).balanceOf(address(this)) - amount;

        (uint256 collateralReceived, uint256 swapOut) = _liquidateAndSwap(p);
        uint256 surplus = _settle(p, asset, amount + premium, startBalance);

        emit SimulatedLiquidation(
            p.user, p.collateralAsset, asset, p.debtToCover, collateralReceived, swapOut, premium, surplus
        );
    }

    /// @dev Repays the borrower's debt via `liquidationCall` and swaps the seized collateral back to
    ///      the debt asset on the allowlisted router.
    /// @return collateralReceived Collateral delta observed on this contract (net of Aave's protocol fee).
    /// @return swapOut Debt-asset amount returned by the router.
    function _liquidateAndSwap(LiquidationParams memory p)
        internal
        returns (uint256 collateralReceived, uint256 swapOut)
    {
        IERC20 collateral = IERC20(p.collateralAsset);

        IERC20(p.debtAsset).forceApprove(address(POOL), p.debtToCover);
        uint256 collateralBefore = collateral.balanceOf(address(this));
        POOL.liquidationCall(p.collateralAsset, p.debtAsset, p.user, p.debtToCover, false);
        collateralReceived = collateral.balanceOf(address(this)) - collateralBefore;
        if (collateralReceived == 0) revert NoCollateralSeized();

        collateral.forceApprove(p.router, collateralReceived);
        swapOut = ISwapRouter02(p.router).exactInputSingle(
            ISwapRouter02.ExactInputSingleParams({
                tokenIn: p.collateralAsset,
                tokenOut: p.debtAsset,
                fee: p.swapFeeTier,
                recipient: address(this),
                amountIn: collateralReceived,
                amountOutMinimum: p.amountOutMinimum,
                sqrtPriceLimitX96: 0
            })
        );
        if (swapOut < p.amountOutMinimum) revert SwapFailed(swapOut, p.amountOutMinimum);
    }

    /// @dev Enforces the profit floor, approves the Pool to pull the repayment and forwards the surplus.
    /// @param amountOwed Flash-loan principal plus premium.
    /// @param startBalance Debt-asset balance before the flash loan arrived.
    /// @return surplus Amount forwarded to `profitRecipient`.
    function _settle(LiquidationParams memory p, address asset, uint256 amountOwed, uint256 startBalance)
        internal
        returns (uint256 surplus)
    {
        uint256 finalBalance = IERC20(asset).balanceOf(address(this));
        uint256 gained = finalBalance > startBalance ? finalBalance - startBalance : 0;
        uint256 required = amountOwed + p.minProfit;
        if (gained < required) revert InsufficientProfit(gained, required);

        // The Pool pulls `amountOwed` via transferFrom after executeOperation returns.
        IERC20(asset).forceApprove(address(POOL), amountOwed);

        surplus = gained - amountOwed;
        if (surplus != 0) IERC20(asset).safeTransfer(profitRecipient, surplus);
    }
}
