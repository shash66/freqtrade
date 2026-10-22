"""Network definitions. RPC URLs come only from settings (never from the client)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

from app.aave.address_book import MARKETS
from app.aave.address_book_extra import EXTRA_MARKETS, AaveMarket
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
    # Additional Aave V3 markets (Phase 4): endpoints come from <SLUG>_RPC_HTTP / <SLUG>_ARCHIVE_RPC environment
    # variables (e.g. ETHEREUM_RPC_HTTP, OPTIMISM_ARCHIVE_RPC). A market with no endpoint is registered disabled so the
    # registry can list it and report it as not configured; nothing is assumed about an unreachable chain.
    for slug, (cid, name, sym, exp, bt, gas) in EXTRA_NETWORKS.items():
        for mslug, mk in EXTRA_MARKETS.items():
            if mk.chain_id != cid:
                continue
            http = os.environ.get(f"{slug.upper()}_RPC_HTTP", "") or (s.ethereum_rpc_http if slug == "ethereum" else "")
            arch = os.environ.get(f"{slug.upper()}_ARCHIVE_RPC", "") or (s.ethereum_archive_rpc if slug == "ethereum" else "")
            nets[mslug] = NetworkConfig(mslug, cid, f"{name} ({mk.market_name})" if mslug != slug else name, sym, exp, bt, http, "", arch, bool(http or arch), mk, gas, None)
    return nets


# chain id, name, native symbol, explorer, block time, gas model. L1 data fee applies on OP-stack chains.
EXTRA_NETWORKS: dict[str, tuple] = {
    "ethereum": (1, "Ethereum", "ETH", "https://etherscan.io", Decimal(12), GasModel(900_000, False, 1_000_000_000)),
    "optimism": (10, "OP Mainnet", "ETH", "https://optimistic.etherscan.io", Decimal(2), GasModel(900_000, True, 1_000_000)),
    "polygon": (137, "Polygon PoS", "POL", "https://polygonscan.com", Decimal(2), GasModel(900_000, False, 30_000_000_000)),
    "avalanche": (43114, "Avalanche C-Chain", "AVAX", "https://snowtrace.io", Decimal(2), GasModel(900_000, False, 1_000_000_000)),
    "scroll": (534352, "Scroll", "ETH", "https://scrollscan.com", Decimal(3), GasModel(900_000, True, 1_000_000)),
    "bnb": (56, "BNB Chain", "BNB", "https://bscscan.com", Decimal("0.75"), GasModel(900_000, False, 1_000_000_000)),
    "gnosis": (100, "Gnosis", "xDAI", "https://gnosisscan.io", Decimal(5), GasModel(900_000, False, 1_000_000_000)),
    "linea": (59144, "Linea", "ETH", "https://lineascan.build", Decimal(2), GasModel(900_000, False, 1_000_000)),
    "sonic": (146, "Sonic", "S", "https://sonicscan.org", Decimal(1), GasModel(900_000, False, 1_000_000_000)),
    "celo": (42220, "Celo", "CELO", "https://celoscan.io", Decimal(1), GasModel(900_000, False, 1_000_000_000)),
    "soneium": (1868, "Soneium", "ETH", "https://soneium.blockscout.com", Decimal(2), GasModel(900_000, True, 1_000_000)),
    "plasma": (9745, "Plasma", "XPL", "https://plasmascan.to", Decimal(1), GasModel(900_000, False, 1_000_000_000)),
}
