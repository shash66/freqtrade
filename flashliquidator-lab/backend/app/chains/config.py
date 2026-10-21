"""Network definitions. RPC URLs come only from settings (never from the client)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.aave.address_book import MARKETS, AaveMarket
from app.core.config import Settings


@dataclass(frozen=True)
class GasModel:
    """Chain-specific execution-cost parameters. L2s add an L1 data component."""
    base_gas_units_liquidation: int  # typical flash-loan + liquidation + swap gas, refined from history
    l1_data_fee_applies: bool
    priority_fee_floor_wei: int


@dataclass(frozen=True)
class NetworkConfig:
    slug: str
    chain_id: int
    name: str
    native_symbol: str
    explorer_url: str
    block_time_seconds: Decimal
    rpc_http: str
    rpc_ws: str
    archive_rpc: str
    enabled: bool
    market: AaveMarket
    gas: GasModel
    indexer_start_block: int | None

    @property
    def has_archive_rpc(self) -> bool:
        return bool(self.archive_rpc)


def build_networks(s: Settings) -> dict[str, NetworkConfig]:
    nets: dict[str, NetworkConfig] = {}
    defs = [
        ("base", 8453, "Base", "ETH", "https://basescan.org", Decimal(2), True,
         GasModel(900_000, True, 1_000_000), s.indexer_start_block_base),
        ("arbitrum", 42161, "Arbitrum One", "ETH", "https://arbiscan.io", Decimal("0.25"), True,
         GasModel(1_500_000, True, 10_000_000), s.indexer_start_block_arbitrum),
    ]
    for slug, cid, name, sym, exp, bt, enabled, gas, start in defs:
        http, ws, arch = s.rpc_for(slug)
        nets[slug] = NetworkConfig(slug, cid, name, sym, exp, bt, http, ws, arch, enabled, MARKETS[slug], gas, start)
    # Ethereum mainnet: adapter exists, disabled unless configured (spec §2.1); no Aave market wired yet.
    return nets
