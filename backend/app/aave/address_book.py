"""Aave V3 deployment addresses.

Source: npm package @bgd-labs/aave-address-book, version pinned below (the official BGD Labs address book,
the same data published at github.com/bgd-labs/aave-address-book). Values were copied from
src/AaveV3Base.sol and src/AaveV3Arbitrum.sol of that package. They are re-verified on startup
(chain id, non-empty bytecode, ADDRESSES_PROVIDER() round-trip) before any data is trusted.

Do not edit by hand. Regenerate with scripts/update-address-book.sh.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ADDRESS_BOOK_VERSION = "4.44.22"


@dataclass(frozen=True)
class ReserveInfo:
    symbol: str
    address: str
    decimals: int


@dataclass(frozen=True)
class AaveMarket:
    chain_id: int
    market_name: str
    pool: str
    pool_impl: str
    addresses_provider: str
    oracle: str
    data_provider: str
    ui_pool_data_provider: str
    reserves: tuple[ReserveInfo, ...] = field(default_factory=tuple)


AAVE_V3_BASE = AaveMarket(
    chain_id=8453,
    market_name="AaveV3Base",
    pool="0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    pool_impl="0xDb578D67A83E94DE73c9e0C14280f804F6C1c3e4",
    addresses_provider="0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D",
    oracle="0x2Cc0Fc26eD4563A5ce5e8bdcfe1A2878676Ae156",
    data_provider="0x0F43731EB8d45A581f4a36DD74F5f358bc90C73A",
    ui_pool_data_provider="0xb84A20e848baE3e13897934bB4e74E2225f4546B",
    reserves=(
        ReserveInfo("WETH", "0x4200000000000000000000000000000000000006", 18),
        ReserveInfo("cbETH", "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", 18),
        ReserveInfo("USDbC", "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", 6),
        ReserveInfo("wstETH", "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452", 18),
        ReserveInfo("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        ReserveInfo("weETH", "0x04C0599Ae5A44757c0af6F9eC3b93da8976c150A", 18),
        ReserveInfo("cbBTC", "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", 8),
    ),
)

AAVE_V3_ARBITRUM = AaveMarket(
    chain_id=42161,
    market_name="AaveV3Arbitrum",
    pool="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    pool_impl="0xb76c1a8da369FC39AAdCF39D2446828BcDF6Ee56",
    addresses_provider="0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb",
    oracle="0xb56c2F0B653B2e0b10C9b928C8580Ac5Df02C7C7",
    data_provider="0x243Aa95cAC2a25651eda86e80bEe66114413c43b",
    ui_pool_data_provider="0x13c833256BD767da2320d727a3691BAff3770E39",
    reserves=(
        ReserveInfo("DAI", "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1", 18),
        ReserveInfo("USDC", "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8", 6),  # bridged USDC.e in address book naming
        ReserveInfo("WBTC", "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", 8),
        ReserveInfo("WETH", "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18),
        ReserveInfo("USDT", "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6),
        ReserveInfo("wstETH", "0x5979D7b546E38E414F7E9822514be443A4800529", 18),
        ReserveInfo("ARB", "0x912CE59144191C1204E64559FE8253a0e49E6548", 18),
        ReserveInfo("weETH", "0x35751007a407ca6FEFfE80b3cB397736D2cf4dbe", 18),
    ),
)

MARKETS: dict[str, AaveMarket] = {"base": AAVE_V3_BASE, "arbitrum": AAVE_V3_ARBITRUM}
