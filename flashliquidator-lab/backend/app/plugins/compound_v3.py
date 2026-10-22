"""Strategy A: Compound V3 (Comet) liquidations and discounted collateral purchases (Phase 5, §3).

Mechanism, from compound-finance/comet `Comet.sol` (not Aave's formulas):
- An account is liquidatable when its borrow exceeds the sum of collateral × liquidateCollateralFactor
  (`isLiquidatable`). Anyone may call `absorb(absorber, accounts)`: the protocol seizes all of the account's
  collateral into its own reserves and repays the borrow from reserves. The absorber is not paid.
- The protocol then sells seized collateral through `buyCollateral(asset, minAmount, baseAmount, recipient)`
  at `quoteCollateral`: price × (1 − storeFrontPriceFactor × (1 − liquidationFactor)). The buyer's gross bonus is
  the discount; what is for sale is `getCollateralReserves(asset)`, which absorbs increase.
- Hence two opportunity kinds: `absorb_and_buy` (absorb a liquidatable account, then buy what it added to the
  reserves) and `reserve_purchase` (buy collateral that is already in the reserves). Both are flash-financeable
  in base token: flashLoanSimple(base) → absorb → buyCollateral → swap collateral→base → repay (CometLiquidator.sol).

Everything measured comes from contract reads, events or the private fork; costs follow the project's fee
accounting; unknowns stay None. Competition is modeled from the recorded absorbers' bids.
"""
from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from web3 import Web3
from web3.logs import DISCARD

from app.chains.l1fees import estimate_l1_fee
from app.competition.bidmodel import p_win_priority_auction
from app.core.money import quantize_usd
from app.plugins.base import (
    CompetitionEstimate,
    Context,
    CostBreakdown,
    Opportunity,
    ReplayReport,
    ShadowObservation,
    SimulationOutcome,
    StrategyConfig,
)
from app.plugins.registry import config_for, register
from app.plugins.util import blocks_for_hours, chain_gas_price_wei
from app.protocols.compound_v3 import (
    COMET_ABI,
    COMET_MARKETS,
    PRICE_SCALE,
    CompoundV3Adapter,
    quote_collateral_bonus_bps,
)
from app.simulation.anvil import AnvilFork
from app.simulation.fast_path import MULTICALL3, MULTICALL3_ABI
from app.simulation.receiver import SWAP_ROUTER_02, load_artifact_named
from app.strategies.quotes import UniswapV3Quoter

EXTRA_ABI = [
    {"name": "quoteCollateral", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}, {"type": "uint256"}], "outputs": [{"type": "uint256"}]},
    {"name": "Withdraw", "type": "event", "anonymous": False, "inputs": [{"indexed": True, "name": "src", "type": "address"}, {"indexed": True, "name": "to", "type": "address"}, {"indexed": False, "name": "amount", "type": "uint256"}]},
]
ABSORB_GAS_UNITS = 450_000       # measured on Base absorb+buy transactions is reported alongside; this is the estimate used before a fork run
NATIVE_PROXY = {8453: "0x4200000000000000000000000000000000000006", 42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"}


@dataclass
class CometConfig(StrategyConfig):
    discovery_hours: float = 5.0           # window of Withdraw events used to find borrowers (live endpoint), in hours of chain time
    max_accounts_per_scan: int = 400


class CompoundV3Strategy:
    name = "compound_v3"
    kind = "liquidation"
    description = "Compound V3 absorb + discounted collateral purchase, flash-financed in base token"

    def __init__(self):
        self.cfg = config_for(self.name, CometConfig())

    # ------------------------------------------------------------------ helpers
    def _comets(self, ctx: Context) -> dict:
        return {k: v for k, v in COMET_MARKETS.items() if v.network == ctx.network.slug}

    def _contract(self, ctx: Context, address: str):
        return ctx.client.w3.eth.contract(address=Web3.to_checksum_address(address), abi=COMET_ABI + EXTRA_ABI)

    def _accounts(self, ctx: Context, comet, from_block: int, to_block: int, extra: list[str] | None = None) -> list[str]:
        """Borrower candidates: senders of Withdraw events (borrowing is a withdrawal of base) in the window, plus extras."""
        found: set[str] = set(extra or [])
        span = 2_000
        b = from_block
        while b <= to_block:
            hi = min(b + span - 1, to_block)
            try:
                for lg in ctx.client.call(comet.events.Withdraw().get_logs, from_block=b, to_block=hi):
                    found.add(Web3.to_checksum_address(lg["args"]["src"]))
            except Exception:  # noqa: BLE001 - a span the provider refuses (public 100-block limits) is retried in 100-block pieces
                for b2 in range(b, hi + 1, 100):
                    with contextlib.suppress(Exception):
                        for lg in ctx.client.call(comet.events.Withdraw().get_logs, from_block=b2, to_block=min(b2 + 99, hi)):
                            found.add(Web3.to_checksum_address(lg["args"]["src"]))
            b = hi + 1
        return sorted(found)[: self.cfg.max_accounts_per_scan]

    @staticmethod
    def _asset_info(comet, collateral: str) -> dict | None:
        """Oracle price, scale and buyer discount (bps) of one collateral asset, read from the fork's Comet."""
        try:
            sf = int(comet.functions.storeFrontPriceFactor().call())
            n = int(comet.functions.numAssets().call())
            for i in range(n):
                a = comet.functions.getAssetInfo(i).call()
                if str(a[1]).lower() == collateral.lower():
                    return {"price": int(comet.functions.getPrice(a[2]).call()), "scale": int(a[3]), "bonus_bps": quote_collateral_bonus_bps(sf, int(a[6]))}
        except Exception:  # noqa: BLE001 - no info means no dust guard; the run proceeds and reports what happens
            return None
        return None

    def _batch_reads(self, ctx: Context, comet, accounts: list[str], block) -> dict[str, tuple[bool, int]]:
        mc = ctx.client.w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=MULTICALL3_ABI)
        out: dict[str, tuple[bool, int]] = {}
        for i in range(0, len(accounts), 100):
            part = accounts[i:i + 100]
            calls = []
            for a in part:
                calls.append((comet.address, True, bytes.fromhex(comet.functions.isLiquidatable(a)._encode_transaction_data()[2:])))
                calls.append((comet.address, True, bytes.fromhex(comet.functions.borrowBalanceOf(a)._encode_transaction_data()[2:])))
            res = ctx.client.call(mc.functions.aggregate3(calls).call, block_identifier=block)
            for j, a in enumerate(part):
                ok1, d1 = res[2 * j]
                ok2, d2 = res[2 * j + 1]
                liq = bool(int.from_bytes(bytes(d1), "big")) if ok1 and d1 else False
                bal = int.from_bytes(bytes(d2), "big") if ok2 and d2 else 0
                out[a] = (liq, bal)
        return out

    # ------------------------------------------------------------------ interface
    def discover(self, ctx: Context) -> list[Opportunity]:
        opps: list[Opportunity] = []
        head = int(ctx.client.call(lambda: ctx.client.w3.eth.block_number)) if ctx.block == "latest" else int(ctx.block)
        for slug, spec in self._comets(ctx).items():
            comet = self._contract(ctx, spec.primary_address)
            adapter = CompoundV3Adapter(spec, ctx.client)
            try:
                base = Web3.to_checksum_address(ctx.client.call(comet.functions.baseToken().call, block_identifier=ctx.block))
                base_scale = int(ctx.client.call(comet.functions.baseScale().call, block_identifier=ctx.block))
                base_feed = ctx.client.call(comet.functions.baseTokenPriceFeed().call, block_identifier=ctx.block)
                base_px = int(ctx.client.call(comet.functions.getPrice(base_feed).call, block_identifier=ctx.block))
                sf = int(ctx.client.call(comet.functions.storeFrontPriceFactor().call, block_identifier=ctx.block))
                assets = adapter.assets(ctx.block)
            except Exception as e:  # noqa: BLE001
                opps.append(Opportunity(self.name, ctx.network.slug, "liquidation", slug, ctx.block, f"{spec.name}: market read failed", None, None,
                                        notes=[str(e)[:120]], data_quality="unknown"))
                continue
            # 1. reserve purchases already available
            for a in assets:
                try:
                    reserves = int(ctx.client.call(comet.functions.getCollateralReserves(a.asset).call, block_identifier=ctx.block))
                except Exception:  # noqa: BLE001
                    continue
                if reserves <= 0:
                    continue
                px = int(ctx.client.call(comet.functions.getPrice(a.price_feed).call, block_identifier=ctx.block))
                bonus_bps = quote_collateral_bonus_bps(sf, a.liquidation_factor)
                value_usd = Decimal(reserves) * Decimal(px) / (Decimal(a.scale) * Decimal(PRICE_SCALE))
                # base needed to buy the whole reserve at the discounted quote
                base_needed = Decimal(reserves) * Decimal(px) * Decimal(10_000) / (Decimal(10_000 + bonus_bps) * Decimal(a.scale) * Decimal(PRICE_SCALE)) * Decimal(base_scale) / Decimal(base_px) * Decimal(PRICE_SCALE)
                opps.append(Opportunity(self.name, ctx.network.slug, "liquidation", f"{slug}:reserve:{a.asset}", ctx.block,
                                        f"{spec.name}: {value_usd:,.2f} USD of seized {a.asset[:8]}… in reserves, buyable at a {bonus_bps} bps discount",
                                        Decimal(0), True, "flashLoanSimple(base) -> buyCollateral -> swap -> repay",
                                        gross_profit_usd=quantize_usd(value_usd - value_usd * Decimal(10_000) / Decimal(10_000 + bonus_bps)),
                                        outcome_class="theoretical", evidence="live_read", data_quality="estimated", confidence="medium", risk="low",
                                        risk_notes=["collateral must be swapped to base at the discount or better; price impact not yet quoted here"],
                                        state={"comet": spec.primary_address, "base": base, "collateral": a.asset, "reserves_raw": reserves, "bonus_bps": bonus_bps,
                                               "base_needed_raw": int(base_needed), "collateral_price": px, "base_price": base_px, "kind": "reserve_purchase",
                                               # dust reserves (gross below the configured floor) are recorded but not worth a fork run
                                               "executable": bool(value_usd - value_usd * Decimal(10_000) / Decimal(10_000 + bonus_bps) >= self.cfg.min_net_profit_usd)},
                                        assumptions=["gross = oracle value of the reserve minus its discounted price; net requires a swap quote and gas (simulate)"]))
            # 2. liquidatable accounts
            extra = list(ctx.config.extra.get("accounts", "").split(",")) if ctx.config.extra.get("accounts") else []
            window = blocks_for_hours(ctx.network.slug, self.cfg.discovery_hours)
            accounts = self._accounts(ctx, comet, max(1, head - window), head, [x for x in extra if x])
            reads = self._batch_reads(ctx, comet, accounts, ctx.block)
            borrowers = [a for a, (_liq, bal) in reads.items() if bal > 0]
            for a, (liq, bal) in reads.items():
                if not liq:
                    continue
                pos = adapter.position(a, ctx.block)
                best = max(pos.detail.get("collateral", []), key=lambda r: Decimal(r["usd"]), default=None)
                debt_usd = pos.debt_usd or Decimal(0)
                bonus_bps = quote_collateral_bonus_bps(sf, int(best["liquidation_factor"])) if best else None
                gross = quantize_usd((pos.collateral_usd or Decimal(0)) * Decimal(bonus_bps) / Decimal(10_000 + bonus_bps)) if bonus_bps is not None else None
                opps.append(Opportunity(self.name, ctx.network.slug, "liquidation", f"{slug}:absorb:{a}", ctx.block,
                                        f"{spec.name}: account {a[:8]}… liquidatable, borrow {debt_usd:,.2f} USD, collateral {pos.collateral_usd or 0:,.2f} USD",
                                        Decimal(0), True, "flashLoanSimple(base) -> absorb -> buyCollateral -> swap -> repay",
                                        gross_profit_usd=gross, outcome_class="theoretical", evidence="live_read", data_quality="verified" if best else "partial",
                                        confidence="medium", risk="medium", risk_notes=["absorb pays nothing; profit only from buying the seized collateral before others do"],
                                        state={"comet": spec.primary_address, "base": base, "account": a, "borrow_raw": bal, "collateral": best["asset"] if best else None,
                                               "collateral_usd": str(pos.collateral_usd), "debt_usd": str(debt_usd), "bonus_bps": bonus_bps, "metric": pos.health_metric,
                                               "kind": "absorb_and_buy", "borrowers_scanned": len(borrowers), "accounts_scanned": len(accounts), "executable": True},
                                        assumptions=["gross = seized collateral value × discount; the whole seized collateral is assumed purchasable right after the absorb"]))
            if not any(o.key.startswith(f"{slug}:absorb:") for o in opps):
                opps.append(Opportunity(self.name, ctx.network.slug, "liquidation", f"{slug}:scan", ctx.block,
                                        f"{spec.name}: {len(borrowers)} borrowers among {len(accounts)} accounts active in the last {window:,} blocks ({self.cfg.discovery_hours:g} h); none liquidatable",
                                        None, True, "", outcome_class="theoretical", evidence="live_read", data_quality="verified", confidence="high", risk="low",
                                        state={"comet": spec.primary_address, "kind": "scan", "borrowers_scanned": len(borrowers), "accounts_scanned": len(accounts), "window_blocks": window}))
        return opps

    def state(self, ctx: Context, opp: Opportunity) -> dict:
        return opp.state

    def simulate(self, ctx: Context, opp: Opportunity) -> SimulationOutcome:
        """Fork at ctx.block (or head): deploy CometLiquidator, flash-finance absorb (if any) + buyCollateral + swap, measure."""
        st = opp.state
        if st.get("kind") not in ("absorb_and_buy", "reserve_purchase"):
            return SimulationOutcome(False, "theoretical", "nothing to execute for a scan row", None, CostBreakdown(), None, False)
        if not ctx.anvil_url or not ctx.archive_client:
            return SimulationOutcome(False, "theoretical", "no fork available", None, CostBreakdown(missing=["fork"]), None, False)
        anvil = AnvilFork(ctx.anvil_url, timeout=300)
        src_url = ctx.network.archive_rpc or ctx.network.rpc_http
        try:
            info = anvil.reset_fork(src_url, None if ctx.block == "latest" else int(ctx.block))
        except Exception as e:  # noqa: BLE001
            return SimulationOutcome(False, "theoretical", f"fork reset failed: {str(e)[:120]}", None, CostBreakdown(missing=["fork"]), None, False)
        w3 = anvil.w3
        art = load_artifact_named("CometLiquidator")
        sim = anvil.accounts()[0]
        anvil.set_balance(sim, 100 * 10**18)
        comet_addr = Web3.to_checksum_address(st["comet"])
        base, coll = Web3.to_checksum_address(st["base"]), Web3.to_checksum_address(st["collateral"])
        router = Web3.to_checksum_address(SWAP_ROUTER_02[ctx.network.chain_id])
        comet = w3.eth.contract(address=comet_addr, abi=COMET_ABI + EXTRA_ABI)
        warnings: list[str] = []
        try:
            factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
            rc = w3.eth.wait_for_transaction_receipt(factory.constructor(Web3.to_checksum_address(ctx.network.market.addresses_provider), sim, sim).transact({"from": sim}), timeout=120)
            c = w3.eth.contract(address=rc["contractAddress"], abi=art["abi"])
            for fn in (c.functions.setRouterAllowed(router, True), c.functions.setTokenAllowed(base, True), c.functions.setTokenAllowed(coll, True), c.functions.setCometAllowed(comet_addr, True)):
                w3.eth.wait_for_transaction_receipt(fn.transact({"from": sim}), timeout=120)
            accounts = [Web3.to_checksum_address(st["account"])] if st.get("kind") == "absorb_and_buy" else []
            # how much collateral will be for sale after the absorb: current reserves + the account's collateral balance
            reserves = int(comet.functions.getCollateralReserves(coll).call())
            if accounts:
                reserves += int(comet.functions.collateralBalanceOf(accounts[0], coll).call())
            if reserves <= 0:
                return SimulationOutcome(False, "simulated", "no collateral would be for sale", None, CostBreakdown(), None, False, fork_block=info.get("block"), path="comet_liquidator")
            # value of what would be for sale, at the Comet oracle: dust is reported as dust, not attempted
            a_info = self._asset_info(comet, coll)
            for_sale_usd = None
            if a_info:
                for_sale_usd = Decimal(reserves) * Decimal(a_info["price"]) / (Decimal(a_info["scale"]) * Decimal(PRICE_SCALE))
                if for_sale_usd * Decimal(a_info["bonus_bps"]) / Decimal(10_000 + a_info["bonus_bps"]) < self.cfg.min_net_profit_usd:
                    return SimulationOutcome(False, "simulated", f"dust: collateral for sale worth {for_sale_usd:.6f} USD at a {a_info['bonus_bps']} bps discount, below the {self.cfg.min_net_profit_usd} USD floor; no transaction attempted",
                                             quantize_usd(for_sale_usd * Decimal(a_info["bonus_bps"]) / Decimal(10_000 + a_info["bonus_bps"])), CostBreakdown(missing=["not attempted"]), None, False,
                                             {"reserves_for_sale": reserves, "for_sale_usd": str(for_sale_usd)}, info.get("block"), "comet_liquidator")
            # base needed for that collateral at the discounted quote (binary search on quoteCollateral)
            lo, hi = 1, 10**30
            for _ in range(80):
                mid = (lo + hi) // 2
                try:
                    q = int(comet.functions.quoteCollateral(coll, mid).call())
                except Exception:  # noqa: BLE001
                    q = 10**40
                if q <= reserves:
                    lo = mid
                else:
                    hi = mid
            base_amount = lo
            cap = int(ctx.config.max_flash_loan_usd) * 10**6 if base.lower() == st.get("base", "").lower() and base_amount > 0 else base_amount
            if base_amount > cap:
                base_amount = cap
                warnings.append("flash loan capped at the configured maximum; buying a part of the reserve")
            expected_coll = int(comet.functions.quoteCollateral(coll, base_amount).call())
            quoter = UniswapV3Quoter(ctx.network.chain_id, ctx.archive_client.__class__(f"{ctx.network.slug}-anvil-q", ctx.anvil_url, 300.0, 1))
            pre = quoter.quote_exact_in(coll, base, expected_coll)
            fee_tier = pre.fee_tier or 500
            out_min = (pre.amount_out_raw * 9_950 // 10_000) if pre.amount_out_raw else 0
            params = (comet_addr, coll, accounts, int(base_amount), int(expected_coll * 99 // 100), int(fee_tier), 0, int(out_min), int(time.time()) + 3600, router)
            calldata = c.functions.liquidate(params)._encode_transaction_data()
            rc2 = anvil.send(sim, c.address, calldata, gas=3_000_000)  # explicit limit: a reverting attempt is mined, so its gas is measured
            gas_price, gas_src = chain_gas_price_wei(ctx)
            warnings.append(f"gas priced at {gas_src}")
            l1 = estimate_l1_fee(ctx.network.chain_id, ctx.archive_client, c.address, bytes.fromhex(calldata[2:]), int(rc2.get("gasUsed") or 0) or 1, gas_price)
            native_px = None
            try:
                from app.aave.reads import AaveReader
                reader = AaveReader(ctx.network, ctx.archive_client.__class__(f"{ctx.network.slug}-anvil-r", ctx.anvil_url, 300.0, 1))
                native_px = reader.asset_prices([NATIVE_PROXY[ctx.network.chain_id]])[Web3.to_checksum_address(NATIVE_PROXY[ctx.network.chain_id])]
            except Exception:  # noqa: BLE001
                warnings.append("native price for gas valuation unavailable")
            costs = CostBreakdown(gas_units=rc2.get("gasUsed"), gas_price_wei=gas_price, source="measured" if rc2.get("gasUsed") else "unknown")
            if rc2.get("gasUsed") and native_px:
                costs.l2_execution_usd = quantize_usd(Decimal(rc2["gasUsed"] * gas_price) / Decimal(10**18) * Decimal(native_px) / Decimal(PRICE_SCALE))
                costs.l1_data_fee_usd = quantize_usd(Decimal(l1.l1_data_wei) / Decimal(10**18) * Decimal(native_px) / Decimal(PRICE_SCALE)) if l1.l1_data_wei is not None else None
                if costs.l1_data_fee_usd is None and l1.source != "not_applicable":
                    costs.missing.append("l1 data fee")
            else:
                costs.missing.append("gas valuation")
            if rc2.get("status") != 1:
                return SimulationOutcome(False, "simulated", rc2.get("revert_reason") or "reverted", None, costs, None, False, {"base_amount": base_amount, "expected_collateral": expected_coll, "reserves_for_sale": reserves},
                                         info.get("block"), "comet_liquidator", warnings=warnings)
            ev = c.events.SimulatedCometLiquidation().process_receipt(rc2, errors=DISCARD)
            e = dict(ev[0]["args"]) if ev else {}
            base_dec = 6 if base.lower() not in (NATIVE_PROXY[ctx.network.chain_id].lower(),) else 18
            base_px = int(comet.functions.getPrice(comet.functions.baseTokenPriceFeed().call()).call())
            premium_usd = quantize_usd(Decimal(int(e.get("premium", 0))) / Decimal(10**base_dec) * Decimal(base_px) / Decimal(PRICE_SCALE))
            surplus_usd = quantize_usd(Decimal(int(e.get("surplus", 0))) / Decimal(10**base_dec) * Decimal(base_px) / Decimal(PRICE_SCALE))
            gross_usd = quantize_usd(Decimal(int(e.get("swapOut", 0)) - int(e.get("baseSpent", 0))) / Decimal(10**base_dec) * Decimal(base_px) / Decimal(PRICE_SCALE))
            costs.flash_premium_usd = premium_usd
            complete = not costs.missing and costs.l2_execution_usd is not None
            net = quantize_usd(surplus_usd - (costs.l2_execution_usd or 0) - (costs.l1_data_fee_usd or 0)) if complete else None
            return SimulationOutcome(True, "simulated", None, gross_usd, costs, net, complete,
                                     {k: str(v) for k, v in e.items()} | {"base_amount": base_amount, "expected_collateral": expected_coll, "reserves_for_sale": reserves, "fee_tier": fee_tier},
                                     info.get("block"), "comet_liquidator",
                                     assumptions=["flash loan of base from Aave V3 (5 bps premium measured); whole purchasable collateral bought in one call; swap on Uniswap v3 SwapRouter02"],
                                     warnings=warnings)
        except Exception as e:  # noqa: BLE001
            return SimulationOutcome(False, "simulated", f"{type(e).__name__}: {str(e)[:160]}", None, CostBreakdown(missing=["execution"]), None, False, fork_block=info.get("block"), path="comet_liquidator")

    def competition(self, ctx: Context, opp: Opportunity, sim: SimulationOutcome | None) -> CompetitionEstimate:
        hist = ctx.config.extra.get("history") or {}
        bids, absorbers, seen = [], {}, set()
        for mk in (hist.get("markets") or {}).values():
            for r in mk.get("absorbs", []):   # one AbsorbDebt + one AbsorbCollateral log per absorb: count transactions once
                if r.get("effective_gas_price_wei") and r["tx"] not in seen:
                    seen.add(r["tx"])
                    bids.append(int(r["effective_gas_price_wei"]))
                    absorbers[r.get("absorber")] = absorbers.get(r.get("absorber"), 0) + 1
        n = sum(absorbers.values())
        competitors = sum(1 for v in absorbers.values() if n and v / n >= 0.05)
        net = sim.net_profit_usd if sim and sim.net_profit_complete else None
        failed = quantize_usd((sim.costs.total_usd or Decimal(0)) * Decimal("0.35")) if sim and sim.costs.total_usd is not None else None
        if not bids or net is None:
            return CompetitionEstimate(None, None, failed, "priority_auction_over_observed_absorb_bids",
                                       ["no observed absorb bids or no complete net profit: capture probability not estimated"], {"absorb_txs": n, "absorbers": len(absorbers)})
        med = sorted(bids)[len(bids) // 2]
        pw = Decimal(str(p_win_priority_auction(med, bids, competitors)))
        att = quantize_usd(pw * net - (Decimal(1) - pw) * (failed or Decimal(0)))
        return CompetitionEstimate(pw, att, failed, "priority_auction_over_observed_absorb_bids",
                                   [f"bid = median observed absorb gas price; P(win) = F(bid)^{competitors} over {len(bids)} observed absorb transactions",
                                    "absorb is permissionless, so the race is for the buyCollateral leg as much as the absorb; modeled as one race"],
                                   {"absorb_txs": n, "absorbers": len(absorbers), "competitors_ge_5pct": competitors, "median_bid_wei": med})

    def replay(self, ctx: Context, from_block: int, to_block: int) -> ReplayReport:
        """Historical evaluation: for each recorded absorb in the window, fork at N−1 and run our receiver on that account."""
        hist = ctx.config.extra.get("history") or {}
        details, values = [], []
        n_sim = n_ok = n_prof = 0
        events = []
        for mk_slug, mk in (hist.get("markets") or {}).items():
            seen = set()
            for r in mk.get("absorbs", []):
                if r["tx"] in seen or not (from_block <= int(r["block"]) <= to_block) or "collateralAbsorbed" not in r:
                    continue
                seen.add(r["tx"])
                events.append((mk_slug, r))
        for mk_slug, r in events[: int(ctx.config.extra.get("max_replays", 20))]:
            spec = COMET_MARKETS[mk_slug]
            blk = int(r["block"])
            sub = Context(ctx.network, ctx.archive_client, ctx.archive_client, ctx.anvil_url, blk - 1, ctx.config)  # historical state: archive only
            comet = self._contract(sub, spec.primary_address)
            try:
                base = Web3.to_checksum_address(ctx.archive_client.call(comet.functions.baseToken().call, block_identifier=blk - 1))
                liq = bool(ctx.archive_client.call(comet.functions.isLiquidatable(Web3.to_checksum_address(r["borrower"])).call, block_identifier=blk - 1))
            except Exception as e:  # noqa: BLE001
                details.append({"tx": r["tx"], "block": blk, "error": str(e)[:100]})
                continue
            opp = Opportunity(self.name, ctx.network.slug, "liquidation", f"{mk_slug}:absorb:{r['borrower']}", blk - 1, "historical absorb", Decimal(0), True,
                              state={"comet": spec.primary_address, "base": base, "account": r["borrower"], "collateral": r["asset"], "kind": "absorb_and_buy"})
            n_sim += 1
            sim = self.simulate(sub, opp) if liq else SimulationOutcome(False, "simulated", "account not liquidatable at N-1 (became so inside block N)", None, CostBreakdown(), None, False, fork_block=blk - 1)
            n_ok += int(sim.success)
            if sim.success and sim.net_profit_complete and sim.net_profit_usd is not None:
                values.append(sim.net_profit_usd)
                n_prof += int(sim.net_profit_usd > 0)
            details.append({"tx": r["tx"], "block": blk, "absorber": r.get("absorber"), "liquidatable_at_n_minus_1": liq, "success": sim.success,
                            "revert_reason": sim.revert_reason, "net_profit_usd": None if sim.net_profit_usd is None else str(sim.net_profit_usd),
                            "complete": sim.net_profit_complete, "measured": sim.measured, "gas_used": sim.costs.gas_units,
                            "historical_gas_used": r.get("gas_used"), "historical_gas_price_wei": r.get("effective_gas_price_wei")})
        return ReplayReport(self.name, ctx.network.slug, from_block, to_block, len(events), n_sim, n_ok, n_prof, None,
                            quantize_usd(sum(values, Decimal(0)) / len(values)) if values else None, min(values) if values else None,
                            f"{len(events)} recorded absorb transactions in the window; first {min(len(events), int(ctx.config.extra.get('max_replays', 20)))} replayed",
                            details, ["fork at N−1 of each absorb; our receiver absorbs the same account and buys its collateral; competition not applied here"])

    def shadow_tick(self, ctx: Context) -> ShadowObservation:
        t = time.monotonic()
        opps = self.discover(ctx)
        head = int(ctx.client.call(lambda: ctx.client.w3.eth.block_number))
        return ShadowObservation(self.name, ctx.network.slug, head, datetime.now(UTC).isoformat(), [o.as_dict() for o in opps], (time.monotonic() - t) * 1000)


register(CompoundV3Strategy())
