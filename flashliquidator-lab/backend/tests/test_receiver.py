from web3 import Web3

from app.simulation.receiver import SWAP_ROUTER_02, load_artifact


def test_packaged_artifact_loads_and_encodes_liquidate():
    art = load_artifact()
    assert art["bytecode"].startswith("0x") and len(art["bytecode"]) > 1000
    w3 = Web3()
    c = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    names = {e.get("name") for e in art["abi"]}
    assert {"liquidate", "executeOperation", "setTokenAllowed", "setRouterAllowed", "SimulatedLiquidation"} <= names
    assert not any(e.get("name") in ("execute", "call", "multicall") for e in art["abi"]), "no arbitrary-call surface"
    params = ("0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40, 10**6, 500, 0, 990_000, 1_900_000_000, SWAP_ROUTER_02[8453])
    data = c.functions.liquidate(tuple(Web3.to_checksum_address(x) if isinstance(x, str) else x for x in params))._encode_transaction_data()
    assert data.startswith("0x") and len(data) == 2 + 8 + 9 * 64
