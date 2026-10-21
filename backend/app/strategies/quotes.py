"""On-chain DEX quotes (Uniswap v3 QuoterV2 via eth_call). No API keys; needs a working RPC.
Addresses from Uniswap's published deployments (docs.uniswap.org/contracts/v3/reference/deployments);
verified at use by a bytecode check. A failed or missing quote is reported as `incomplete`, never estimated."""
from __future__ import annotations

from dataclasses import dataclass

from web3 import Web3

from app.chains.rpc import RpcClient
from app.chains.validation import load_abi
from app.core.money import BPS

QUOTER_V2 = {8453: "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a", 42161: "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"}
FEE_TIERS = (100, 500, 3000, 10000)


@dataclass
class Quote:
    status: str            # ok | incomplete | error
    amount_out_raw: int | None
    fee_tier: int | None
    gas_estimate: int | None
    price_impact_bps: int | None
    source: str
    error: str | None = None


def price_impact_bps(amount_in_raw: int, in_dec: int, in_price: int, amount_out_raw: int, out_dec: int, out_price: int) -> int:
    """Shortfall of the quoted output versus the oracle-implied fair value, in bps (>= 0 is worse than fair)."""
    fair_out = amount_in_raw * in_price * 10**out_dec // (out_price * 10**in_dec)
    if fair_out == 0:
        return 0
    return int((fair_out - amount_out_raw) * BPS // fair_out)


class UniswapV3Quoter:
    def __init__(self, chain_id: int, client: RpcClient):
        self.chain_id, self.client = chain_id, client
        self.address = QUOTER_V2.get(chain_id)
        self._verified: bool | None = None

    def _contract(self):
        w3 = self.client.w3
        return w3.eth.contract(address=w3.to_checksum_address(self.address), abi=load_abi("uniswap_v3_quoter_v2"))

    def verify(self) -> bool:
        if self._verified is None:
            try:
                self._verified = self.address is not None and len(self.client.call(self.client.w3.eth.get_code, self.address)) > 0
            except Exception:
                self._verified = False
        return self._verified

    def quote_exact_in(self, token_in: str, token_out: str, amount_in_raw: int, block="latest") -> Quote:
        src = "uniswap_v3_quoter_v2"
        if not self.address:
            return Quote("incomplete", None, None, None, None, src, "no quoter known for this chain")
        if not self.verify():
            return Quote("incomplete", None, None, None, None, src, "quoter contract not found at expected address")
        c = self._contract()
        best: Quote | None = None
        errors = []
        for fee in FEE_TIERS:
            try:
                out, _sqrt, _ticks, gas = self.client.call(
                    c.functions.quoteExactInputSingle((Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out),
                                                        amount_in_raw, fee, 0)).call, block_identifier=block)
                q = Quote("ok", int(out), fee, int(gas), None, src)
                if best is None or q.amount_out_raw > best.amount_out_raw:
                    best = q
            except Exception as e:  # pool for this tier may not exist; that is expected
                errors.append(f"fee {fee}: {str(e)[:60]}")
        if best is None:
            return Quote("incomplete", None, None, None, None, src, "; ".join(errors)[:400])
        return best
