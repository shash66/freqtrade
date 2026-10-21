"""Complete transaction fee accounting for L2s (spec §6, deployment-readiness item 2).

Base (OP stack): total fee = L2 execution (gasUsed * effectiveGasPrice) + L1 data fee. The L1 data fee is
charged by the GasPriceOracle predeploy on the serialized transaction; it is *estimated* with
GasPriceOracle.getL1Fee(serialized unsigned tx) and *verified* from the receipt field `l1Fee`.

Arbitrum: the L1 component is folded into gas units. NodeInterface.gasEstimateL1Component returns the extra
L2 gas units that pay for L1 posting; the receipt field `gasUsedForL1` gives the verified figure.

Every result states its source: `estimated` (pre-trade quote), `verified` (from a mined receipt) or
`unknown` (could not be obtained; never replaced by a number)."""
from __future__ import annotations

from dataclasses import dataclass, field

import rlp
from web3 import Web3

from app.chains.rpc import RpcClient

OP_GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F"
ARB_NODE_INTERFACE = "0x00000000000000000000000000000000000000C8"
OP_STACK_CHAINS = {8453, 10}
ARBITRUM_CHAINS = {42161}

GAS_PRICE_ORACLE_ABI = [
    {"type": "function", "name": "getL1Fee", "stateMutability": "view", "inputs": [{"name": "_data", "type": "bytes"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "getL1GasUsed", "stateMutability": "view", "inputs": [{"name": "_data", "type": "bytes"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]
NODE_INTERFACE_ABI = [
    {"type": "function", "name": "gasEstimateL1Component", "stateMutability": "payable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "contractCreation", "type": "bool"}, {"name": "data", "type": "bytes"}],
     "outputs": [{"name": "gasEstimateForL1", "type": "uint64"}, {"name": "baseFee", "type": "uint256"}, {"name": "l1BaseFeeEstimate", "type": "uint256"}]},
]


@dataclass
class FeeAccounting:
    l2_execution_wei: int | None
    l1_data_wei: int | None
    source: str                 # estimated | verified | unknown | not_applicable
    method: str                 # how the L1 figure was obtained
    notes: list[str] = field(default_factory=list)

    @property
    def total_wei(self) -> int | None:
        if self.l2_execution_wei is None:
            return None
        if self.l1_data_wei is None:
            return None if self.source != "not_applicable" else self.l2_execution_wei
        return self.l2_execution_wei + self.l1_data_wei

    def as_dict(self) -> dict:
        return {"l2_execution_wei": None if self.l2_execution_wei is None else str(self.l2_execution_wei),
                "l1_data_wei": None if self.l1_data_wei is None else str(self.l1_data_wei),
                "total_wei": None if self.total_wei is None else str(self.total_wei),
                "source": self.source, "method": self.method, "notes": self.notes}


def serialize_unsigned_1559(chain_id: int, to: str, data: bytes, gas: int, max_fee_wei: int, nonce: int = 1,
                            priority_fee_wei: int = 1, value: int = 0) -> bytes:
    """EIP-1559 transaction bytes with placeholder signature, the same shape the OP oracle prices on-chain."""
    r = (1 << 255) - 1
    s = (1 << 254) - 1
    fields = [chain_id, nonce, priority_fee_wei, max_fee_wei, gas, bytes.fromhex(Web3.to_checksum_address(to)[2:]), value, data, [], 1, r, s]
    return b"\x02" + rlp.encode(fields)


def estimate_l1_fee(chain_id: int, client: RpcClient, to: str, data: bytes, gas: int, max_fee_wei: int, block="latest") -> FeeAccounting:
    """Pre-trade L1 data fee estimate for a transaction calling `to` with `data`."""
    w3 = client.w3
    try:
        if chain_id in OP_STACK_CHAINS:
            oracle = w3.eth.contract(address=Web3.to_checksum_address(OP_GAS_PRICE_ORACLE), abi=GAS_PRICE_ORACLE_ABI)
            raw = serialize_unsigned_1559(chain_id, to, data, gas, max_fee_wei)
            fee = int(client.call(oracle.functions.getL1Fee(raw).call, block_identifier=block))
            return FeeAccounting(None, fee, "estimated", "op_gas_price_oracle.getL1Fee(serialized_tx)",
                                 ["L1 fee depends on the L1 base fee and blob fee at inclusion time; quote is for the current block"])
        if chain_id in ARBITRUM_CHAINS:
            ni = w3.eth.contract(address=Web3.to_checksum_address(ARB_NODE_INTERFACE), abi=NODE_INTERFACE_ABI)
            l1_gas, base_fee, _l1_base = client.call(
                ni.functions.gasEstimateL1Component(Web3.to_checksum_address(to), False, data).call, block_identifier=block)
            return FeeAccounting(None, int(l1_gas) * int(base_fee), "estimated", "arbitrum_node_interface.gasEstimateL1Component * baseFee",
                                 [f"L1 component = {int(l1_gas)} L2 gas units at base fee {int(base_fee)} wei"])
        return FeeAccounting(None, 0, "not_applicable", "chain has no separate L1 data fee")
    except Exception as e:  # no number is invented: the caller sees `unknown`
        return FeeAccounting(None, None, "unknown", "estimate failed", [f"{type(e).__name__}: {str(e)[:120]}"])


def verified_fees_from_receipt(chain_id: int, receipt: dict) -> FeeAccounting:
    """Fees actually paid by a mined transaction, from receipt fields exposed by the chain's node."""
    gas_used = _int(receipt.get("gasUsed"))
    price = _int(receipt.get("effectiveGasPrice"))
    l2 = gas_used * price if gas_used is not None and price is not None else None
    if chain_id in OP_STACK_CHAINS:
        l1 = _int(receipt.get("l1Fee"))
        if l1 is None:
            return FeeAccounting(l2, None, "unknown", "receipt lacks l1Fee (node not OP-stack aware)")
        return FeeAccounting(l2, l1, "verified", "receipt.l1Fee")
    if chain_id in ARBITRUM_CHAINS:
        l1_gas = _int(receipt.get("gasUsedForL1"))
        if l1_gas is None or price is None:
            return FeeAccounting(l2, None, "unknown", "receipt lacks gasUsedForL1")
        # on Arbitrum gasUsed already includes the L1 component; report it separately without double counting
        return FeeAccounting(l2 - l1_gas * price if l2 is not None else None, l1_gas * price, "verified", "receipt.gasUsedForL1 * effectiveGasPrice")
    return FeeAccounting(l2, 0, "not_applicable", "chain has no separate L1 data fee")


def _int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, str):
        return int(v, 16) if v.startswith("0x") else int(v)
    return int(v)
