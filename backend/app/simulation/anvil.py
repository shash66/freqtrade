"""Controller for the private Anvil service: fork resets, impersonation, funding, calls and traces.
Anvil is only reachable inside the compose network; this module never talks to a public chain for writes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from app.core.logging import get_logger

log = get_logger(__name__)


class AnvilUnavailable(RuntimeError):
    pass


@dataclass
class CallSummary:
    to: str | None
    selector: str | None
    gas_used: int | None
    error: str | None
    depth: int


class AnvilFork:
    def __init__(self, url: str, timeout: float = 120.0):
        self.url = url
        self.w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    def rpc(self, method: str, params: list | None = None) -> Any:
        resp = self.w3.provider.make_request(method, params or [])
        if "error" in resp:
            raise AnvilUnavailable(f"{method}: {resp['error']}")
        return resp.get("result")

    def health(self) -> dict:
        try:
            return {"ok": True, "chain_id": self.w3.eth.chain_id, "block": self.w3.eth.block_number}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    # --- fork lifecycle -------------------------------------------------------------------------
    def reset_fork(self, fork_url: str, block_number: int | None) -> dict:
        forking: dict = {"jsonRpcUrl": fork_url}
        if block_number is not None:
            forking["blockNumber"] = block_number
        self.rpc("anvil_reset", [{"forking": forking}])
        self.rpc("anvil_setBlockTimestampInterval", [1]) if False else None
        info = {"chain_id": self.w3.eth.chain_id, "block": self.w3.eth.block_number}
        blk = self.w3.eth.get_block("latest")
        info["block_hash"] = blk["hash"].hex() if hasattr(blk["hash"], "hex") else str(blk["hash"])
        if not info["block_hash"].startswith("0x"):
            info["block_hash"] = "0x" + info["block_hash"]
        return info

    def snapshot(self) -> str:
        return self.rpc("evm_snapshot")

    def revert(self, snap_id: str) -> bool:
        return bool(self.rpc("evm_revert", [snap_id]))

    def mine(self, n: int = 1) -> None:
        self.rpc("anvil_mine", [hex(n)])

    # --- accounts -------------------------------------------------------------------------------
    def accounts(self) -> list[str]:
        return self.w3.eth.accounts

    def impersonate(self, addr: str) -> None:
        self.rpc("anvil_impersonateAccount", [Web3.to_checksum_address(addr)])

    def stop_impersonate(self, addr: str) -> None:
        self.rpc("anvil_stopImpersonatingAccount", [Web3.to_checksum_address(addr)])

    def set_balance(self, addr: str, wei: int) -> None:
        self.rpc("anvil_setBalance", [Web3.to_checksum_address(addr), hex(wei)])

    def erc20(self, token: str):
        from app.chains.validation import load_abi
        abi = load_abi("erc20") + [{"type": "function", "name": "transfer", "stateMutability": "nonpayable",
                                    "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
                                    "outputs": [{"name": "", "type": "bool"}]},
                                   {"type": "function", "name": "approve", "stateMutability": "nonpayable",
                                    "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
                                    "outputs": [{"name": "", "type": "bool"}]}]
        return self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=abi)

    def fund_erc20_from(self, token: str, source: str, to: str, amount: int) -> dict:
        """Move `amount` of `token` from an impersonated holder (e.g. the Aave aToken, which holds the reserve
        liquidity) to `to`. Returns the receipt. Fails loudly if the source does not hold enough."""
        t = self.erc20(token)
        bal = t.functions.balanceOf(Web3.to_checksum_address(source)).call()
        if bal < amount:
            raise AnvilUnavailable(f"funding source {source} holds {bal} < {amount} of {token}")
        self.set_balance(source, 10**18)
        self.impersonate(source)
        try:
            tx = t.functions.transfer(Web3.to_checksum_address(to), amount).transact({"from": Web3.to_checksum_address(source)})
            rc = self.w3.eth.wait_for_transaction_receipt(tx, timeout=120)
        finally:
            self.stop_impersonate(source)
        if rc["status"] != 1:
            raise AnvilUnavailable("funding transfer reverted")
        return dict(rc)

    # --- execution ------------------------------------------------------------------------------
    def send(self, from_addr: str, to: str, data: bytes | str, gas: int | None = None, value: int = 0) -> dict:
        tx = {"from": Web3.to_checksum_address(from_addr), "to": Web3.to_checksum_address(to), "data": data, "value": value}
        if gas:
            tx["gas"] = gas
        try:
            tx["gas"] = tx.get("gas") or int(self.w3.eth.estimate_gas(tx) * 1.2)
        except Exception as e:  # estimate fails when the call would revert: return the revert instead of raising
            return {"status": 0, "revert_reason": _revert_reason(e), "gasUsed": None, "logs": [], "transactionHash": None}
        h = self.w3.eth.send_transaction(tx)
        rc = self.w3.eth.wait_for_transaction_receipt(h, timeout=120)
        out = dict(rc)
        out["transactionHash"] = h.hex() if hasattr(h, "hex") else str(h)
        if rc["status"] != 1:
            out["revert_reason"] = self._replay_revert(tx)
        return out

    def _replay_revert(self, tx: dict) -> str | None:
        try:
            self.w3.eth.call(tx)
        except Exception as e:
            return _revert_reason(e)
        return None

    def trace(self, tx_hash: str, max_calls: int = 40) -> list[CallSummary]:
        try:
            res = self.rpc("debug_traceTransaction", [tx_hash, {"tracer": "callTracer"}])
        except AnvilUnavailable as e:
            log.warning("trace_unavailable", error=str(e)[:120])
            return []
        out: list[CallSummary] = []

        def walk(node: dict, depth: int):
            if len(out) >= max_calls:
                return
            inp = node.get("input") or ""
            out.append(CallSummary(to=node.get("to"), selector=inp[:10] if len(inp) >= 10 else None,
                                   gas_used=int(node["gasUsed"], 16) if isinstance(node.get("gasUsed"), str) else node.get("gasUsed"),
                                   error=node.get("error") or node.get("revertReason"), depth=depth))
            for c in node.get("calls", []) or []:
                walk(c, depth + 1)

        if isinstance(res, dict):
            walk(res, 0)
        return out


def _revert_reason(e: Exception) -> str:
    s = str(e)
    return s[:300]
