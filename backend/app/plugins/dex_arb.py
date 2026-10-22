"""Strategy B: DEX arbitrage (Phase 5, §4) on Base and Arbitrum.

Venues: Uniswap v3 pools (quoted with QuoterV2, executed through SwapRouter02) and Aerodrome on Base (quoted with
the router's getAmountsOut, executed through the same router). Pool addresses are resolved from the factories
and verified (bytecode) before use; nothing is quoted against a stale or hypothetical price: every quote is an
`eth_call` at the block being evaluated, and the opportunity's size is chosen on those quotes.

An opportunity is a pair of venues for the same token pair where buying on one and selling on the other returns
more of the input token than the flash-loan principal plus premium. Size: a geometric grid from 0.01 to the
configured maximum of the input token, refined by golden-section search on the quoted profit curve. Costs:
flash premium (Aave 5 bps, measured on the fork), gas (measured on the fork, valued at the oracle native price),
L1 data fee (estimated). The complete transaction runs on a private fork through FlashArb.sol.

Competition: arbitrage on these chains is a backrun race for the same state; we cannot observe losers. We measure
instead how long a quoted spread above costs persists (shadow: blocks until it disappears) and report the capture
probability as the share of spreads still present at the next block, labeled as that proxy.
"""
from __future__ import annotations

import contextlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from web3 import Web3
from web3.logs import DISCARD

from app.aave.reads import AaveReader
from app.chains.l1fees import estimate_l1_fee
from app.chains.rpc import RpcClient
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
from app.plugins.util import chain_gas_price_wei
from app.simulation.anvil import AnvilFork
from app.simulation.fast_path import MULTICALL3, MULTICALL3_ABI, QUOTER_ABI
from app.simulation.receiver import SWAP_ROUTER_02, load_artifact_named
from app.strategies.quotes import QUOTER_V2

PRICE_SCALE = 10**8
NATIVE_PROXY = {8453: "0x4200000000000000000000000000000000000006", 42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"}
AERODROME_ROUTER = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"
AERODROME_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"
SOLIDLY_ROUTER_ABI = [{"name": "getAmountsOut", "type": "function", "stateMutability": "view",
                       "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "routes", "type": "tuple[]", "components": [
                           {"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "stable", "type": "bool"}, {"name": "factory", "type": "address"}]}],
                       "outputs": [{"name": "amounts", "type": "uint256[]"}]}]


INSUFFICIENT_PROFIT_SELECTOR = Web3.keccak(text="InsufficientProfit(uint256,uint256)")[:4].hex()


def _decode_insufficient_profit(reason: str) -> tuple[int, int] | None:
    """Pull (actual, required) out of a FlashArb `InsufficientProfit` revert string (the node echoes the revert data)."""
    m = re.search(r"(?:0x)?" + INSUFFICIENT_PROFIT_SELECTOR + r"([0-9a-fA-F]{128})", reason or "")
    if not m:
        return None
    data = m.group(1)
    return int(data[:64], 16), int(data[64:128], 16)


def _usd(raw: int, price_raw: int, decimals: int) -> Decimal:
    return quantize_usd(Decimal(raw) * Decimal(price_raw) / (Decimal(10**decimals) * Decimal(PRICE_SCALE)))


@dataclass(frozen=True)
class Venue:
    kind: str            # univ3 | solidly
    name: str
    router: str
    pool: str
    fee: int = 0         # univ3 fee tier
    stable: bool = False
    factory: str = ""


@dataclass(frozen=True)
class Pair:
    key: str
    token_a: str
    token_b: str
    dec_a: int
    dec_b: int
    venues: tuple[Venue, ...]


# Pool addresses resolved from the factories (Uniswap v3 getPool / Aerodrome getPool) on 2026-10-22 and verified live.
PAIRS: dict[str, list[Pair]] = {
    "base": [
        Pair("WETH/USDC", "0x4200000000000000000000000000000000000006", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 18, 6, (
            Venue("univ3", "Uniswap v3 0.05%", SWAP_ROUTER_02[8453], "0xd0b53D9277642d899DF5C87A3966A349A798F224", 500),
            Venue("univ3", "Uniswap v3 0.3%", SWAP_ROUTER_02[8453], "0x6c561B446416E1A00E8E93E221854d6eA4171372", 3000),
            Venue("solidly", "Aerodrome vAMM", AERODROME_ROUTER, "0xcDAC0d6c6C59727a65F871236188350531885C43", 0, False, AERODROME_FACTORY))),
        Pair("USDC/USDbC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", 6, 6, (
            Venue("univ3", "Uniswap v3 0.01%", SWAP_ROUTER_02[8453], "0x06959273E9A65433De71F5A452D529544E07dDD0", 100),
            Venue("solidly", "Aerodrome sAMM", AERODROME_ROUTER, "0x27a8Afa3Bd49406e48a074350fB7b2020c43B2bD", 0, True, AERODROME_FACTORY))),
        Pair("cbBTC/WETH", "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "0x4200000000000000000000000000000000000006", 8, 18, (
            Venue("univ3", "Uniswap v3 0.05%", SWAP_ROUTER_02[8453], "0x7AEa2E8A3843516AfA07293a10AC8E49906DAbd1", 500),)),
        Pair("cbBTC/USDC", "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 8, 6, (
            Venue("univ3", "Uniswap v3 0.05%", SWAP_ROUTER_02[8453], "0xfBB6Eed8e7aa03B138556eeDaF5D271A5E1e43ef", 500),)),
    ],
    "arbitrum": [
        Pair("WETH/USDC", "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 18, 6, (
            Venue("univ3", "Uniswap v3 0.05%", SWAP_ROUTER_02[42161], "0xC6962004f452bE9203591991D15f6b388e09E8D0", 500),
            Venue("univ3", "Uniswap v3 0.3%", SWAP_ROUTER_02[42161], "0xC473e2aEE3441BF9240Be85eb122aBB059A3B57c", 3000))),
        Pair("USDC/USDT", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6, 6, (
            Venue("univ3", "Uniswap v3 0.01%", SWAP_ROUTER_02[42161], "0xbE3aD6a5669Dc0B8b12FeBC03608860C31E2eef6", 100),)),
        Pair("USDC/USDC.e", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8", 6, 6, (
            Venue("univ3", "Uniswap v3 0.01%", SWAP_ROUTER_02[42161], "0x8e295789c9465487074a65b1ae9Ce0351172393f", 100),)),
    ],
}
# 3-venue pairs give 6 directed venue pairs; single-venue pairs are quoted only (price reference, no arbitrage possible within one pool).


@dataclass
class DexArbConfig(StrategyConfig):
    max_size_usd: Decimal = Decimal("50000")
    min_size_usd: Decimal = Decimal("50")
    grid_points: int = 7


class DexArbStrategy:
    name = "dex_arb"
    kind = "arbitrage"
    description = "Two-leg atomic arbitrage between Uniswap v3 / Aerodrome pools, flash-financed"

    def __init__(self):
        self.cfg = config_for(self.name, DexArbConfig())

    # ------------------------------------------------------------------ quoting
    def _quote_calls(self, w3, venue: Venue, token_in: str, token_out: str, amount: int) -> tuple[str, bytes]:
        if venue.kind == "univ3":
            q = w3.eth.contract(address=Web3.to_checksum_address(QUOTER_V2[self._chain]), abi=QUOTER_ABI)
            return (q.address, bytes.fromhex(q.functions.quoteExactInputSingle((Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out), amount, venue.fee, 0))._encode_transaction_data()[2:]))
        r = w3.eth.contract(address=Web3.to_checksum_address(venue.router), abi=SOLIDLY_ROUTER_ABI)
        return (r.address, bytes.fromhex(r.functions.getAmountsOut(amount, [(Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out), venue.stable, Web3.to_checksum_address(venue.factory))])._encode_transaction_data()[2:]))

    def _decode(self, w3, venue: Venue, ok: bool, data: bytes) -> int | None:
        if not ok or not data:
            return None
        try:
            if venue.kind == "univ3":
                return int(w3.codec.decode(["uint256", "uint160", "uint32", "uint256"], data)[0])
            amounts = w3.codec.decode(["uint256[]"], data)[0]
            return int(amounts[-1])
        except Exception:  # noqa: BLE001
            return None

    def _multiquote(self, ctx: Context, requests: list[tuple[Venue, str, str, int]]) -> list[int | None]:
        """One Multicall3 round trip for many quotes: [(venue, token_in, token_out, amount)] -> amounts out (None = no quote)."""
        w3 = ctx.client.w3
        mc = w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=MULTICALL3_ABI)
        out: list[int | None] = []
        for i in range(0, len(requests), 60):
            part = requests[i:i + 60]
            calls = [(a, True, d) for a, d in (self._quote_calls(w3, v, ti, to, amt) for v, ti, to, amt in part)]
            res = ctx.client.call(mc.functions.aggregate3(calls).call, block_identifier=ctx.block)
            out += [self._decode(w3, v, bool(ok), bytes(d)) for (v, _, _, _), (ok, d) in zip(part, res, strict=True)]
        return out

    def _prices(self, ctx: Context) -> dict[str, int]:
        reader = AaveReader(ctx.network, ctx.client)
        assets = sorted({t for p in PAIRS.get(ctx.network.slug, []) for t in (p.token_a, p.token_b)} | {NATIVE_PROXY[ctx.network.chain_id]})
        try:
            return {k.lower(): v for k, v in reader.asset_prices(assets, ctx.block).items()}
        except Exception:  # noqa: BLE001 - some pair tokens may not be Aave reserves: price them one by one, missing stays absent
            out = {}
            for a in assets:
                with contextlib.suppress(Exception):
                    out[a.lower()] = reader.asset_prices([a], ctx.block)[Web3.to_checksum_address(a)]
            return out

    # ------------------------------------------------------------------ interface
    def discover(self, ctx: Context) -> list[Opportunity]:
        self._chain = ctx.network.chain_id
        prices = self._prices(ctx)
        opps: list[Opportunity] = []
        for pair in PAIRS.get(ctx.network.slug, []):
            pa, pb = prices.get(pair.token_a.lower()), prices.get(pair.token_b.lower())
            if len(pair.venues) < 2 or not pa:
                continue
            # size grid in token A, from min to max USD
            lo = max(1, int(self.cfg.min_size_usd * Decimal(10**pair.dec_a) * Decimal(PRICE_SCALE) / Decimal(pa)))
            hi = max(lo + 1, int(self.cfg.max_size_usd * Decimal(10**pair.dec_a) * Decimal(PRICE_SCALE) / Decimal(pa)))
            n = self.cfg.grid_points
            sizes = sorted({int(lo * (hi / lo) ** (i / (n - 1))) for i in range(n)})
            for direction, (tin, tout, dec_in) in enumerate(((pair.token_a, pair.token_b, pair.dec_a), (pair.token_b, pair.token_a, pair.dec_b))):
                px_in = pa if direction == 0 else pb
                if not px_in:
                    continue
                sz = sizes if direction == 0 else sorted({int(s * Decimal(pa) / Decimal(pb) / Decimal(10**pair.dec_a) * Decimal(10**pair.dec_b)) for s in sizes if pb}) or sizes
                # leg 1 on every venue for every size, then leg 2 on every other venue for the resulting amounts
                req1 = [(v, tin, tout, s) for v in pair.venues for s in sz]
                out1 = self._multiquote(ctx, req1)
                req2, idx = [], []
                for i, (v, _t, _o, _s) in enumerate(req1):
                    if out1[i]:
                        for v2 in pair.venues:
                            if v2 is not v:
                                req2.append((v2, tout, tin, out1[i]))
                                idx.append((i, v2))
                out2 = self._multiquote(ctx, req2) if req2 else []
                best = None
                for (i, v2), back in zip(idx, out2, strict=True):
                    v1, _t, _o, s = req1[i]
                    if back is None:
                        continue
                    premium = s * 5 // 10_000
                    gross = back - s
                    net_pre_gas = gross - premium
                    if best is None or net_pre_gas > best[0]:
                        best = (net_pre_gas, s, back, v1, v2, out1[i], premium)
                if best is None:
                    continue
                net_pre_gas, s, back, v1, v2, mid, premium = best
                gross = back - s
                gross_usd = _usd(gross, px_in, dec_in) if gross > 0 else -_usd(-gross, px_in, dec_in)
                prem_usd = _usd(premium, px_in, dec_in)
                opps.append(Opportunity(self.name, ctx.network.slug, "arbitrage", f"{pair.key}:{v1.name}->{v2.name}:{'ab' if direction == 0 else 'ba'}", ctx.block,
                                        f"{pair.key}: buy on {v1.name}, sell on {v2.name}, size {Decimal(s) / Decimal(10**dec_in):,.4f} {'A' if direction == 0 else 'B'}; "
                                        f"quoted gross {gross_usd} USD, premium {prem_usd} USD",
                                        Decimal(0), True, "flashLoanSimple(tokenIn) -> leg 1 -> leg 2 -> repay (FlashArb.sol)",
                                        gross_profit_usd=gross_usd, costs=CostBreakdown(flash_premium_usd=prem_usd, source="estimated", missing=["gas (simulate)"]),
                                        net_profit_usd=None, net_profit_complete=False, outcome_class="theoretical", evidence="live_read",
                                        data_quality="verified" if net_pre_gas > 0 else "verified", confidence="medium" if net_pre_gas > 0 else "high",
                                        risk="medium", risk_notes=["atomic: a failed leg reverts the whole transaction; the cost of a failed attempt is gas only"],
                                        state={"kind": "arbitrage", "pair": pair.key, "token_in": tin, "token_out": tout, "dec_in": dec_in, "size_raw": s, "mid_raw": mid, "back_raw": back,
                                               "premium_raw": premium, "net_pre_gas_raw": net_pre_gas, "leg1": v1.__dict__, "leg2": v2.__dict__, "price_in": px_in,
                                               "executable": net_pre_gas > 0, "net_pre_gas_usd": str(quantize_usd(gross_usd - prem_usd)), "grid": sz},
                                        assumptions=["quotes are eth_call at this block (QuoterV2 / Aerodrome getAmountsOut); size chosen on the quoted profit curve; gas not yet included"]))
        return opps

    def state(self, ctx: Context, opp: Opportunity) -> dict:
        return opp.state

    def simulate(self, ctx: Context, opp: Opportunity) -> SimulationOutcome:
        st = opp.state
        if st.get("kind") != "arbitrage":
            return SimulationOutcome(False, "theoretical", "not an arbitrage row", None, CostBreakdown(), None, False)
        if not ctx.anvil_url:
            return SimulationOutcome(False, "theoretical", "no fork available", None, CostBreakdown(missing=["fork"]), None, False)
        anvil = AnvilFork(ctx.anvil_url, timeout=300)
        try:
            info = anvil.reset_fork(ctx.network.archive_rpc or ctx.network.rpc_http, None if ctx.block == "latest" else int(ctx.block))
        except Exception as e:  # noqa: BLE001
            return SimulationOutcome(False, "theoretical", f"fork reset failed: {str(e)[:120]}", None, CostBreakdown(missing=["fork"]), None, False)
        w3 = anvil.w3
        art = load_artifact_named("FlashArb")
        sim = anvil.accounts()[0]
        anvil.set_balance(sim, 100 * 10**18)
        tin, tout = Web3.to_checksum_address(st["token_in"]), Web3.to_checksum_address(st["token_out"])
        l1v, l2v = st["leg1"], st["leg2"]
        try:
            factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
            rc = w3.eth.wait_for_transaction_receipt(factory.constructor(Web3.to_checksum_address(ctx.network.market.addresses_provider), sim, sim).transact({"from": sim}), timeout=120)
            c = w3.eth.contract(address=rc["contractAddress"], abi=art["abi"])
            for fn in (c.functions.setTokenAllowed(tin, True), c.functions.setTokenAllowed(tout, True),
                       c.functions.setRouterAllowed(Web3.to_checksum_address(l1v["router"]), True), c.functions.setRouterAllowed(Web3.to_checksum_address(l2v["router"]), True)):
                w3.eth.wait_for_transaction_receipt(fn.transact({"from": sim}), timeout=120)
            legs = []
            for v, a, b in ((l1v, tin, tout), (l2v, tout, tin)):
                legs.append((0 if v["kind"] == "univ3" else 1, Web3.to_checksum_address(v["router"]), a, b, int(v.get("fee") or 0), bool(v.get("stable")),
                             Web3.to_checksum_address(v["factory"]) if v.get("factory") else "0x0000000000000000000000000000000000000000", 0))
            params = (tin, int(st["size_raw"]), legs, 0, int(time.time()) + 3600)
            calldata = c.functions.arbitrage(params)._encode_transaction_data()
            rc2 = anvil.send(sim, c.address, calldata, gas=2_500_000)  # explicit limit: a reverting attempt is mined, so its gas is measured
            gas_price, gas_src = chain_gas_price_wei(ctx)
            arch = ctx.archive_client or ctx.client
            l1 = estimate_l1_fee(ctx.network.chain_id, arch, c.address, bytes.fromhex(calldata[2:]), int(rc2.get("gasUsed") or 0) or 1, gas_price)
            prices = self._prices(Context(ctx.network, RpcClient(f"{ctx.network.slug}-anvil-p", ctx.anvil_url, 300.0, 1), block="latest"))
            native_px = prices.get(NATIVE_PROXY[ctx.network.chain_id].lower())
            px_in = int(st["price_in"])
            costs = CostBreakdown(gas_units=rc2.get("gasUsed"), gas_price_wei=gas_price, source="measured" if rc2.get("gasUsed") else "unknown")
            if rc2.get("gasUsed") and native_px:
                costs.l2_execution_usd = quantize_usd(Decimal(rc2["gasUsed"] * gas_price) / Decimal(10**18) * Decimal(native_px) / Decimal(PRICE_SCALE))
                costs.l1_data_fee_usd = quantize_usd(Decimal(l1.l1_data_wei) / Decimal(10**18) * Decimal(native_px) / Decimal(PRICE_SCALE)) if l1.l1_data_wei is not None else None
                if costs.l1_data_fee_usd is None and l1.source != "not_applicable":
                    costs.missing.append("l1 data fee")
            else:
                costs.missing.append("gas valuation")
            dec_in = int(st["dec_in"])
            if rc2.get("status") != 1:
                reason = rc2.get("revert_reason") or "reverted"
                short = _decode_insufficient_profit(reason)
                if short is not None:
                    # Both swaps ran and the receiver refused to settle: `actual` is what the round trip returned and
                    # `required` the principal + premium (+ minProfit = 0). The shortfall is a measured loss, not an estimate.
                    actual, required = short
                    size = int(st["size_raw"])
                    gross = _usd(actual - size, px_in, dec_in) if actual >= size else -_usd(size - actual, px_in, dec_in)
                    costs.flash_premium_usd = _usd(required - size, px_in, dec_in)
                    gas_usd = (costs.l2_execution_usd or Decimal(0)) + (costs.l1_data_fee_usd or Decimal(0))
                    complete = not costs.missing and costs.l2_execution_usd is not None
                    net = quantize_usd(gross - costs.flash_premium_usd - gas_usd) if complete else None
                    return SimulationOutcome(False, "simulated", f"InsufficientProfit(actual={actual}, required={required}) [{reason[:80]}]", gross, costs, net, complete,
                                             {"size_raw": st["size_raw"], "round_trip_out": str(actual), "owed": str(required), "gas_units_until_revert": rc2.get("gasUsed")},
                                             info.get("block"), "flash_arb",
                                             assumptions=["both swap legs executed on the fork; the receiver reverted at settlement because the round trip returned less than principal + premium",
                                                          "net = measured round-trip P&L − measured flash premium − gas of the reverted transaction (what an attempt would have cost)", f"gas priced at {gas_src}"],
                                             warnings=["negative result: a reverted attempt on-chain costs only its gas; the swap shortfall is never realised"])
                return SimulationOutcome(False, "simulated", reason, None, costs, None, False, {"size_raw": st["size_raw"]}, info.get("block"), "flash_arb")
            ev = c.events.SimulatedArbitrage().process_receipt(rc2, errors=DISCARD)
            e = dict(ev[0]["args"]) if ev else {}
            premium, surplus, amount_out = int(e.get("premium", 0)), int(e.get("surplus", 0)), int(e.get("amountOut", 0))
            costs.flash_premium_usd = _usd(premium, px_in, dec_in)
            gross = _usd(amount_out - int(st["size_raw"]), px_in, dec_in) if amount_out >= int(st["size_raw"]) else -_usd(int(st["size_raw"]) - amount_out, px_in, dec_in)
            complete = not costs.missing and costs.l2_execution_usd is not None
            net = quantize_usd(_usd(surplus, px_in, dec_in) - (costs.l2_execution_usd or 0) - (costs.l1_data_fee_usd or 0)) if complete else None
            return SimulationOutcome(True, "simulated", None, gross, costs, net, complete, {k: str(v) for k, v in e.items()} | {"size_raw": st["size_raw"]}, info.get("block"), "flash_arb",
                                     assumptions=["Aave V3 flash loan of the input token (premium measured); both swaps executed on the fork; gas valued at the Aave oracle native price", f"gas priced at {gas_src}"])
        except Exception as e:  # noqa: BLE001
            return SimulationOutcome(False, "simulated", f"{type(e).__name__}: {str(e)[:160]}", None, CostBreakdown(missing=["execution"]), None, False, fork_block=info.get("block"), path="flash_arb")

    def competition(self, ctx: Context, opp: Opportunity, sim: SimulationOutcome | None) -> CompetitionEstimate:
        persist = ctx.config.extra.get("spread_persistence")   # measured by the shadow run: share of above-cost spreads still present at the next block
        net = sim.net_profit_usd if sim and sim.net_profit_complete else None
        failed = quantize_usd((sim.costs.total_usd or Decimal(0)) * Decimal("0.6")) if sim and sim.costs.total_usd is not None else None
        if persist is None or net is None:
            return CompetitionEstimate(None, None, failed, "spread_persistence_proxy", ["capture probability not estimated: no shadow persistence measurement or no complete net profit"], {})
        p = Decimal(str(persist))
        return CompetitionEstimate(p, quantize_usd(p * net - (Decimal(1) - p) * (failed or Decimal(0))), failed, "spread_persistence_proxy",
                                   ["p_capture = measured share of above-cost spreads still quoted at the next block (proxy for 'not already taken'); an atomic failed attempt burns ~60 % of the gas"], {"spread_persistence": persist})

    def replay(self, ctx: Context, from_block: int, to_block: int) -> ReplayReport:
        """Historical sampling: every `step` blocks in the range, quote every directed venue pair at that block (archive) and
        record the best net-of-premium spread; count how often it exceeds a gas floor estimated from the fork measurements."""
        step = int(ctx.config.extra.get("step", 200))
        gas_floor = Decimal(str(ctx.config.extra.get("gas_floor_usd", "0.05")))
        samples, above = [], 0
        arch = ctx.archive_client or ctx.client
        for blk in range(from_block, to_block + 1, step):
            sub = Context(ctx.network, arch, arch, None, blk, ctx.config)
            try:
                opps = self.discover(sub)
            except Exception as e:  # noqa: BLE001
                samples.append({"block": blk, "error": str(e)[:80]})
                continue
            best = max(opps, key=lambda o: o.gross_profit_usd - (o.costs.flash_premium_usd or 0) if o.gross_profit_usd is not None else Decimal("-1e9"), default=None)
            if best is None:
                samples.append({"block": blk, "none": True})
                continue
            npg = (best.gross_profit_usd or Decimal(0)) - (best.costs.flash_premium_usd or Decimal(0))
            hit = npg > gas_floor
            above += int(hit)
            samples.append({"block": blk, "key": best.key, "gross_usd": str(best.gross_profit_usd), "net_pre_gas_usd": str(quantize_usd(npg)), "above_gas_floor": hit, "size_raw": best.state["size_raw"]})
        vals = [Decimal(s["net_pre_gas_usd"]) for s in samples if "net_pre_gas_usd" in s]
        return ReplayReport(self.name, ctx.network.slug, from_block, to_block, len(vals), 0, 0, above, None,
                            quantize_usd(sum(vals, Decimal(0)) / len(vals)) if vals else None, min(vals) if vals else None,
                            f"{len(vals)} sampled blocks every {step} blocks; {above} with a quoted spread above the {gas_floor} USD gas floor (before competition)",
                            samples, ["spreads quoted at historical blocks via archive eth_call; no execution; gas floor from fork measurements"])

    def shadow_tick(self, ctx: Context) -> ShadowObservation:
        t = time.monotonic()
        self._chain = ctx.network.chain_id
        opps = self.discover(ctx)
        head = int(ctx.client.call(lambda: ctx.client.w3.eth.block_number))
        return ShadowObservation(self.name, ctx.network.slug, head, datetime.now(UTC).isoformat(), [o.as_dict() for o in opps], (time.monotonic() - t) * 1000)


register(DexArbStrategy())
