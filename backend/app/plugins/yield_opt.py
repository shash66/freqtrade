"""Strategy C: automated yield optimization research (Phase 5, §5). Lower-frequency, capital-based, read-only.

What is measured (every figure is a contract read at the block evaluated, or a difference of two such reads):
- Aave V3 reserve rates: `Pool.getReserveData(asset)` -> currentLiquidityRate / currentVariableBorrowRate (ray, APR);
  available liquidity = underlying balance of the aToken; supply/borrow caps from the configuration.
- Compound V3 rates: `Comet.getSupplyRate(utilization)` / `getBorrowRate(utilization)` (per-second, 1e18) at the
  current `getUtilization()`; available liquidity = totalSupply − totalBorrow.
- Yield-bearing collateral growth: the Aave oracle price ratio of an LST (wstETH, weETH, cbETH) to WETH at two
  blocks `lookback_days` apart, annualised (the chain's own view of the staking yield).

Opportunity kinds (outcome class `passive_yield`, never mixed with transaction revenue):
- `stable_lending`: best supply APY for a stablecoin across the protocols on the network, net of the gas to
  enter/exit amortised over the holding period, with the rate spread between venues (rebalancing signal).
- `lst_loop`: supply an LST, borrow WETH in the Aave e-mode (or plain) at the maximum safe loop; net APY =
  leverage × (LST yield + supply APY) − (leverage − 1) × borrow APY, with the liquidation distance implied by the
  loop; flash loans only enter/exit the loop, they do not add return.
Risks are listed explicitly (smart contract, depeg, rate variability, liquidation exposure, liquidity exit).
Historical: rates sampled at daily blocks over the lookback window (archive) give the average and the range.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from web3 import Web3

from app.aave.reads import AaveReader
from app.chains.validation import load_abi
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
from app.protocols.compound_v3 import COMET_ABI, COMET_MARKETS

RAY = 10**27
SECONDS_PER_YEAR = 365 * 24 * 3600
PRICE_SCALE = 10**8
COMET_RATE_ABI = [
    {"name": "getUtilization", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
    {"name": "getSupplyRate", "type": "function", "stateMutability": "view", "inputs": [{"type": "uint256"}], "outputs": [{"type": "uint64"}]},
    {"name": "getBorrowRate", "type": "function", "stateMutability": "view", "inputs": [{"type": "uint256"}], "outputs": [{"type": "uint64"}]},
    {"name": "totalSupply", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
    {"name": "totalBorrow", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
]
STABLES = {"base": ["0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA"],
           "arbitrum": ["0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1"]}
LSTS = {"base": {"wstETH": "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452", "weETH": "0x04C0599Ae5A44757c0af6F9eC3b93da8976c150A", "cbETH": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22"},
        "arbitrum": {"wstETH": "0x5979D7b546E38E414F7E9822514be443A4800529", "weETH": "0x35751007a407ca6FEFfE80b3cB397736D2cf4dbe"}}
WETH = {"base": "0x4200000000000000000000000000000000000006", "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"}
BLOCKS_PER_DAY = {"base": 43_200, "arbitrum": 345_600}


@dataclass
class YieldConfig(StrategyConfig):
    capital_usd: Decimal = Decimal("10000")
    holding_days: int = 30
    lookback_days: int = 7
    enter_exit_gas_usd: Decimal = Decimal("0.60")     # measured order of magnitude on Base/Arbitrum for supply+withdraw; labeled estimate
    loop_safety_bps: int = 300                        # keep LTV this far below the liquidation threshold


def _apy_from_ray_apr(rate_ray: int) -> Decimal:
    apr = Decimal(rate_ray) / Decimal(RAY)
    return (Decimal(1) + apr / Decimal(SECONDS_PER_YEAR)) ** SECONDS_PER_YEAR - Decimal(1) if apr else Decimal(0)


def _apy_from_per_second(rate_1e18: int) -> Decimal:
    r = Decimal(rate_1e18) / Decimal(10**18)
    return (Decimal(1) + r) ** SECONDS_PER_YEAR - Decimal(1) if r else Decimal(0)


class YieldStrategy:
    name = "yield_opt"
    kind = "yield"
    description = "Stablecoin lending and LST looping across Aave V3 and Compound V3, net of costs and with explicit risks"

    def __init__(self):
        self.cfg = config_for(self.name, YieldConfig())

    # ------------------------------------------------------------------ readers
    def _aave_rates(self, ctx: Context, asset: str) -> dict | None:
        pool = ctx.client.w3.eth.contract(address=Web3.to_checksum_address(ctx.network.market.pool), abi=load_abi("pool"))
        try:
            d = ctx.client.call(pool.functions.getReserveData(Web3.to_checksum_address(asset)).call, block_identifier=ctx.block)
            reader = AaveReader(ctx.network, ctx.client)
            cfg = reader.reserve_config(asset, ctx.block)
            atoken = d[8]
            erc = ctx.client.w3.eth.contract(address=Web3.to_checksum_address(asset), abi=load_abi("erc20"))
            avail = int(ctx.client.call(erc.functions.balanceOf(Web3.to_checksum_address(atoken)).call, block_identifier=ctx.block))
            dec = int(ctx.client.call(erc.functions.decimals().call, block_identifier=ctx.block))
            return {"venue": "aave_v3", "supply_apy": _apy_from_ray_apr(int(d[2])), "borrow_apy": _apy_from_ray_apr(int(d[4])), "available_raw": avail, "decimals": dec,
                    "ltv_bps": cfg.ltv_bps, "liquidation_threshold_bps": cfg.liquidation_threshold_bps, "supply_cap": cfg.supply_cap, "borrow_cap": cfg.borrow_cap,
                    "active": cfg.active, "frozen": cfg.frozen, "paused": cfg.paused}
        except Exception as e:  # noqa: BLE001
            return {"venue": "aave_v3", "error": str(e)[:100]}

    def _comet_rates(self, ctx: Context, base_asset: str) -> list[dict]:
        out = []
        for slug, spec in COMET_MARKETS.items():
            if spec.network != ctx.network.slug:
                continue
            c = ctx.client.w3.eth.contract(address=Web3.to_checksum_address(spec.primary_address), abi=COMET_ABI + COMET_RATE_ABI)
            try:
                base = Web3.to_checksum_address(ctx.client.call(c.functions.baseToken().call, block_identifier=ctx.block))
                if base.lower() != base_asset.lower():
                    continue
                util = int(ctx.client.call(c.functions.getUtilization().call, block_identifier=ctx.block))
                sr = int(ctx.client.call(c.functions.getSupplyRate(util).call, block_identifier=ctx.block))
                br = int(ctx.client.call(c.functions.getBorrowRate(util).call, block_identifier=ctx.block))
                ts = int(ctx.client.call(c.functions.totalSupply().call, block_identifier=ctx.block))
                tb = int(ctx.client.call(c.functions.totalBorrow().call, block_identifier=ctx.block))
                out.append({"venue": slug, "supply_apy": _apy_from_per_second(sr), "borrow_apy": _apy_from_per_second(br), "utilization": Decimal(util) / Decimal(10**18),
                            "available_raw": max(ts - tb, 0)})
            except Exception as e:  # noqa: BLE001
                out.append({"venue": slug, "error": str(e)[:100]})
        return out

    def _lst_yield(self, ctx: Context, lst: str) -> tuple[Decimal | None, dict]:
        """Annualised growth of the oracle price ratio LST/WETH over lookback_days (archive reads)."""
        arch = ctx.archive_client or ctx.client
        reader = AaveReader(ctx.network, arch)
        head = int(arch.call(lambda: arch.w3.eth.block_number)) if ctx.block == "latest" else int(ctx.block)
        past = head - self.cfg.lookback_days * BLOCKS_PER_DAY[ctx.network.slug]
        try:
            w = Web3.to_checksum_address(WETH[ctx.network.slug])
            p_now = reader.asset_prices([lst, w], head)
            p_then = reader.asset_prices([lst, w], past)
            r_now = Decimal(p_now[Web3.to_checksum_address(lst)]) / Decimal(p_now[w])
            r_then = Decimal(p_then[Web3.to_checksum_address(lst)]) / Decimal(p_then[w])
            growth = r_now / r_then - Decimal(1)
            apy = (Decimal(1) + growth) ** Decimal(365 / self.cfg.lookback_days) - Decimal(1)
            return apy, {"ratio_now": str(r_now), "ratio_then": str(r_then), "blocks": [past, head], "lookback_days": self.cfg.lookback_days}
        except Exception as e:  # noqa: BLE001
            return None, {"error": str(e)[:100]}

    # ------------------------------------------------------------------ interface
    def discover(self, ctx: Context) -> list[Opportunity]:
        opps: list[Opportunity] = []
        cap = self.cfg.capital_usd
        hold = Decimal(self.cfg.holding_days) / Decimal(365)
        gas = self.cfg.enter_exit_gas_usd
        risks_common = ["smart-contract risk of the lending protocol (audited, battle-tested, not zero)", "rates float with utilisation; the quoted APY is the current one"]
        # 1. stablecoin lending across venues
        for asset in STABLES.get(ctx.network.slug, []):
            rows = [r for r in [self._aave_rates(ctx, asset)] + self._comet_rates(ctx, asset) if r and "error" not in r]
            for r in rows:  # a frozen/paused/inactive reserve cannot be entered; it stays in the table but never wins
                r["enterable"] = bool(r.get("active", True)) and not r.get("frozen", False) and not r.get("paused", False)
            enterable = [r for r in rows if r["enterable"]]
            if not enterable:
                continue
            best = max(enterable, key=lambda r: r["supply_apy"])
            others = sorted((r for r in rows if r is not best), key=lambda r: -r["supply_apy"])
            gross = quantize_usd(cap * best["supply_apy"] * hold)
            net = quantize_usd(gross - gas)
            avail_usd = Decimal(best["available_raw"]) / Decimal(10**6)
            opps.append(Opportunity(self.name, ctx.network.slug, "yield", f"stable_lending:{asset}:{best['venue']}", ctx.block,
                                    f"Lend {cap:,.0f} USD of {asset[:8]}… on {best['venue']} at {best['supply_apy'] * 100:.2f}% APY"
                                    + (f" (next best {others[0]['venue']} {others[0]['supply_apy'] * 100:.2f}%)" if others else ""),
                                    cap, False, "no flash loan: capital must be held; a flash loan cannot earn a lending rate",
                                    gross_profit_usd=gross, costs=CostBreakdown(other_usd=gas, source="estimated"), net_profit_usd=net, net_profit_complete=True,
                                    outcome_class="passive_yield", evidence="live_read", data_quality="verified", confidence="high",
                                    risk="low" if avail_usd > cap * 10 else "medium",
                                    risk_notes=risks_common + ["stablecoin depeg risk", f"exit liquidity now {avail_usd:,.0f} USD available" + ("" if avail_usd > cap * 10 else " (thin relative to the position)")],
                                    state={"kind": "stable_lending", "asset": asset, "venues": [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in rows],
                                           "spread_to_next_bps": int((best["supply_apy"] - others[0]["supply_apy"]) * 10_000) if others else None,
                                           "holding_days": self.cfg.holding_days, "capital_usd": str(cap)},
                                    assumptions=[f"{self.cfg.holding_days}-day hold at today's APY; enter+exit gas {gas} USD (estimate); passive yield, not transaction revenue"]))
        # 2. LST loops on Aave (supply LST, borrow WETH)
        weth = WETH.get(ctx.network.slug)
        weth_rates = self._aave_rates(ctx, weth) if weth else None
        for sym, lst in LSTS.get(ctx.network.slug, {}).items():
            r = self._aave_rates(ctx, lst)
            if not r or "error" in r or not weth_rates or "error" in weth_rates:
                continue
            lst_apy, info = self._lst_yield(ctx, lst)
            if lst_apy is None:
                opps.append(Opportunity(self.name, ctx.network.slug, "yield", f"lst_loop:{sym}", ctx.block, f"{sym} loop: staking yield not measurable ({info.get('error')})",
                                        cap, True, "", outcome_class="passive_yield", data_quality="unknown", state={"kind": "lst_loop", "asset": lst}))
                continue
            # e-mode (ETH-correlated) bonus is read when the user is in e-mode; for the loop sizing use the reserve LT as the conservative bound
            lt = Decimal(r["liquidation_threshold_bps"]) / Decimal(10_000)
            ltv_used = max(Decimal(0), lt - Decimal(self.cfg.loop_safety_bps) / Decimal(10_000))
            leverage = Decimal(1) / (Decimal(1) - ltv_used) if ltv_used < 1 else Decimal(1)
            net_apy = leverage * (lst_apy + r["supply_apy"]) - (leverage - Decimal(1)) * weth_rates["borrow_apy"]
            unlev_apy = lst_apy + r["supply_apy"]
            gross = quantize_usd(cap * leverage * (lst_apy + r["supply_apy"]) * hold)
            financing = quantize_usd(cap * (leverage - Decimal(1)) * weth_rates["borrow_apy"] * hold)
            net = quantize_usd(cap * net_apy * hold - gas * leverage)
            liq_drop = (Decimal(1) - ltv_used / lt) if lt else None
            opps.append(Opportunity(self.name, ctx.network.slug, "yield", f"lst_loop:{sym}", ctx.block,
                                    f"{sym} loop on Aave: {leverage:.2f}x at LTV {ltv_used * 100:.1f}% (LT {lt * 100:.1f}%): staking {lst_apy * 100:.2f}% + supply {r['supply_apy'] * 100:.2f}% "
                                    f"vs WETH borrow {weth_rates['borrow_apy'] * 100:.2f}% -> net {net_apy * 100:.2f}% APY (unlevered {unlev_apy * 100:.2f}%)",
                                    cap, True, "flash loan only to enter/exit the loop atomically; it adds no return",
                                    gross_profit_usd=gross, costs=CostBreakdown(financing_usd=financing, other_usd=quantize_usd(gas * leverage), source="estimated"),
                                    net_profit_usd=net, net_profit_complete=True, outcome_class="passive_yield", evidence="live_read", data_quality="verified",
                                    confidence="medium", risk="high" if leverage > 3 else "medium",
                                    risk_notes=risks_common + [f"liquidation if the {sym}/WETH ratio falls {liq_drop * 100:.1f}% (ratio moves are small but the loop is {leverage:.1f}x)",
                                                               "borrow rate can exceed the staking yield (negative carry)", "LST depeg / withdrawal queue risk"],
                                    state={"kind": "lst_loop", "asset": lst, "lst_apy": str(lst_apy), "supply_apy": str(r["supply_apy"]), "borrow_apy": str(weth_rates["borrow_apy"]),
                                           "leverage": str(leverage), "ltv_used": str(ltv_used), "liquidation_threshold": str(lt), "net_apy": str(net_apy), "lst_measurement": info,
                                           "weth_available_raw": weth_rates["available_raw"], "capital_usd": str(cap)},
                                    assumptions=[f"staking yield = annualised oracle ratio growth over {self.cfg.lookback_days} days", "loop held at a constant LTV; rebalancing gas not included beyond entry/exit",
                                                 "passive yield, not transaction revenue"]))
        return opps

    def state(self, ctx: Context, opp: Opportunity) -> dict:
        return opp.state

    def simulate(self, ctx: Context, opp: Opportunity) -> SimulationOutcome:
        return SimulationOutcome(False, "theoretical", "yield positions are not simulated as transactions; returns are rate reads", opp.gross_profit_usd, opp.costs, opp.net_profit_usd, opp.net_profit_complete)

    def competition(self, ctx: Context, opp: Opportunity, sim: SimulationOutcome | None) -> CompetitionEstimate:
        return CompetitionEstimate(Decimal(1), opp.net_profit_usd, Decimal(0), "none_passive",
                                   ["passive yield has no execution race; the risk is rate drift and protocol/peg events, not competition"], {})

    def replay(self, ctx: Context, from_block: int, to_block: int) -> ReplayReport:
        """Daily samples of the stablecoin supply APYs and the LST loop inputs over the range (archive)."""
        step = BLOCKS_PER_DAY[ctx.network.slug]
        arch = ctx.archive_client or ctx.client
        samples = []
        for blk in range(from_block, to_block + 1, step):
            sub = Context(ctx.network, arch, arch, None, blk, ctx.config)
            row = {"block": blk}
            for asset in STABLES.get(ctx.network.slug, [])[:1]:
                rows = [r for r in [self._aave_rates(sub, asset)] + self._comet_rates(sub, asset) if r and "error" not in r]
                row["stable_supply_apy"] = {r["venue"]: str(quantize_usd(r["supply_apy"] * 100)) for r in rows}
            weth = WETH.get(ctx.network.slug)
            wr = self._aave_rates(sub, weth) if weth else None
            row["weth_borrow_apy"] = str(quantize_usd(wr["borrow_apy"] * 100)) if wr and "error" not in wr else None
            samples.append(row)
        vals = []
        for s_ in samples:
            best = max((Decimal(v) for v in (s_.get("stable_supply_apy") or {}).values()), default=None)
            if best is not None:
                vals.append(best)
        cap, hold = self.cfg.capital_usd, Decimal(self.cfg.holding_days) / Decimal(365)
        avg_apy = (sum(vals, Decimal(0)) / len(vals)) if vals else None
        expected = quantize_usd(cap * avg_apy / Decimal(100) * hold - self.cfg.enter_exit_gas_usd) if avg_apy is not None else None
        stats = {"samples": len(vals), "best_stable_apy_pct_avg": str(quantize_usd(avg_apy)) if avg_apy is not None else None,
                 "best_stable_apy_pct_min": str(min(vals)) if vals else None, "best_stable_apy_pct_max": str(max(vals)) if vals else None}
        return ReplayReport(self.name, ctx.network.slug, from_block, to_block, len(samples), 0, 0, len(vals), None, expected, None,
                            f"{len(samples)} daily samples; expected profit = {cap:,.0f} USD × average best stablecoin APY × {self.cfg.holding_days}/365 − entry/exit gas; "
                            "worst-case loss not sampled (a depeg or exploit, not a rate path, is the loss scenario)",
                            [{"stats": stats}] + samples,
                            ["rates read at daily blocks on the archive endpoint; APY in percent", "passive yield: every sampled day counts as 'net profitable' when the APY is positive"])

    def shadow_tick(self, ctx: Context) -> ShadowObservation:
        t = time.monotonic()
        opps = self.discover(ctx)
        head = int(ctx.client.call(lambda: ctx.client.w3.eth.block_number))
        return ShadowObservation(self.name, ctx.network.slug, head, datetime.now(UTC).isoformat(), [o.as_dict() for o in opps], (time.monotonic() - t) * 1000)


register(YieldStrategy())
