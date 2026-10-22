"""Aave V3 adapter: wraps the existing reader, protocol math and indexer so Aave markets are served through the
same interface as any other protocol. Math: aave-v3-origin LiquidationLogic.sol via app.strategies.profitability."""
from __future__ import annotations

from decimal import Decimal

from web3 import Web3

from app.aave.address_book import MARKETS, AaveMarket
from app.aave.address_book_extra import EXTRA_MARKETS
from app.aave.reads import AaveReader
from app.chains.config import NetworkConfig
from app.chains.rpc import RpcClient
from app.chains.validation import load_abi
from app.core.money import WAD, quantize_usd
from app.protocols.base import HistoricalEvent, LiquidationValue, MarketSpec, PositionView, Verification
from app.strategies import profitability as pf

BASE_UNIT = 10**8


def aave_specs() -> dict[str, MarketSpec]:
    out = {}
    for slug, mk in {**MARKETS, **EXTRA_MARKETS}.items():
        out[f"aave_v3_{slug}"] = MarketSpec("aave_v3", f"aave_v3_{slug}", slug.split("_")[0], mk.chain_id, mk.market_name, mk.pool, mk.oracle,
                                            "@bgd-labs/aave-address-book 4.44.22", {"addresses_provider": mk.addresses_provider, "data_provider": mk.data_provider})
    return out


class AaveV3Adapter:
    def __init__(self, spec: MarketSpec, market: AaveMarket, net: NetworkConfig, client: RpcClient):
        self.spec, self.market, self.net, self.client = spec, market, net, client
        self.reader = AaveReader(net, client)

    def verify(self) -> Verification:
        v = Verification(self.spec.slug, "failed")
        try:
            cid = int(self.client.call(lambda: self.client.w3.eth.chain_id))
            v.chain_id_ok = cid == self.spec.chain_id
            v.bytecode_ok = all(len(self.client.call(self.client.w3.eth.get_code, Web3.to_checksum_address(a))) > 0
                                for a in (self.market.pool, self.market.oracle, self.market.data_provider))
            provider = self.client.call(self.reader.pool.functions.ADDRESSES_PROVIDER().call)
            v.round_trip_ok = provider.lower() == self.market.addresses_provider.lower()
            v.head_block = int(self.client.call(lambda: self.client.w3.eth.block_number))
            total, _ = self.reader.flashloan_premium_bps()
            v.detail = {"addresses_provider": provider, "flashloan_premium_total_bps": total, "reserves": len(self.reader.reserves_list())}
            v.status = "verified" if v.chain_id_ok and v.bytecode_ok and v.round_trip_ok else "failed"
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            v.status = "blocked" if any(k in msg for k in ("Connection", "Max retries", "403", "Name or service", "timed out")) else "failed"
            v.reason = f"{type(e).__name__}: {msg[:160]}"
        return v

    def position(self, account: str, block="latest") -> PositionView:
        try:
            a = self.reader.user_account_data(account, block)
            hf = Decimal(a.health_factor_wad) / Decimal(WAD) if a.health_factor_wad < 2**256 - 1 else Decimal("Infinity")
            return PositionView(Web3.to_checksum_address(account), block, a.total_debt_base > 0 and a.health_factor_wad < WAD, str(hf),
                                quantize_usd(Decimal(a.total_debt_base) / BASE_UNIT), quantize_usd(Decimal(a.total_collateral_base) / BASE_UNIT),
                                {"metric": "Aave health factor", "liquidation_threshold_bps": a.current_liquidation_threshold_bps})
        except Exception as e:  # noqa: BLE001
            return PositionView(account, block, None, None, None, None, {}, [f"read failed: {str(e)[:120]}"])

    def value_liquidation(self, pos: PositionView, debt_asset: str | None, collateral_asset: str | None) -> LiquidationValue:
        """Gross bonus at oracle prices for the largest permitted repayment (LiquidationLogic rules), outcome class theoretical."""
        if not debt_asset or not collateral_asset:
            return LiquidationValue(None, None, None, "liquidationCall: collateral at bonus", incomplete=["debt and collateral reserves required"])
        try:
            block = pos.block
            coll, dbt = Web3.to_checksum_address(collateral_asset), Web3.to_checksum_address(debt_asset)
            acct = self.reader.user_account_data(pos.account, block)
            cc, cd = self.reader.reserve_config(coll, block), self.reader.reserve_config(dbt, block)
            urc, urd = self.reader.user_reserve_data(coll, pos.account, block), self.reader.user_reserve_data(dbt, pos.account, block)
            prices = self.reader.asset_prices([coll, dbt], block)
            emode_bonus = None
            cat = self.reader.user_emode(pos.account, block)
            if cat:
                emode_bonus = self.reader.emode_collateral_bonus(cat, coll, block)
            p = pf.PositionInputs(acct.health_factor_wad, acct.total_collateral_base, acct.total_debt_base,
                                  pf.ReserveParams(dbt, "", cd.decimals, prices[dbt], cd.liquidation_bonus_bps, cd.liquidation_protocol_fee_bps, cd.liquidation_threshold_bps, cd.active, cd.paused),
                                  pf.ReserveParams(coll, "", cc.decimals, prices[coll], cc.liquidation_bonus_bps, cc.liquidation_protocol_fee_bps, cc.liquidation_threshold_bps, cc.active, cc.paused),
                                  urd.variable_debt + urd.stable_debt, urc.a_token_balance, urc.usage_as_collateral, emode_bonus)
            rules = pf.LiquidationRules()
            elig = pf.check_eligibility(p, rules)
            if not elig.eligible:
                return LiquidationValue(None, None, None, "liquidationCall: collateral at bonus", incomplete=elig.reasons)
            outcome, problems = pf.choose_debt_to_cover(p, rules, elig, None)
            repay = pf.raw_to_usd(outcome.debt_to_cover_raw, cd.decimals, prices[dbt])
            seize = pf.raw_to_usd(outcome.collateral_to_liquidator_raw, cc.decimals, prices[coll])
            notes = list(outcome.assumptions) + problems
            if cat and emode_bonus is None:
                notes.append(f"user in e-mode {cat}; category bonus unreadable, reserve bonus used")
            return LiquidationValue(quantize_usd(repay), quantize_usd(seize), quantize_usd(seize - repay), "liquidationCall: collateral at bonus", notes)
        except Exception as e:  # noqa: BLE001
            return LiquidationValue(None, None, None, "liquidationCall: collateral at bonus", incomplete=[f"valuation failed: {str(e)[:100]}"])

    def historical_liquidations(self, from_block: int, to_block: int) -> list[HistoricalEvent]:
        pool = self.client.w3.eth.contract(address=Web3.to_checksum_address(self.market.pool), abi=load_abi("pool"))
        out = []
        for lg in self.client.call(pool.events.LiquidationCall().get_logs, from_block=from_block, to_block=to_block):
            h = lg["transactionHash"].hex() if hasattr(lg["transactionHash"], "hex") else str(lg["transactionHash"])
            a = lg["args"]
            out.append(HistoricalEvent(h if h.startswith("0x") else "0x" + h, int(lg["logIndex"]), int(lg["blockNumber"]), a["liquidator"], a["user"], a["debtAsset"],
                                       a["collateralAsset"], int(a["debtToCover"]), int(a["liquidatedCollateralAmount"]), "LiquidationCall"))
        return out
