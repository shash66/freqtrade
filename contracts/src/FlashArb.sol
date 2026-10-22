// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

//////////////////////////////////////////////////////////////////////////////
//  FlashArb (research receiver, FlashLiquidator Lab Phase 5)                //
//                                                                          //
//  Two-leg atomic arbitrage financed by an Aave V3 flash loan:             //
//    flashLoanSimple(tokenIn) -> leg 1 (tokenIn -> tokenMid on venue A)     //
//    -> leg 2 (tokenMid -> tokenIn on venue B) -> repay principal + premium //
//    -> forward the surplus. Reverts unless the surplus >= minProfit.       //
//                                                                          //
//  Venues: Uniswap v3 SwapRouter02 (exactInputSingle) and Solidly-style    //
//  routers (Aerodrome/Velodrome: swapExactTokensForTokens with a route).   //
//  Deployed only on private Anvil forks by the simulator. Allowlisted      //
//  tokens and routers, onlyPool, initiator check, deadline, reentrancy.    //
//////////////////////////////////////////////////////////////////////////////

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

import {IPool} from "./interfaces/IPool.sol";
import {IPoolAddressesProvider} from "./interfaces/IPoolAddressesProvider.sol";
import {ISwapRouter02} from "./interfaces/ISwapRouter02.sol";

interface ISolidlyRouter {
    struct Route { address from; address to; bool stable; address factory; }
    function swapExactTokensForTokens(uint256 amountIn, uint256 amountOutMin, Route[] calldata routes, address to, uint256 deadline)
        external returns (uint256[] memory amounts);
}

contract FlashArb is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint8 public constant KIND_UNIV3 = 0;
    uint8 public constant KIND_SOLIDLY = 1;

    struct Leg {
        uint8 kind;        // KIND_UNIV3 | KIND_SOLIDLY
        address router;
        address tokenIn;
        address tokenOut;
        uint24 fee;        // Uniswap v3 fee tier
        bool stable;       // Solidly pool type
        address factory;   // Solidly factory
        uint256 amountOutMin;
    }

    struct Params {
        address tokenIn;   // flash-loaned asset, also the final asset
        uint256 amountIn;  // flash-loan principal
        Leg[] legs;        // executed in order; last leg must end in tokenIn
        uint256 minProfit; // surplus floor in tokenIn units
        uint256 deadline;
    }

    event SimulatedArbitrage(address indexed tokenIn, uint256 amountIn, uint256 amountOut, uint256 premium, uint256 surplus, uint256 legs);

    error Unauthorized(address caller);
    error NotPool(address caller);
    error BadInitiator(address initiator);
    error Expired(uint256 deadline, uint256 blockTimestamp);
    error TokenNotAllowed(address token);
    error RouterNotAllowed(address router);
    error BadRoute();
    error InsufficientProfit(uint256 actual, uint256 required);
    error ZeroAddress();

    IPoolAddressesProvider public immutable ADDRESSES_PROVIDER;
    IPool public immutable POOL;
    address public profitRecipient;
    mapping(address => bool) public allowedTokens;
    mapping(address => bool) public allowedRouters;
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
    function setInitiator(address who, bool ok) external onlyOwner { authorizedInitiators[who] = ok; }

    function arbitrage(Params calldata p) external nonReentrant {
        if (!authorizedInitiators[msg.sender]) revert Unauthorized(msg.sender);
        if (block.timestamp > p.deadline) revert Expired(p.deadline, block.timestamp);
        if (p.legs.length == 0 || p.legs[0].tokenIn != p.tokenIn || p.legs[p.legs.length - 1].tokenOut != p.tokenIn) revert BadRoute();
        for (uint256 i = 0; i < p.legs.length; i++) {
            if (!allowedRouters[p.legs[i].router]) revert RouterNotAllowed(p.legs[i].router);
            if (!allowedTokens[p.legs[i].tokenIn]) revert TokenNotAllowed(p.legs[i].tokenIn);
            if (!allowedTokens[p.legs[i].tokenOut]) revert TokenNotAllowed(p.legs[i].tokenOut);
            if (i > 0 && p.legs[i].tokenIn != p.legs[i - 1].tokenOut) revert BadRoute();
        }
        POOL.flashLoanSimple(address(this), p.tokenIn, p.amountIn, abi.encode(p), 0);
    }

    function executeOperation(address asset, uint256 amount, uint256 premium, address initiator, bytes calldata data)
        external
        returns (bool)
    {
        if (msg.sender != address(POOL)) revert NotPool(msg.sender);
        if (initiator != address(this)) revert BadInitiator(initiator);
        Params memory p = abi.decode(data, (Params));
        uint256 startBalance = IERC20(asset).balanceOf(address(this)) - amount;
        uint256 running = amount;
        for (uint256 i = 0; i < p.legs.length; i++) {
            running = _swap(p.legs[i], running);
        }
        uint256 surplus = _settle(asset, amount + premium, startBalance, p.minProfit);
        emit SimulatedArbitrage(asset, amount, running, premium, surplus, p.legs.length);
        return true;
    }

    function _swap(Leg memory leg, uint256 amountIn) internal returns (uint256 out) {
        IERC20(leg.tokenIn).forceApprove(leg.router, amountIn);
        if (leg.kind == KIND_UNIV3) {
            out = ISwapRouter02(leg.router).exactInputSingle(
                ISwapRouter02.ExactInputSingleParams({
                    tokenIn: leg.tokenIn, tokenOut: leg.tokenOut, fee: leg.fee, recipient: address(this),
                    amountIn: amountIn, amountOutMinimum: leg.amountOutMin, sqrtPriceLimitX96: 0
                })
            );
        } else {
            ISolidlyRouter.Route[] memory routes = new ISolidlyRouter.Route[](1);
            routes[0] = ISolidlyRouter.Route({from: leg.tokenIn, to: leg.tokenOut, stable: leg.stable, factory: leg.factory});
            uint256[] memory amounts = ISolidlyRouter(leg.router).swapExactTokensForTokens(amountIn, leg.amountOutMin, routes, address(this), block.timestamp);
            out = amounts[amounts.length - 1];
        }
    }

    function _settle(address asset, uint256 owed, uint256 startBalance, uint256 minProfit) internal returns (uint256 surplus) {
        uint256 finalBalance = IERC20(asset).balanceOf(address(this));
        uint256 gained = finalBalance > startBalance ? finalBalance - startBalance : 0;
        if (gained < owed + minProfit) revert InsufficientProfit(gained, owed + minProfit);
        IERC20(asset).forceApprove(address(POOL), owed);
        surplus = gained - owed;
        if (surplus > 0) IERC20(asset).safeTransfer(profitRecipient, surplus);
    }
}
