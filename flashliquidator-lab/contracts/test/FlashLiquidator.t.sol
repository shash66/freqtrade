// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/*//////////////////////////////////////////////////////////////////////////////
//  FlashLiquidator fork tests (Base, Aave V3).                               //
//                                                                            //
//  REQUIRES an archive RPC for Base in the environment variable               //
//  BASE_ARCHIVE_RPC. Run with:                                               //
//                                                                            //
//      forge test --fork-url $BASE_ARCHIVE_RPC -vvv                          //
//                                                                            //
//  Optional: BASE_FORK_BLOCK overrides the pinned fork block.                //
//  If BASE_ARCHIVE_RPC is not set every test is marked SKIPPED (vm.skip)     //
//  with an explanation; it is never reported as passed.                      //
//                                                                            //
//  Research tool; fork-simulation only; no production audit.                 //
//  The borrower is synthetic (created in setUp) and the WETH oracle price is //
//  mocked downwards to make it liquidatable, so the surplus these tests see  //
//  is NOT a realistic profit figure: the DEX still trades at the real price. //
//////////////////////////////////////////////////////////////////////////////*/

import {Test, console2} from "forge-std/Test.sol";
import {Vm} from "forge-std/Vm.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

import {FlashLiquidator} from "../src/FlashLiquidator.sol";
import {IERC20} from "../src/interfaces/IERC20.sol";
import {IPool} from "../src/interfaces/IPool.sol";
import {IPoolAddressesProvider} from "../src/interfaces/IPoolAddressesProvider.sol";
import {IAaveOracle} from "../src/interfaces/IAaveOracle.sol";

contract FlashLiquidatorForkTest is Test {
    // ------------------------------------------------------------------
    // Base mainnet addresses (Aave V3 Base market, Uniswap v3 SwapRouter02)
    // ------------------------------------------------------------------
    address constant POOL_ADDRESSES_PROVIDER = 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address constant POOL = 0xA238Dd80C259a72e81d7e4664a9801593F98d1c5;
    address constant ORACLE = 0x2Cc0Fc26eD4563A5ce5e8bdcfe1A2878676Ae156;
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant ROUTER = 0x2626664c2603336E57B271c5C0b26F421741e481;
    /// @dev cbETH on Base: used only as an example of a token that is NOT allowlisted.
    address constant NOT_ALLOWLISTED_TOKEN = 0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22;

    /// @dev Pinned fork block (Base, ~May 2025). Override with BASE_FORK_BLOCK.
    uint256 constant DEFAULT_FORK_BLOCK = 30_000_000;

    uint24 constant FEE_TIER_005 = 500;
    uint256 constant WETH_SUPPLY = 10 ether;
    /// @dev Target health factor after the oracle mock (below 1e18 => liquidatable).
    uint256 constant TARGET_HF = 0.90e18;
    /// @dev Share of the borrower's debt that each test liquidates (keeps clear of Aave's
    ///      close-factor and "no dust leftover" rules in every 3.x version).
    uint256 constant DEBT_SHARE_PCT = 40;

    bytes32 constant SIMULATED_LIQUIDATION_SIG =
        keccak256("SimulatedLiquidation(address,address,address,uint256,uint256,uint256,uint256,uint256)");

    // ------------------------------------------------------------------
    // State
    // ------------------------------------------------------------------
    IPool pool = IPool(POOL);
    IAaveOracle oracle = IAaveOracle(ORACLE);
    FlashLiquidator liq;

    address owner;
    address recipient;
    address borrower;
    address attacker;

    bool forkReady;
    uint256 wethPrice; // real oracle price at the fork block (8 decimals)
    uint256 usdcPrice; // real oracle price at the fork block (8 decimals)
    uint256 crashedWethPrice; // mocked price, set by _crashCollateralPrice()
    uint256 borrowAmount; // USDC (6 decimals) borrowed by the synthetic borrower
    uint256 debtToCover; // USDC the tests liquidate

    struct Evt {
        uint256 debtToCover;
        uint256 collateralReceived;
        uint256 swapOut;
        uint256 premium;
        uint256 surplus;
    }

    // ------------------------------------------------------------------
    // Setup
    // ------------------------------------------------------------------

    function setUp() public {
        string memory rpc = vm.envOr("BASE_ARCHIVE_RPC", string(""));
        if (bytes(rpc).length == 0) {
            console2.log(
                "SKIPPED: BASE_ARCHIVE_RPC is not set. These are Base fork tests; run with `BASE_ARCHIVE_RPC=<archive url> forge test --fork-url $BASE_ARCHIVE_RPC`."
            );
            return; // every test then calls vm.skip(true) via the requiresFork modifier
        }

        uint256 forkBlock = vm.envOr("BASE_FORK_BLOCK", uint256(DEFAULT_FORK_BLOCK));
        vm.createSelectFork(vm.envString("BASE_ARCHIVE_RPC"), forkBlock);
        forkReady = true;

        owner = makeAddr("owner");
        recipient = makeAddr("recipient");
        borrower = makeAddr("borrower");
        attacker = makeAddr("attacker");

        liq = new FlashLiquidator(IPoolAddressesProvider(POOL_ADDRESSES_PROVIDER), owner, recipient);
        vm.startPrank(owner);
        liq.setRouterAllowed(ROUTER, true);
        liq.setTokenAllowed(WETH, true);
        liq.setTokenAllowed(USDC, true);
        vm.stopPrank();

        _openPosition();
    }

    /// @dev Marks the test as skipped (not passed) when no archive RPC is available.
    modifier requiresFork() {
        if (!forkReady) {
            vm.skip(true);
        }
        _;
    }

    /// @dev Fresh address supplies WETH and borrows USDC at ~98 % of the reserve's max LTV.
    function _openPosition() internal {
        wethPrice = oracle.getAssetPrice(WETH);
        usdcPrice = oracle.getAssetPrice(USDC);
        assertGt(wethPrice, 0, "oracle WETH price");
        assertGt(usdcPrice, 0, "oracle USDC price");

        uint256 ltvBps = pool.getConfiguration(WETH).data & 0xFFFF;
        assertGt(ltvBps, 0, "WETH LTV must be > 0 at the fork block");

        // Base-currency values use 8 decimals on Base; WETH has 18, USDC has 6.
        uint256 collateralBase = (WETH_SUPPLY * wethPrice) / 1e18;
        uint256 borrowBase = (collateralBase * ltvBps * 98) / (10_000 * 100);
        borrowAmount = (borrowBase * 1e6) / usdcPrice;
        debtToCover = (borrowAmount * DEBT_SHARE_PCT) / 100;

        deal(WETH, borrower, WETH_SUPPLY);
        vm.startPrank(borrower);
        IERC20(WETH).approve(POOL, WETH_SUPPLY);
        pool.supply(WETH, WETH_SUPPLY, borrower, 0);
        pool.borrow(USDC, borrowAmount, 2, 0, borrower);
        vm.stopPrank();

        (,,,,, uint256 hf) = pool.getUserAccountData(borrower);
        assertGe(hf, 1e18, "freshly opened position must be healthy");
    }

    /// @dev Mocks the oracle so that the borrower's health factor lands at TARGET_HF (< 1).
    function _crashCollateralPrice() internal {
        (,,,,, uint256 hfBefore) = pool.getUserAccountData(borrower);
        // WETH is the only collateral, so HF scales linearly with its price.
        crashedWethPrice = (wethPrice * TARGET_HF) / hfBefore;

        vm.mockCall(
            ORACLE, abi.encodeWithSelector(IAaveOracle.getAssetPrice.selector, WETH), abi.encode(crashedWethPrice)
        );
        address[] memory assets = new address[](1);
        assets[0] = WETH;
        uint256[] memory prices = new uint256[](1);
        prices[0] = crashedWethPrice;
        vm.mockCall(ORACLE, abi.encodeWithSelector(IAaveOracle.getAssetsPrices.selector, assets), abi.encode(prices));

        (,,,,, uint256 hfAfter) = pool.getUserAccountData(borrower);
        assertLt(hfAfter, 1e18, "borrower must be liquidatable after the oracle mock");
    }

    function _params() internal view returns (FlashLiquidator.LiquidationParams memory) {
        return FlashLiquidator.LiquidationParams({
            collateralAsset: WETH,
            debtAsset: USDC,
            user: borrower,
            debtToCover: debtToCover,
            swapFeeTier: FEE_TIER_005,
            minProfit: 0,
            amountOutMinimum: 0,
            deadline: block.timestamp + 300,
            router: ROUTER
        });
    }

    /// @dev Runs `liquidate` as the owner and returns the decoded SimulatedLiquidation event.
    function _liquidateAndReadEvent(FlashLiquidator.LiquidationParams memory p) internal returns (Evt memory evt) {
        vm.recordLogs();
        vm.prank(owner);
        liq.liquidate(p);

        Vm.Log[] memory logs = vm.getRecordedLogs();
        bool found;
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].emitter == address(liq) && logs[i].topics[0] == SIMULATED_LIQUIDATION_SIG) {
                assertEq(address(uint160(uint256(logs[i].topics[1]))), borrower, "event user");
                assertEq(address(uint160(uint256(logs[i].topics[2]))), WETH, "event collateral");
                assertEq(address(uint160(uint256(logs[i].topics[3]))), USDC, "event debt");
                (evt.debtToCover, evt.collateralReceived, evt.swapOut, evt.premium, evt.surplus) =
                    abi.decode(logs[i].data, (uint256, uint256, uint256, uint256, uint256));
                found = true;
                break;
            }
        }
        assertTrue(found, "SimulatedLiquidation event not emitted");
    }

    // Aave's PercentageMath (half-up rounding, 1e4 = 100 %).
    function _percentMul(uint256 value, uint256 bps) internal pure returns (uint256) {
        return (value * bps + 5_000) / 10_000;
    }

    function _percentDiv(uint256 value, uint256 bps) internal pure returns (uint256) {
        return (value * 10_000 + bps / 2) / bps;
    }

    // ------------------------------------------------------------------
    // Happy path
    // ------------------------------------------------------------------

    /// @notice Successful flash-loan liquidation: surplus paid to the profit recipient, nothing left behind.
    function test_Liquidate_PaysSurplusToRecipient() public requiresFork {
        _crashCollateralPrice();
        uint256 recipientBefore = IERC20(USDC).balanceOf(recipient);

        Evt memory evt = _liquidateAndReadEvent(_params());

        assertEq(evt.debtToCover, debtToCover, "event debtToCover");
        assertGt(evt.surplus, 0, "surplus must be positive with the mocked price drop");
        assertEq(IERC20(USDC).balanceOf(recipient) - recipientBefore, evt.surplus, "recipient received surplus");
        assertEq(IERC20(USDC).balanceOf(address(liq)), 0, "contract keeps no debt asset");
        assertEq(IERC20(WETH).balanceOf(address(liq)), 0, "contract keeps no collateral");

        (,,,,, uint256 hfAfter) = pool.getUserAccountData(borrower);
        assertGt(hfAfter, TARGET_HF, "borrower health factor improved");
    }

    /// @notice Principal plus the actual premium is repaid to the Pool's aToken.
    function test_Liquidate_RepaysPrincipalPlusPremium() public requiresFork {
        _crashCollateralPrice();
        address aUsdc = pool.getReserveData(USDC).aTokenAddress;
        assertTrue(aUsdc != address(0), "aUSDC address");

        uint256 premiumBps = uint256(pool.FLASHLOAN_PREMIUM_TOTAL());
        uint256 expectedPremium = _percentMul(debtToCover, premiumBps);
        uint256 aTokenBefore = IERC20(USDC).balanceOf(aUsdc);

        Evt memory evt = _liquidateAndReadEvent(_params());

        assertEq(evt.premium, expectedPremium, "event premium equals amount.percentMul(FLASHLOAN_PREMIUM_TOTAL)");
        // Within the single transaction the aToken paid out `debtToCover` (flash loan), received
        // `debtToCover` (liquidation repayment) and received `debtToCover + premium` (flash repayment).
        assertEq(
            IERC20(USDC).balanceOf(aUsdc) - aTokenBefore,
            debtToCover + expectedPremium,
            "aToken net delta = liquidation repayment + premium"
        );
    }

    /// @notice Seized collateral is fully swapped: debt-asset balance increased, collateral balance zero.
    function test_Liquidate_SwapsAllCollateral() public requiresFork {
        _crashCollateralPrice();
        uint256 recipientBefore = IERC20(USDC).balanceOf(recipient);

        Evt memory evt = _liquidateAndReadEvent(_params());

        assertGt(evt.collateralReceived, 0, "collateral was seized");
        assertGt(evt.swapOut, 0, "swap produced debt asset");
        assertGt(evt.swapOut, evt.premium, "swap output exceeds the premium");
        assertEq(IERC20(WETH).balanceOf(address(liq)), 0, "no collateral left on the contract");
        assertGt(IERC20(USDC).balanceOf(recipient), recipientBefore, "debt asset increased for the recipient");
        // The flash-loan principal (debtToCover) was spent in liquidationCall and the swap brought the
        // debt asset back, so swapOut = principal + premium + surplus (startBalance was 0).
        assertEq(evt.swapOut, debtToCover + evt.premium + evt.surplus, "swapOut = debtToCover + premium + surplus");
    }

    /// @notice Collateral received equals seized-with-bonus minus Aave's liquidation protocol fee (within 1 wei).
    function test_Liquidate_ProtocolFeeAccounting() public requiresFork {
        _crashCollateralPrice();

        uint256 cfg = pool.getConfiguration(WETH).data;
        uint256 bonusBps = (cfg >> 32) & 0xFFFF; // bits 32-47
        uint256 feeBps = (cfg >> 152) & 0xFFFF; // bits 152-167
        assertGt(bonusBps, 10_000, "liquidation bonus must be > 100 %");

        // Aave LiquidationLogic._calculateAvailableCollateralToLiquidate (no e-mode).
        uint256 baseCollateral = (usdcPrice * debtToCover * 1e18) / (crashedWethPrice * 1e6);
        uint256 maxCollateral = _percentMul(baseCollateral, bonusBps);
        assertLt(maxCollateral, WETH_SUPPLY, "test must not be collateral-capped");
        uint256 bonusCollateral = maxCollateral - _percentDiv(maxCollateral, bonusBps);
        uint256 protocolFee = _percentMul(bonusCollateral, feeBps);
        uint256 expectedToLiquidator = maxCollateral - protocolFee;

        Evt memory evt = _liquidateAndReadEvent(_params());

        assertApproxEqAbs(evt.collateralReceived, expectedToLiquidator, 1, "collateral = seized - protocol fee");
        if (feeBps > 0) {
            assertGt(protocolFee, 0, "protocol fee is non-zero when configured");
            assertLt(evt.collateralReceived, maxCollateral, "liquidator receives less than the gross seizure");
        }
    }

    // ------------------------------------------------------------------
    // Revert paths
    // ------------------------------------------------------------------

    /// @notice Minimum-profit enforcement: absurd minProfit reverts with InsufficientProfit.
    function test_RevertWhen_MinProfitNotMet() public requiresFork {
        _crashCollateralPrice();
        FlashLiquidator.LiquidationParams memory p = _params();
        p.minProfit = type(uint256).max / 2;

        vm.prank(owner);
        // InsufficientProfit carries (actual, required); match on the selector only.
        vm.expectPartialRevert(FlashLiquidator.InsufficientProfit.selector);
        liq.liquidate(p);
    }

    /// @notice Without the oracle mock the borrower is healthy and Aave rejects the liquidation.
    function test_RevertWhen_UserIsHealthy() public requiresFork {
        (,,,,, uint256 hf) = pool.getUserAccountData(borrower);
        assertGe(hf, 1e18, "precondition: healthy");

        vm.prank(owner);
        vm.expectRevert(); // Aave: HEALTH_FACTOR_NOT_BELOW_THRESHOLD (string "45" or custom error by version)
        liq.liquidate(_params());
    }

    /// @notice executeOperation called by the Pool but with a foreign initiator is rejected.
    function test_RevertWhen_InitiatorIsNotThisContract() public requiresFork {
        bytes memory params = abi.encode(_params());
        vm.prank(POOL);
        vm.expectRevert(abi.encodeWithSelector(FlashLiquidator.BadInitiator.selector, attacker));
        liq.executeOperation(USDC, debtToCover, 0, attacker, params);
    }

    /// @notice executeOperation called by anyone other than the Pool is rejected.
    function test_RevertWhen_ExecuteOperationNotFromPool() public requiresFork {
        bytes memory params = abi.encode(_params());
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(FlashLiquidator.NotPool.selector, attacker));
        liq.executeOperation(USDC, debtToCover, 0, address(liq), params);
    }

    /// @notice liquidate is restricted to the owner.
    function test_RevertWhen_LiquidateCalledByNonOwner() public requiresFork {
        _crashCollateralPrice();
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(FlashLiquidator.Unauthorized.selector, attacker));
        liq.liquidate(_params());
    }

    /// @notice Collateral not on the allowlist is rejected.
    function test_RevertWhen_TokenNotAllowed() public requiresFork {
        _crashCollateralPrice();
        FlashLiquidator.LiquidationParams memory p = _params();
        p.collateralAsset = NOT_ALLOWLISTED_TOKEN;

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(FlashLiquidator.TokenNotAllowed.selector, NOT_ALLOWLISTED_TOKEN));
        liq.liquidate(p);
    }

    /// @notice A router not on the allowlist is rejected.
    function test_RevertWhen_RouterNotAllowed() public requiresFork {
        _crashCollateralPrice();
        FlashLiquidator.LiquidationParams memory p = _params();
        p.router = address(0xdead);

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(FlashLiquidator.RouterNotAllowed.selector, address(0xdead)));
        liq.liquidate(p);
    }

    /// @notice amountOutMinimum far above fair output makes the router revert (bubbled through the flash loan).
    function test_RevertWhen_SlippageTooHigh() public requiresFork {
        _crashCollateralPrice();
        FlashLiquidator.LiquidationParams memory p = _params();
        p.amountOutMinimum = type(uint256).max / 2;

        vm.prank(owner);
        vm.expectRevert(bytes("Too little received")); // Uniswap SwapRouter02 V3SwapRouter
        liq.liquidate(p);
    }

    /// @notice A deadline in the past is rejected before any flash loan is opened.
    function test_RevertWhen_DeadlineExpired() public requiresFork {
        _crashCollateralPrice();
        FlashLiquidator.LiquidationParams memory p = _params();
        p.deadline = block.timestamp - 1;

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(FlashLiquidator.Expired.selector, p.deadline, block.timestamp));
        liq.liquidate(p);
    }

    // ------------------------------------------------------------------
    // Administration
    // ------------------------------------------------------------------

    /// @notice rescueToken sweeps a stray balance to the given address; owner only.
    function test_RescueToken() public requiresFork {
        deal(WETH, address(liq), 1 ether);

        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, attacker));
        liq.rescueToken(WETH, attacker);

        vm.prank(owner);
        liq.rescueToken(WETH, recipient);
        assertEq(IERC20(WETH).balanceOf(recipient), 1 ether, "recipient got the swept balance");
        assertEq(IERC20(WETH).balanceOf(address(liq)), 0, "contract swept clean");
    }

    /// @notice Allowlist setters are owner-only and two-step ownership transfer works.
    function test_OwnerOnlySettersAndTwoStepOwnership() public requiresFork {
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, attacker));
        liq.setTokenAllowed(NOT_ALLOWLISTED_TOKEN, true);

        address newOwner = makeAddr("newOwner");
        vm.prank(owner);
        liq.transferOwnership(newOwner);
        assertEq(liq.owner(), owner, "ownership unchanged until accepted");
        assertEq(liq.pendingOwner(), newOwner, "pending owner recorded");
        vm.prank(newOwner);
        liq.acceptOwnership();
        assertEq(liq.owner(), newOwner, "ownership accepted");
    }

    /// @notice Immutables are read from the addresses provider.
    function test_ConstructorWiresPoolFromProvider() public requiresFork {
        assertEq(address(liq.POOL()), POOL, "POOL from provider");
        assertEq(address(liq.ADDRESSES_PROVIDER()), POOL_ADDRESSES_PROVIDER, "ADDRESSES_PROVIDER stored");
        assertEq(liq.profitRecipient(), recipient, "profit recipient");
        assertEq(liq.owner(), owner, "owner");
    }
}
