// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/*//////////////////////////////////////////////////////////////////////////////
//  CometLiquidator fork tests (Base, Compound V3 USDC market).               //
//                                                                            //
//  Mechanism tests, not economic evidence: the borrower is synthetic (set   //
//  up here) and the WETH price feed that Comet reads is mocked downwards so //
//  the account becomes liquidatable; Uniswap still trades at the real price //
//  so the surplus is NOT a realistic profit figure. What the tests prove is //
//  that flashLoanSimple -> absorb -> buyCollateral -> swap -> repay runs in  //
//  one transaction, respects the profit floor and settles with Aave.        //
//                                                                            //
//  REQUIRES BASE_ARCHIVE_RPC (archive endpoint). Run:                        //
//      BASE_FORK_BLOCK=52900000 forge test --match-contract CometLiquidator  //
//          --fork-url $BASE_ARCHIVE_RPC -vvv                                 //
//  Without BASE_ARCHIVE_RPC every test is SKIPPED (vm.skip), never passed.   //
//////////////////////////////////////////////////////////////////////////////*/

import {Test, console2} from "forge-std/Test.sol";
import {Vm} from "forge-std/Vm.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {CometLiquidator} from "../src/CometLiquidator.sol";

interface ICometTest {
    struct AssetInfo {
        uint8 offset;
        address asset;
        address priceFeed;
        uint64 scale;
        uint64 borrowCollateralFactor;
        uint64 liquidateCollateralFactor;
        uint64 liquidationFactor;
        uint128 supplyCap;
    }

    function baseToken() external view returns (address);
    function baseTokenPriceFeed() external view returns (address);
    function supply(address asset, uint256 amount) external;
    function withdraw(address asset, uint256 amount) external;
    function isLiquidatable(address account) external view returns (bool);
    function borrowBalanceOf(address account) external view returns (uint256);
    function collateralBalanceOf(address account, address asset) external view returns (uint128);
    function getAssetInfoByAddress(address asset) external view returns (AssetInfo memory);
    function getPrice(address priceFeed) external view returns (uint256);
    function quoteCollateral(address asset, uint256 baseAmount) external view returns (uint256);
    function getCollateralReserves(address asset) external view returns (uint256);
    function baseBorrowMin() external view returns (uint256);
}

contract CometLiquidatorForkTest is Test {
    address constant POOL_ADDRESSES_PROVIDER = 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D; // Aave V3 Base
    address constant COMET_USDC = 0xb125E6687d4313864e53df431d5425969c15Eb2F;              // Compound V3 Base USDC
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant ROUTER = 0x2626664c2603336E57B271c5C0b26F421741e481;                  // Uniswap v3 SwapRouter02
    uint256 constant DEFAULT_FORK_BLOCK = 52_900_000;
    uint256 constant WETH_SUPPLY = 1 ether;

    bytes32 constant EVENT_SIG =
        keccak256("SimulatedCometLiquidation(address,address,uint256,uint256,uint256,uint256,uint256,uint256)");

    ICometTest comet = ICometTest(COMET_USDC);
    CometLiquidator liq;
    address owner;
    address recipient;
    address borrower;
    bool forkReady;
    address wethFeed;
    uint256 realWethPrice;
    uint256 borrowAmount;

    struct Evt {
        bool seen;
        uint256 absorbed;
        uint256 baseSpent;
        uint256 bought;
        uint256 swapOut;
        uint256 premium;
        uint256 surplus;
    }

    function _findEvent(Vm.Log[] memory logs) internal pure returns (Evt memory e) {
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].topics[0] == EVENT_SIG) {
                e.seen = true;
                (e.absorbed, e.baseSpent, e.bought, e.swapOut, e.premium, e.surplus) =
                    abi.decode(logs[i].data, (uint256, uint256, uint256, uint256, uint256, uint256));
            }
        }
    }

    modifier requiresFork() {
        if (!forkReady) {
            vm.skip(true);
        }
        _;
    }

    function setUp() public {
        string memory rpc = vm.envOr("BASE_ARCHIVE_RPC", string(""));
        if (bytes(rpc).length == 0) {
            console2.log("SKIPPED: BASE_ARCHIVE_RPC is not set (Base fork tests).");
            return;
        }
        vm.createSelectFork(rpc, vm.envOr("BASE_FORK_BLOCK", uint256(DEFAULT_FORK_BLOCK)));
        forkReady = true;

        owner = makeAddr("owner");
        recipient = makeAddr("recipient");
        borrower = makeAddr("borrower");

        liq = new CometLiquidator(POOL_ADDRESSES_PROVIDER, owner, recipient);
        vm.startPrank(owner);
        liq.setRouterAllowed(ROUTER, true);
        liq.setTokenAllowed(WETH, true);
        liq.setTokenAllowed(USDC, true);
        liq.setCometAllowed(COMET_USDC, true);
        vm.stopPrank();

        // Synthetic borrower: supply 1 WETH, borrow USDC up to ~90 % of the borrow collateral factor.
        ICometTest.AssetInfo memory info = comet.getAssetInfoByAddress(WETH);
        wethFeed = info.priceFeed;
        realWethPrice = comet.getPrice(wethFeed); // 8 decimals
        uint256 capacityUsd8 = WETH_SUPPLY * realWethPrice / 1e18 * info.borrowCollateralFactor / 1e18; // 8 dp
        borrowAmount = capacityUsd8 * 90 / 100 / 1e2; // USDC has 6 decimals
        require(borrowAmount >= comet.baseBorrowMin(), "borrow below baseBorrowMin");
        deal(WETH, borrower, WETH_SUPPLY);
        vm.startPrank(borrower);
        IERC20(WETH).approve(COMET_USDC, WETH_SUPPLY);
        comet.supply(WETH, WETH_SUPPLY);
        comet.withdraw(USDC, borrowAmount);
        vm.stopPrank();
        assertFalse(comet.isLiquidatable(borrower), "healthy before the price mock");
    }

    /// @dev Mock the WETH feed Comet reads so the borrower becomes liquidatable (price halved).
    function _crashWeth() internal returns (uint256 mocked) {
        mocked = realWethPrice / 2;
        vm.mockCall(
            wethFeed,
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(1), int256(mocked), block.timestamp, block.timestamp, uint80(1))
        );
        assertTrue(comet.isLiquidatable(borrower), "liquidatable after the price mock");
    }

    function _params(uint256 baseAmount, uint256 minProfit) internal view returns (CometLiquidator.Params memory p) {
        address[] memory accounts = new address[](1);
        accounts[0] = borrower;
        p = CometLiquidator.Params({
            comet: COMET_USDC,
            collateralAsset: WETH,
            accounts: accounts,
            baseAmount: baseAmount,
            minCollateral: 0,
            swapFeeTier: 500,
            amountOutMinimum: 0,
            minProfit: minProfit,
            deadline: block.timestamp + 600,
            router: ROUTER
        });
    }

    /// @dev Base needed to buy the borrower's whole collateral at the discounted quote (binary search on quoteCollateral).
    function _baseFor(uint256 collateral) internal view returns (uint256 lo) {
        lo = 1;
        uint256 hi = 1e15; // 1e9 USDC
        for (uint256 i = 0; i < 64; i++) {
            uint256 mid = (lo + hi) / 2;
            if (comet.quoteCollateral(WETH, mid) <= collateral) lo = mid;
            else hi = mid;
        }
    }

    function test_AbsorbBuySwapRepay_RunsAtomically() public requiresFork {
        _crashWeth();
        uint256 reservesBefore = comet.getCollateralReserves(WETH);
        uint256 collateral = reservesBefore + comet.collateralBalanceOf(borrower, WETH);
        uint256 baseAmount = _baseFor(collateral);
        assertGt(baseAmount, 1e6, "the quote is for a real amount of USDC");

        vm.recordLogs();
        vm.prank(owner);
        liq.liquidate(_params(baseAmount, 0));

        Evt memory e = _findEvent(vm.getRecordedLogs());
        assertTrue(e.seen, "SimulatedCometLiquidation emitted");
        assertEq(e.absorbed, 1, "one account absorbed");
        assertEq(comet.borrowBalanceOf(borrower), 0, "the absorbed account has no debt left");
        assertEq(comet.collateralBalanceOf(borrower, WETH), 0, "the absorbed account has no collateral left");
        assertGt(e.bought, 0, "collateral bought from reserves");
        assertApproxEqAbs(e.premium, baseAmount * 5 / 10_000, 1, "Aave flash premium is 5 bps (rounded up by the Pool)");
        assertEq(e.baseSpent, baseAmount, "all of the principal went into buyCollateral");
        assertEq(IERC20(USDC).balanceOf(recipient), e.surplus, "surplus forwarded to the recipient");
        assertEq(IERC20(USDC).balanceOf(address(liq)), 0, "nothing left in the receiver");
        assertEq(IERC20(WETH).balanceOf(address(liq)), 0, "all collateral was swapped");
        assertEq(e.swapOut, e.surplus + baseAmount + e.premium, "swap output = principal + premium + surplus");
        // The surplus is large only because the DEX trades at the real price while Comet sells at the mocked one.
        console2.log("baseSpent / bought / swapOut", e.baseSpent, e.bought, e.swapOut);
        console2.log("premium / surplus", e.premium, e.surplus);
    }

    function test_RevertsBelowMinProfit() public requiresFork {
        _crashWeth();
        uint256 collateral = comet.getCollateralReserves(WETH) + comet.collateralBalanceOf(borrower, WETH);
        uint256 baseAmount = _baseFor(collateral);
        vm.prank(owner);
        vm.expectPartialRevert(CometLiquidator.InsufficientProfit.selector);
        liq.liquidate(_params(baseAmount, type(uint128).max));
        assertGt(comet.borrowBalanceOf(borrower), 0, "nothing happened: the whole transaction reverted");
    }

    function test_RevertsWhenNotLiquidatable() public requiresFork {
        // No price mock: Comet's absorb reverts (NotLiquidatable) and the revert bubbles through the flash loan.
        vm.prank(owner);
        vm.expectRevert();
        liq.liquidate(_params(1_000e6, 0));
    }

    function test_RejectsUnauthorizedCallerAndUnknownComet() public requiresFork {
        address stranger = makeAddr("stranger");
        vm.prank(stranger);
        vm.expectPartialRevert(CometLiquidator.Unauthorized.selector);
        liq.liquidate(_params(1_000e6, 0));

        vm.prank(owner);
        liq.setCometAllowed(COMET_USDC, false);
        vm.prank(owner);
        vm.expectPartialRevert(CometLiquidator.CometNotAllowed.selector);
        liq.liquidate(_params(1_000e6, 0));
    }
}
