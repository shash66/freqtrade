"""Typed, exact reads of Aave V3 state. Everything returns ints in base units; nothing is a float.
The reserve configuration bitmap layout follows aave-v3-origin ReserveConfiguration.sol."""
from __future__ import annotations

from dataclasses import dataclass

from web3 import Web3

from app.chains.config import NetworkConfig
from app.chains.rpc import RpcClient
from app.chains.validation import load_abi


def _bits(data: int, start: int, width: int) -> int:
    return (data >> start) & ((1 << width) - 1)


@dataclass(frozen=True)
class ReserveConfig:
    ltv_bps: int
    liquidation_threshold_bps: int
    liquidation_bonus_bps: int  # e.g. 10500 == 5% bonus
    decimals: int
    active: bool
    frozen: bool
    borrowing_enabled: bool
    paused: bool
    borrowable_in_isolation: bool
    siloed_borrowing: bool
    flashloan_enabled: bool
    reserve_factor_bps: int
    borrow_cap: int
    supply_cap: int
    liquidation_protocol_fee_bps: int
    emode_category_legacy: int  # bits 168-175; deprecated in v3.3+ (e-modes became a bitmap elsewhere)
    unbacked_mint_cap: int
    debt_ceiling: int
    virtual_accounting_active: bool

    @classmethod
    def from_bitmap(cls, data: int) -> ReserveConfig:
        return cls(
            ltv_bps=_bits(data, 0, 16),
            liquidation_threshold_bps=_bits(data, 16, 16),
            liquidation_bonus_bps=_bits(data, 32, 16),
            decimals=_bits(data, 48, 8),
            active=bool(_bits(data, 56, 1)),
            frozen=bool(_bits(data, 57, 1)),
            borrowing_enabled=bool(_bits(data, 58, 1)),
            paused=bool(_bits(data, 60, 1)),
            borrowable_in_isolation=bool(_bits(data, 61, 1)),
            siloed_borrowing=bool(_bits(data, 62, 1)),
            flashloan_enabled=bool(_bits(data, 63, 1)),
            reserve_factor_bps=_bits(data, 64, 16),
            borrow_cap=_bits(data, 80, 36),
            supply_cap=_bits(data, 116, 36),
            liquidation_protocol_fee_bps=_bits(data, 152, 16),
            emode_category_legacy=_bits(data, 168, 8),
            unbacked_mint_cap=_bits(data, 176, 36),
            debt_ceiling=_bits(data, 212, 40),
            virtual_accounting_active=bool(_bits(data, 252, 1)),
        )


@dataclass(frozen=True)
class UserAccountData:
    total_collateral_base: int
    total_debt_base: int
    available_borrows_base: int
    current_liquidation_threshold_bps: int
    ltv_bps: int
    health_factor_wad: int


@dataclass(frozen=True)
class UserReserveData:
    a_token_balance: int
    stable_debt: int
    variable_debt: int
    usage_as_collateral: bool


class AaveReader:
    def __init__(self, net: NetworkConfig, client: RpcClient):
        self.net = net
        self.client = client
        w3 = client.w3
        self.w3 = w3
        self.pool = w3.eth.contract(address=w3.to_checksum_address(net.market.pool), abi=load_abi("pool"))
        self.oracle = w3.eth.contract(address=w3.to_checksum_address(net.market.oracle), abi=load_abi("oracle"))
        self.data_provider = w3.eth.contract(address=w3.to_checksum_address(net.market.data_provider),
                                             abi=load_abi("data_provider"))

    def _call(self, fn, block: int | str = "latest"):
        return self.client.call(fn.call, block_identifier=block)

    def reserves_list(self, block="latest") -> list[str]:
        return [Web3.to_checksum_address(a) for a in self._call(self.pool.functions.getReservesList(), block)]

    def reserve_config(self, asset: str, block="latest") -> ReserveConfig:
        data = self._call(self.pool.functions.getConfiguration(Web3.to_checksum_address(asset)), block)
        raw = data[0] if isinstance(data, (tuple, list)) else data
        return ReserveConfig.from_bitmap(int(raw))

    def reserve_tokens(self, asset: str, block="latest") -> tuple[str, str, str]:
        a, s, v = self._call(self.data_provider.functions.getReserveTokensAddresses(Web3.to_checksum_address(asset)), block)
        return a, s, v

    def reserve_emode_category(self, asset: str, block="latest") -> int | None:
        try:
            return int(self._call(self.data_provider.functions.getReserveEModeCategory(Web3.to_checksum_address(asset)), block))
        except Exception:
            return None  # removed in newer data providers; caller records "unknown"

    def user_account_data(self, user: str, block="latest") -> UserAccountData:
        r = self._call(self.pool.functions.getUserAccountData(Web3.to_checksum_address(user)), block)
        return UserAccountData(int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4]), int(r[5]))

    def user_emode(self, user: str, block="latest") -> int:
        return int(self._call(self.pool.functions.getUserEMode(Web3.to_checksum_address(user)), block))

    def user_reserve_data(self, asset: str, user: str, block="latest") -> UserReserveData:
        r = self._call(self.data_provider.functions.getUserReserveData(Web3.to_checksum_address(asset),
                                                                        Web3.to_checksum_address(user)), block)
        return UserReserveData(int(r[0]), int(r[1]), int(r[2]), bool(r[8]))

    def emode_collateral_bonus(self, category: int, collateral: str, block="latest") -> int | None:
        """Liquidation bonus that applies to `collateral` for a user in e-mode `category`, or None if the
        collateral is not in the category. v3.3 exposes per-category collateral config + a collateral bitmap;
        older pools expose getEModeCategoryData with a single bonus for all reserves in the category."""
        c = Web3.to_checksum_address(collateral)
        try:
            cfg = self._call(self.pool.functions.getEModeCategoryCollateralConfig(category), block)
            bitmap = int(self._call(self.pool.functions.getEModeCategoryCollateralBitmap(category), block))
            reserves = self.reserves_list(block)
            idx = reserves.index(c)
            return int(cfg[2]) if (bitmap >> idx) & 1 else None
        except ValueError:
            return None  # collateral not in reserves list
        except Exception:
            data = self._call(self.pool.functions.getEModeCategoryData(category), block)  # legacy (<= v3.2)
            return int(data[2]) if int(data[2]) > 0 else None

    def asset_prices(self, assets: list[str], block="latest") -> dict[str, int]:
        cs = [Web3.to_checksum_address(a) for a in assets]
        prices = self._call(self.oracle.functions.getAssetsPrices(cs), block)
        return {a: int(p) for a, p in zip(cs, prices, strict=False)}

    def base_currency_unit(self, block="latest") -> int:
        return int(self._call(self.oracle.functions.BASE_CURRENCY_UNIT(), block))

    def flashloan_premium_bps(self, block="latest") -> tuple[int, int]:
        total = int(self._call(self.pool.functions.FLASHLOAN_PREMIUM_TOTAL(), block))
        to_protocol = int(self._call(self.pool.functions.FLASHLOAN_PREMIUM_TO_PROTOCOL(), block))
        return total, to_protocol
