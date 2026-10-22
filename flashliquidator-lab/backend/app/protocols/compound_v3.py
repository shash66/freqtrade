"""Compound V3 (Comet) adapter: read-only, Base and Arbitrum deployments.

Mechanism (Comet.sol, compound-finance/comet): an underwater account is `absorb`ed by anyone; the protocol
takes the account's collateral and repays its base-token debt from reserves. The absorber is not paid by
`absorb` itself. Value accrues to whoever then calls `buyCollateral(asset, minAmount, baseAmount, recipient)`,
paying base token for the protocol's seized collateral at the discounted quote

    quoteCollateral(asset, baseAmount):
        discountFactor      = storeFrontPriceFactor × (1 − liquidationFactor(asset))
        assetPriceDiscounted = price(asset) × (1 − discountFactor)
        collateral           = baseAmount × price(base) / assetPriceDiscounted

so the liquidator's gross bonus on a purchase of value V (at oracle prices) is V × discountFactor / (1 − discountFactor).
Eligibility is `isLiquidatable(account)` (borrow balance above the liquidation-collateral-factor-weighted
collateral). The historical record is the `AbsorbCollateral`/`AbsorbDebt` events (absorb) and `BuyCollateral`
(the paid leg). Everything here is a contract read or an event; prices come from the Comet's own `getPrice`.

Not implemented (reported incomplete): a flash-loan execution path and swap valuation for Comet; the gas model.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from web3 import Web3

from app.chains.rpc import RpcClient
from app.core.money import quantize_usd
from app.protocols.base import HistoricalEvent, LiquidationValue, MarketSpec, PositionView, Verification

COMET_ABI = [
    {"name": "baseToken", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
    {"name": "baseTokenPriceFeed", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
    {"name": "numAssets", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint8"}]},
    {"name": "storeFrontPriceFactor", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint64"}]},
    {"name": "baseScale", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint64"}]},
    {"name": "isLiquidatable", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}], "outputs": [{"type": "bool"}]},
    {"name": "borrowBalanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "collateralBalanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "uint128"}]},
    {"name": "getPrice", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "getAssetInfo", "type": "function", "stateMutability": "view", "inputs": [{"type": "uint8"}],
     "outputs": [{"type": "tuple", "components": [{"name": "offset", "type": "uint8"}, {"name": "asset", "type": "address"}, {"name": "priceFeed", "type": "address"},
                                                  {"name": "scale", "type": "uint64"}, {"name": "borrowCollateralFactor", "type": "uint64"},
                                                  {"name": "liquidateCollateralFactor", "type": "uint64"}, {"name": "liquidationFactor", "type": "uint64"},
                                                  {"name": "supplyCap", "type": "uint128"}]}]},
    {"name": "getCollateralReserves", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "AbsorbDebt", "type": "event", "anonymous": False, "inputs": [{"indexed": True, "name": "absorber", "type": "address"}, {"indexed": True, "name": "borrower", "type": "address"},
                                                                           {"indexed": False, "name": "basePaidOut", "type": "uint256"}, {"indexed": False, "name": "usdValue", "type": "uint256"}]},
    {"name": "AbsorbCollateral", "type": "event", "anonymous": False, "inputs": [{"indexed": True, "name": "absorber", "type": "address"}, {"indexed": True, "name": "borrower", "type": "address"},
                                                                                 {"indexed": True, "name": "asset", "type": "address"}, {"indexed": False, "name": "collateralAbsorbed", "type": "uint256"},
                                                                                 {"indexed": False, "name": "usdValue", "type": "uint256"}]},
    {"name": "BuyCollateral", "type": "event", "anonymous": False, "inputs": [{"indexed": True, "name": "buyer", "type": "address"}, {"indexed": True, "name": "asset", "type": "address"},
                                                                              {"indexed": False, "name": "baseAmount", "type": "uint256"}, {"indexed": False, "name": "collateralAmount", "type": "uint256"}]},
]
FACTOR_SCALE = 10**18
PRICE_SCALE = 10**8

# Comet deployments (compound.finance docs, "Comet addresses"); verified live before use.
COMET_MARKETS: dict[str, MarketSpec] = {
    "compound_v3_base_usdc": MarketSpec("compound_v3", "compound_v3_base_usdc", "base", 8453, "Compound V3 Base USDC", "0xb125E6687d4313864e53df431d5425969c15Eb2F", None, "docs.compound.finance Comet addresses"),
    "compound_v3_base_weth": MarketSpec("compound_v3", "compound_v3_base_weth", "base", 8453, "Compound V3 Base WETH", "0x46e6b214b524310239732D51387075E0e70970bf", None, "docs.compound.finance Comet addresses"),
    "compound_v3_base_usdbc": MarketSpec("compound_v3", "compound_v3_base_usdbc", "base", 8453, "Compound V3 Base USDbC", "0x9c4ec768c28520B50860ea7a15bd7213a9fF58bf", None, "docs.compound.finance Comet addresses"),
    "compound_v3_arbitrum_usdc": MarketSpec("compound_v3", "compound_v3_arbitrum_usdc", "arbitrum", 42161, "Compound V3 Arbitrum USDC", "0x9c4ec768c28520B50860ea7a15bd7213a9fF58bf", None, "docs.compound.finance Comet addresses"),
}


@dataclass
class CometAsset:
    asset: str
    price_feed: str
    scale: int
    borrow_cf: int
    liquidate_cf: int
    liquidation_factor: int


def quote_collateral_bonus_bps(store_front_price_factor: int, liquidation_factor: int) -> int:
    """Gross bonus (bps of the purchase value at oracle prices) a buyer of seized collateral receives, from Comet.quoteCollateral."""
    discount = store_front_price_factor * (FACTOR_SCALE - liquidation_factor) // FACTOR_SCALE
    if discount >= FACTOR_SCALE:
        return 0
    return int(Decimal(discount) * 10_000 / Decimal(FACTOR_SCALE - discount))


class CompoundV3Adapter:
    def __init__(self, spec: MarketSpec, client: RpcClient):
        self.spec, self.client, self.w3 = spec, client, client.w3
        self.comet = self.w3.eth.contract(address=Web3.to_checksum_address(spec.primary_address), abi=COMET_ABI)

    def verify(self) -> Verification:
        v = Verification(self.spec.slug, "failed")
        try:
            cid = int(self.client.call(lambda: self.w3.eth.chain_id))
            v.chain_id_ok = cid == self.spec.chain_id
            code = self.client.call(self.w3.eth.get_code, self.comet.address)
            v.bytecode_ok = len(code) > 0
            base = self.client.call(self.comet.functions.baseToken().call)
            n = int(self.client.call(self.comet.functions.numAssets().call))
            sf = int(self.client.call(self.comet.functions.storeFrontPriceFactor().call))
            v.round_trip_ok = bool(base) and n > 0
            v.head_block = int(self.client.call(lambda: self.w3.eth.block_number))
            v.detail = {"base_token": base, "num_assets": n, "store_front_price_factor": sf}
            v.status = "verified" if v.chain_id_ok and v.bytecode_ok and v.round_trip_ok else "failed"
        except Exception as e:  # noqa: BLE001
            v.status, v.reason = "blocked" if "Connection" in str(e) or "Max retries" in str(e) or "403" in str(e) else "failed", f"{type(e).__name__}: {str(e)[:160]}"
        return v

    def assets(self, block="latest") -> list[CometAsset]:
        n = int(self.client.call(self.comet.functions.numAssets().call, block_identifier=block))
        out = []
        for i in range(n):
            a = self.client.call(self.comet.functions.getAssetInfo(i).call, block_identifier=block)
            out.append(CometAsset(a[1], a[2], int(a[3]), int(a[4]), int(a[5]), int(a[6])))
        return out

    def position(self, account: str, block="latest") -> PositionView:
        acct = Web3.to_checksum_address(account)
        inc: list[str] = []
        try:
            liq = bool(self.client.call(self.comet.functions.isLiquidatable(acct).call, block_identifier=block))
            borrow = int(self.client.call(self.comet.functions.borrowBalanceOf(acct).call, block_identifier=block))
            base_scale = int(self.client.call(self.comet.functions.baseScale().call, block_identifier=block))
            base_feed = self.client.call(self.comet.functions.baseTokenPriceFeed().call, block_identifier=block)
            base_px = int(self.client.call(self.comet.functions.getPrice(base_feed).call, block_identifier=block))
            debt_usd = Decimal(borrow) * Decimal(base_px) / (Decimal(base_scale) * Decimal(PRICE_SCALE))
            coll_usd = Decimal(0)
            limit_usd = Decimal(0)
            detail = {"collateral": []}
            for a in self.assets(block):
                bal = int(self.client.call(self.comet.functions.collateralBalanceOf(acct, a.asset).call, block_identifier=block))
                if bal == 0:
                    continue
                px = int(self.client.call(self.comet.functions.getPrice(a.price_feed).call, block_identifier=block))
                val = Decimal(bal) * Decimal(px) / (Decimal(a.scale) * Decimal(PRICE_SCALE))
                coll_usd += val
                limit_usd += val * Decimal(a.liquidate_cf) / Decimal(FACTOR_SCALE)
                detail["collateral"].append({"asset": a.asset, "balance_raw": bal, "usd": str(quantize_usd(val)), "liquidate_cf": a.liquidate_cf, "liquidation_factor": a.liquidation_factor})
            metric = str((limit_usd / debt_usd).quantize(Decimal("0.000001"))) if debt_usd else None   # >1 healthy, <1 liquidatable (mirrors isLiquidatable)
            detail["metric"] = "liquidation-collateral-factor-weighted collateral / borrow (Comet isLiquidatable rule)"
            return PositionView(acct, block, liq, metric, quantize_usd(debt_usd), quantize_usd(coll_usd), detail, inc)
        except Exception as e:  # noqa: BLE001
            return PositionView(acct, block, None, None, None, None, {}, [f"read failed: {str(e)[:120]}"])

    def value_liquidation(self, pos: PositionView, debt_asset: str | None, collateral_asset: str | None) -> LiquidationValue:
        """Value of buying the account's seized collateral after absorb, at Comet's discounted quote (theoretical)."""
        try:
            sf = int(self.client.call(self.comet.functions.storeFrontPriceFactor().call, block_identifier=pos.block))
            rows = pos.detail.get("collateral", [])
            if not rows:
                return LiquidationValue(None, None, None, "absorb + buyCollateral at discount", incomplete=["no collateral balances read"])
            target = next((r for r in rows if collateral_asset and r["asset"].lower() == collateral_asset.lower()), rows[0])
            bonus_bps = quote_collateral_bonus_bps(sf, int(target["liquidation_factor"]))
            seize_usd = Decimal(target["usd"])
            repay_usd = seize_usd * Decimal(10_000) / Decimal(10_000 + bonus_bps)
            return LiquidationValue(quantize_usd(repay_usd), quantize_usd(seize_usd), quantize_usd(seize_usd - repay_usd), "absorb + buyCollateral at discount",
                                    assumptions=[f"whole seized {target['asset']} balance bought at the storefront discount ({bonus_bps} bps gross bonus); the absorb itself pays nothing",
                                                 "buyer competes for the protocol's collateral reserves after any absorb; availability is not modeled here"])
        except Exception as e:  # noqa: BLE001
            return LiquidationValue(None, None, None, "absorb + buyCollateral at discount", incomplete=[f"valuation failed: {str(e)[:100]}"])

    def historical_liquidations(self, from_block: int, to_block: int) -> list[HistoricalEvent]:
        out = []
        ev = self.comet.events.AbsorbCollateral()
        for lg in self.client.call(ev.get_logs, from_block=from_block, to_block=to_block):
            h = lg["transactionHash"].hex() if hasattr(lg["transactionHash"], "hex") else str(lg["transactionHash"])
            out.append(HistoricalEvent(h if h.startswith("0x") else "0x" + h, int(lg["logIndex"]), int(lg["blockNumber"]), lg["args"]["absorber"], lg["args"]["borrower"],
                                       None, lg["args"]["asset"], None, int(lg["args"]["collateralAbsorbed"]), "AbsorbCollateral"))
        return out
