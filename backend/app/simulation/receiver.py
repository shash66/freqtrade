"""Deploy and drive the real FlashLiquidator receiver on an Anvil fork.

The artifact is produced by scripts/compile-contracts.sh (solc-js) or `forge build` and installed at
app/simulation/artifacts/FlashLiquidator.json. On the fork the receiver takes an actual Aave
flashLoanSimple, repays the borrower, swaps the collateral through Uniswap SwapRouter02 and repays
principal + premium; the SimulatedLiquidation event carries the measured figures."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from importlib import resources

from web3 import Web3
from web3.logs import DISCARD

from app.simulation.anvil import AnvilFork

SWAP_ROUTER_02 = {8453: "0x2626664c2603336E57B271c5C0b26F421741e481", 42161: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"}


class ReceiverArtifactMissing(RuntimeError):
    pass


def load_artifact() -> dict:
    """Prefer an explicit path, then forge output, then the packaged solc-js artifact."""
    cands = [os.environ.get("FLASH_RECEIVER_ARTIFACT"), "contracts/out/FlashLiquidator.sol/FlashLiquidator.json"]
    for p in cands:
        if p and os.path.exists(p):
            with open(p) as f:
                raw = json.load(f)
            bc = raw.get("bytecode")
            if isinstance(bc, dict):
                bc = bc.get("object")
            return {"abi": raw["abi"], "bytecode": bc, "source": p}
    try:
        with resources.files("app.simulation.artifacts").joinpath("FlashLiquidator.json").open() as f:
            raw = json.load(f)
        return {"abi": raw["abi"], "bytecode": raw["bytecode"], "source": "packaged solc-js artifact (" + raw.get("compiler", "?") + ")"}
    except FileNotFoundError as e:
        raise ReceiverArtifactMissing("FlashLiquidator artifact not found; run scripts/compile-contracts.sh") from e


@dataclass
class ReceiverRun:
    receiver_address: str
    tx_hash: str | None
    status: int
    gas_used: int | None
    revert_reason: str | None
    event: dict | None       # SimulatedLiquidation args
    calldata: str
    artifact_source: str


def deploy_receiver(anvil: AnvilFork, deployer: str, provider: str) -> tuple[str, object]:
    art = load_artifact()
    w3 = anvil.w3
    factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    tx = factory.constructor(Web3.to_checksum_address(provider), Web3.to_checksum_address(deployer),
                             Web3.to_checksum_address(deployer)).transact({"from": deployer})
    rc = w3.eth.wait_for_transaction_receipt(tx, timeout=120)
    if rc["status"] != 1 or not rc.get("contractAddress"):
        raise RuntimeError("receiver deployment reverted on the fork")
    return rc["contractAddress"], w3.eth.contract(address=rc["contractAddress"], abi=art["abi"])


def run_receiver_liquidation(anvil: AnvilFork, owner: str, provider: str, chain_id: int, collateral: str, debt: str, user: str,
                             debt_to_cover: int, fee_tier: int, amount_out_min: int, min_profit: int = 0) -> ReceiverRun:
    art = load_artifact()
    router = SWAP_ROUTER_02.get(chain_id)
    if router is None or len(anvil.w3.eth.get_code(Web3.to_checksum_address(router))) == 0:
        raise RuntimeError(f"Uniswap SwapRouter02 not found on chain {chain_id} at {router}")
    addr, c = deploy_receiver(anvil, owner, provider)
    for fn in (c.functions.setRouterAllowed(Web3.to_checksum_address(router), True),
               c.functions.setTokenAllowed(Web3.to_checksum_address(collateral), True),
               c.functions.setTokenAllowed(Web3.to_checksum_address(debt), True)):
        rc = anvil.w3.eth.wait_for_transaction_receipt(fn.transact({"from": owner}), timeout=120)
        if rc["status"] != 1:
            raise RuntimeError("receiver configuration reverted")
    params = (Web3.to_checksum_address(collateral), Web3.to_checksum_address(debt), Web3.to_checksum_address(user), int(debt_to_cover),
              int(fee_tier), int(min_profit), int(amount_out_min), int(time.time()) + 3600, Web3.to_checksum_address(router))
    fn = c.functions.liquidate(params)
    calldata = fn._encode_transaction_data()
    rc = anvil.send(owner, addr, calldata)
    event = None
    if rc.get("status") == 1:
        try:
            logs = c.events.SimulatedLiquidation().process_receipt(rc, errors=DISCARD)
            if logs:
                event = {k: (str(v) if isinstance(v, int) else v) for k, v in dict(logs[0]["args"]).items()}
        except Exception:  # event parsing never masks the measured result
            event = None
    return ReceiverRun(receiver_address=addr, tx_hash=rc.get("transactionHash"), status=int(rc.get("status", 0)), gas_used=rc.get("gasUsed"),
                       revert_reason=rc.get("revert_reason"), event=event, calldata=calldata, artifact_source=art["source"])
