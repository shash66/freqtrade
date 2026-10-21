// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/*//////////////////////////////////////////////////////////////////////////////
//  Deploy FlashLiquidator to a LOCAL ANVIL FORK ONLY.                        //
//                                                                            //
//  Research tool; fork-simulation only; no production audit.                 //
//                                                                            //
//  Usage (two terminals):                                                    //
//    anvil --fork-url $BASE_ARCHIVE_RPC --fork-block-number 30000000         //
//    ANVIL_RPC=http://127.0.0.1:8545 FORK_SIMULATION_ONLY=true \             //
//      forge script script/Deploy.s.sol:Deploy --rpc-url $ANVIL_RPC \        //
//      --broadcast --unlocked                                                //
//                                                                            //
//  Safety:                                                                   //
//    * ANVIL_RPC must be set (the script refuses to run otherwise).          //
//    * If the connected chain reports chainid 1, 8453 or 42161 (an Anvil     //
//      fork keeps the forked chain id by default), FORK_SIMULATION_ONLY=true //
//      must be set explicitly or the script reverts.                         //
//    * No private key is ever read. Transactions are sent via                //
//      vm.startBroadcast(address) impersonating Anvil's default account #0,  //
//      which only works against a node that has that account unlocked.      //
//////////////////////////////////////////////////////////////////////////////*/

import {Script, console2} from "forge-std/Script.sol";

import {FlashLiquidator} from "../src/FlashLiquidator.sol";
import {IPoolAddressesProvider} from "../src/interfaces/IPoolAddressesProvider.sol";

/// @title Deploy
/// @notice Deploys and configures FlashLiquidator on a local Anvil fork.
contract Deploy is Script {
    /// @dev Anvil's default funded account #0 (public, well-known; its key is NOT used here).
    address constant ANVIL_ACCOUNT_0 = 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266;

    // Defaults: Aave V3 Base market and Uniswap v3 SwapRouter02 on Base. Override via env.
    address constant DEFAULT_PROVIDER = 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address constant DEFAULT_ROUTER = 0x2626664c2603336E57B271c5C0b26F421741e481;
    address constant DEFAULT_WETH = 0x4200000000000000000000000000000000000006;
    address constant DEFAULT_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;

    /// @notice ANVIL_RPC is not set; this script only targets a local Anvil node.
    error AnvilRpcNotSet();
    /// @notice The connected chain id belongs to a public network and FORK_SIMULATION_ONLY=true was not set.
    error ProductionChainRefused(uint256 chainId);

    /// @notice Deploys FlashLiquidator, allowlists the router and the WETH/USDC pair.
    /// @return liq The deployed contract.
    function run() external returns (FlashLiquidator liq) {
        string memory anvilRpc = vm.envOr("ANVIL_RPC", string(""));
        if (bytes(anvilRpc).length == 0) revert AnvilRpcNotSet();

        uint256 chainId = block.chainid;
        if (chainId == 1 || chainId == 8453 || chainId == 42161) {
            if (!vm.envOr("FORK_SIMULATION_ONLY", false)) revert ProductionChainRefused(chainId);
            console2.log("FORK_SIMULATION_ONLY=true: treating chain id as a local fork", chainId);
        }

        address provider = vm.envOr("AAVE_POOL_ADDRESSES_PROVIDER", DEFAULT_PROVIDER);
        address router = vm.envOr("SWAP_ROUTER02", DEFAULT_ROUTER);
        address weth = vm.envOr("WETH", DEFAULT_WETH);
        address usdc = vm.envOr("USDC", DEFAULT_USDC);
        address deployer = vm.envOr("ANVIL_DEPLOYER", ANVIL_ACCOUNT_0);
        address profitRecipient = vm.envOr("PROFIT_RECIPIENT", deployer);

        console2.log("ANVIL_RPC:        ", anvilRpc);
        console2.log("chain id:         ", chainId);
        console2.log("deployer (unlocked):", deployer);
        console2.log("addresses provider:", provider);

        // Impersonation: no key material is loaded; the node must have `deployer` unlocked.
        vm.startBroadcast(deployer);
        liq = new FlashLiquidator(IPoolAddressesProvider(provider), deployer, profitRecipient);
        liq.setRouterAllowed(router, true);
        liq.setTokenAllowed(weth, true);
        liq.setTokenAllowed(usdc, true);
        vm.stopBroadcast();

        console2.log("FlashLiquidator deployed at:", address(liq));
        console2.log("POOL:", address(liq.POOL()));
    }
}
