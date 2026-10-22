// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

//////////////////////////////////////////////////////////////////////////////
//  CometLiquidator (research receiver, FlashLiquidator Lab Phase 5)        //
//                                                                          //
//  Compound V3 liquidation with an Aave V3 flash loan, in one transaction: //
//    flashLoanSimple(base) -> Comet.absorb(accounts) -> Comet.buyCollateral //
//    (base -> collateral at the storefront discount) -> swap collateral    //
//    back to base on Uniswap v3 SwapRouter02 -> repay principal + premium  //
//    -> forward the surplus.                                               //
//                                                                          //
//  Deployed only on private Anvil forks by the simulator. No public        //
//  deployment, no key material, no generic execute(). Same guards as       //
//  FlashLiquidator: allowlisted tokens/router, onlyPool, initiator check,  //
//  minimum profit, deadline, reentrancy.                                    //
//////////////////////////////////////////////////////////////////////////////

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

import {IPool} from "./interfaces/IPool.sol";
import {IPoolAddressesProvider} from "./interfaces/IPoolAddressesProvider.sol";
import {ISwapRouter02} from "./interfaces/ISwapRouter02.sol";

interface IComet {
    function absorb(address absorber, address[] calldata accounts) external;
    function buyCollateral(address asset, uint minAmount, uint baseAmount, address recipient) external;
    function quoteCollateral(address asset, uint baseAmount) external view returns (uint);
    function getCollateralReserves(address asset) external view returns (uint);
    function isLiquidatable(address account) external view returns (bool);
    function baseToken() external view returns (address);
}

contract CometLiquidator is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct Params {
        address comet;          // Comet market
        address collateralAsset; // collateral to buy after the absorb
        address[] accounts;     // accounts to absorb (may be empty: buy from existing reserves only)
        uint256 baseAmount;     // base token spent in buyCollateral (also the flash-loan principal)
        uint256 minCollateral;  // minimum collateral out of buyCollateral
        uint24 swapFeeTier;     // Uniswap v3 fee tier collateral -> base
        uint256 amountOutMinimum;
        uint256 minProfit;      // surplus floor in base units
        uint256 deadline;
        address router;
    }

    event SimulatedCometLiquidation(
        address indexed comet, address indexed collateralAsset, uint256 accountsAbsorbed, uint256 baseSpent,
        uint256 collateralBought, uint256 swapOut, uint256 premium, uint256 surplus
    );

    error Unauthorized(address caller);
    error NotPool(address caller);
    error BadInitiator(address initiator);
    error Expired(uint256 deadline, uint256 blockTimestamp);
    error TokenNotAllowed(address token);
    error RouterNotAllowed(address router);
    error CometNotAllowed(address comet);
    error InsufficientProfit(uint256 actual, uint256 required);
    error ZeroAddress();

    IPoolAddressesProvider public immutable ADDRESSES_PROVIDER;
    IPool public immutable POOL;
    address public profitRecipient;
    mapping(address => bool) public allowedTokens;
    mapping(address => bool) public allowedRouters;
    mapping(address => bool) public allowedComets;
    mapping(address => bool) public authorizedInitiators;

    constructor(address provider, address owner_, address recipient) Ownable(owner_) {
        if (provider == address(0) || recipient == address(0)) revert ZeroAddress();
        ADDRESSES_PROVIDER = IPoolAddressesProvider(provider);
        POOL = IPool(IPoolAddressesProvider(provider).getPool());
        profitRecipient = recipient;
        authorizedInitiators[owner_] = true;
    }

    function setTokenAllowed(address token, bool ok) external onlyOwner { allowedTokens[token] = ok; }
    function setRouterAllowed(address router, bool ok) external onlyOwner { allowedRouters[router] = ok; }
    function setCometAllowed(address comet, bool ok) external onlyOwner { allowedComets[comet] = ok; }
    function setInitiator(address who, bool ok) external onlyOwner { authorizedInitiators[who] = ok; }

    /// @notice Flash-financed absorb + buyCollateral + swap, atomically. Reverts unless the surplus >= minProfit.
    function liquidate(Params calldata p) external nonReentrant {
        if (!authorizedInitiators[msg.sender]) revert Unauthorized(msg.sender);
        if (block.timestamp > p.deadline) revert Expired(p.deadline, block.timestamp);
        if (!allowedComets[p.comet]) revert CometNotAllowed(p.comet);
        if (!allowedRouters[p.router]) revert RouterNotAllowed(p.router);
        if (!allowedTokens[p.collateralAsset]) revert TokenNotAllowed(p.collateralAsset);
        address base = IComet(p.comet).baseToken();
        if (!allowedTokens[base]) revert TokenNotAllowed(base);
        POOL.flashLoanSimple(address(this), base, p.baseAmount, abi.encode(p), 0);
    }

    function executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes calldata data)
        external
        returns (bool)
    {
        if (msg.sender != address(POOL)) revert NotPool(msg.sender);
        if (initiator != address(this)) revert BadInitiator(initiator);
        _run(asset, amount, premium, abi.decode(data, (Params)));
        return true;
    }

    /// @dev Kept separate from executeOperation so the decoded params and the measured figures fit the stack.
    function _run(address asset, uint256 amount, uint256 premium, Params memory p) internal {
        uint256 startBalance = IERC20(asset).balanceOf(address(this)) - amount;
        (uint256 bought, uint256 swapOut) = _absorbBuyAndSwap(p, asset);
        uint256 surplus = _settle(asset, amount + premium, startBalance, p.minProfit);
        _emitResult(p, bought, swapOut, premium, surplus);
    }

    function _emitResult(Params memory p, uint256 bought, uint256 swapOut, uint256 premium, uint256 surplus) internal {
        emit SimulatedCometLiquidation(p.comet, p.collateralAsset, p.accounts.length, p.baseAmount, bought, swapOut, premium, surplus);
    }

    /// @dev absorb (optional) -> buyCollateral at the storefront quote -> swap the collateral back to base.
    function _absorbBuyAndSwap(Params memory p, address asset) internal returns (uint256 bought, uint256 swapOut) {
        IComet comet = IComet(p.comet);
        if (p.accounts.length > 0) comet.absorb(address(this), p.accounts);
        IERC20(asset).forceApprove(p.comet, p.baseAmount);
        uint256 collBefore = IERC20(p.collateralAsset).balanceOf(address(this));
        comet.buyCollateral(p.collateralAsset, p.minCollateral, p.baseAmount, address(this));
        bought = IERC20(p.collateralAsset).balanceOf(address(this)) - collBefore;
        IERC20(p.collateralAsset).forceApprove(p.router, bought);
        swapOut = ISwapRouter02(p.router).exactInputSingle(
            ISwapRouter02.ExactInputSingleParams({
                tokenIn: p.collateralAsset, tokenOut: asset, fee: p.swapFeeTier, recipient: address(this),
                amountIn: bought, amountOutMinimum: p.amountOutMinimum, sqrtPriceLimitX96: 0
            })
        );
    }

    /// @dev profit floor, approval for the Pool's pull, surplus forwarded.
    function _settle(address asset, uint256 owed, uint256 startBalance, uint256 minProfit) internal returns (uint256 surplus) {
        uint256 finalBalance = IERC20(asset).balanceOf(address(this));
        uint256 gained = finalBalance > startBalance ? finalBalance - startBalance : 0;
        if (gained < owed + minProfit) revert InsufficientProfit(gained, owed + minProfit);
        IERC20(asset).forceApprove(address(POOL), owed);
        surplus = gained - owed;
        if (surplus > 0) IERC20(asset).safeTransfer(profitRecipient, surplus);
    }
}
