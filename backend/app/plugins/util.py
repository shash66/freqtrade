"""Small helpers shared by the strategy plugins."""
from __future__ import annotations

from app.plugins.base import Context

BLOCKS_PER_DAY = {"base": 43_200, "arbitrum": 345_600, "ethereum": 7_200, "optimism": 43_200, "polygon": 39_000}


def chain_gas_price_wei(ctx: Context) -> tuple[int, str]:
    """Gas price to value a fork run at. A fork's own `eth_gasPrice` is Anvil's default (≈1 gwei), not the chain's,
    so the price comes from the real node: `eth_gasPrice` at the head for live runs, the pinned block's base fee
    plus a 10 % tip for historical runs. Returns (wei, source)."""
    if ctx.block == "latest":
        return int(ctx.client.call(lambda: ctx.client.w3.eth.gas_price)), "live eth_gasPrice"
    arch = ctx.archive_client or ctx.client
    blk = arch.call(lambda: arch.w3.eth.get_block(int(ctx.block)))
    base_fee = int(blk.get("baseFeePerGas") or 0)
    if base_fee <= 0:
        return int(arch.call(lambda: arch.w3.eth.gas_price)), "archive eth_gasPrice (no base fee on the block)"
    return base_fee + base_fee // 10, f"base fee of block {int(ctx.block)} + 10 % tip"


def blocks_for_hours(slug: str, hours: float) -> int:
    return max(1, int(BLOCKS_PER_DAY.get(slug, 43_200) * hours / 24))
