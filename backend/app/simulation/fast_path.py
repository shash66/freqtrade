"""Low-latency candidate evaluation path (Phase 4, priority 1).

Two simulation paths now exist and are kept separate:

- **fast path** (this module): no fork, no transaction. One Multicall3 round trip reads everything the
  protocol math needs (account data, the two user reserve states, the two reserve configurations, the
  oracle prices, the flash-loan premium, the user's e-mode), the pure profitability functions run, and a
  second Multicall3 round trip quotes the collateral→debt swap on every Uniswap v3 fee tier and (on OP-stack
  chains) the L1 data fee. Result: a theoretical candidate with a complete cost picture in two RPC round trips,
  with every stage timed by a monotonic clock. Outcome class: theoretical.
- **full path** (`app.simulation.replay`): Anvil fork, real receiver deployed, flash loan executed, swap
  executed, every figure measured. Outcome class: simulated. Tens of seconds per evaluation on a public
  archive provider (Phase 3: 17 s median).

The fast path is what a live strategy would run on every head; the full path is what validates it.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from web3 import Web3

from app.aave.reads import ReserveConfig, UserAccountData, UserReserveData
from app.chains.config import NetworkConfig
from app.chains.l1fees import FeeAccounting, serialize_unsigned_1559
from app.chains.rpc import RpcClient
from app.chains.validation import load_abi
from app.core.money import WAD
from app.strategies import profitability as pf
from app.strategies.quotes import FEE_TIERS, QUOTER_V2, Quote

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
MULTICALL3_ABI = [{"inputs": [{"components": [{"name": "target", "type": "address"}, {"name": "allowFailure", "type": "bool"},
                                              {"name": "callData", "type": "bytes"}], "name": "calls", "type": "tuple[]"}],
                   "name": "aggregate3", "outputs": [{"components": [{"name": "success", "type": "bool"}, {"name": "returnData", "type": "bytes"}],
                                                      "name": "returnData", "type": "tuple[]"}], "stateMutability": "payable", "type": "function"}]
OP_GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F"
NATIVE_PROXY = {8453: "0x4200000000000000000000000000000000000006", 42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"}
QUOTER_ABI = [{"inputs": [{"components": [{"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"}, {"name": "amountIn", "type": "uint256"},
                                          {"name": "fee", "type": "uint24"}, {"name": "sqrtPriceLimitX96", "type": "uint160"}], "name": "params", "type": "tuple"}],
               "name": "quoteExactInputSingle", "outputs": [{"name": "amountOut", "type": "uint256"}, {"name": "sqrtPriceX96After", "type": "uint160"},
                                                             {"name": "initializedTicksCrossed", "type": "uint32"}, {"name": "gasEstimate", "type": "uint256"}],
               "stateMutability": "nonpayable", "type": "function"}]
GPO_ABI = [{"inputs": [{"name": "_data", "type": "bytes"}], "name": "getL1Fee", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]


@dataclass
class StageTimings:
    rpc_reads_ms: float = 0.0
    math_ms: float = 0.0
    quote_ms: float = 0.0
    fee_estimate_ms: float = 0.0
    total_ms: float = 0.0
    rpc_round_trips: int = 0


@dataclass
class FastEvaluation:
    borrower: str
    collateral: str
    debt: str
    block: int | str
    eligible: bool
    health_factor: str | None
    reasons: list[str]
    debt_to_cover_raw: int | None
    collateral_to_liquidator_raw: int | None
    swap_out_raw: int | None
    swap_fee_tier: int | None
    flash_premium_raw: int | None
    gas_units: int | None
    gas_price_wei: int | None
    l1_data_fee_wei: int | None
    fee_source: str
    gross_bonus_usd: str | None
    net_profit_usd: str | None
    complete: bool
    outcome_class: str = "theoretical"
    timings: StageTimings = field(default_factory=StageTimings)
    assumptions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class FastEvaluator:
    """Stateless apart from contract objects; safe to reuse across evaluations on one network."""

    def __init__(self, net: NetworkConfig, client: RpcClient):
        self.net, self.client, self.w3 = net, client, client.w3
        self.pool = self.w3.eth.contract(address=Web3.to_checksum_address(net.market.pool), abi=load_abi("pool"))
        self.oracle = self.w3.eth.contract(address=Web3.to_checksum_address(net.market.oracle), abi=load_abi("oracle"))
        self.dp = self.w3.eth.contract(address=Web3.to_checksum_address(net.market.data_provider), abi=load_abi("data_provider"))
        self.mc = self.w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=MULTICALL3_ABI)
        self.quoter = self.w3.eth.contract(address=Web3.to_checksum_address(QUOTER_V2[net.chain_id]), abi=QUOTER_ABI) if net.chain_id in QUOTER_V2 else None
        self.gpo = self.w3.eth.contract(address=Web3.to_checksum_address(OP_GAS_PRICE_ORACLE), abi=GPO_ABI) if net.gas.l1_data_fee_applies and net.chain_id != 42161 else None
        self.erc20_abi = load_abi("erc20")

    def _agg(self, calls: list[tuple[str, bytes]], block) -> list[tuple[bool, bytes]]:
        res = self.client.call(self.mc.functions.aggregate3([(Web3.to_checksum_address(t), True, d) for t, d in calls]).call, block_identifier=block)
        return [(bool(ok), bytes(data)) for ok, data in res]

    def quote_round_trip(self, collateral: str, debt: str, amount_in_raw: int, block="latest") -> list[tuple[bool, bytes]]:
        """One Multicall3 round trip quoting every fee tier (used by the latency benchmark; same call shape as evaluate)."""
        coll, dbt = Web3.to_checksum_address(collateral), Web3.to_checksum_address(debt)
        if self.quoter is None or coll == dbt:
            return []
        calls = [(self.quoter.address, bytes.fromhex(self.quoter.functions.quoteExactInputSingle((coll, dbt, amount_in_raw, fee, 0))._encode_transaction_data()[2:]))
                 for fee in FEE_TIERS]
        return self._agg(calls, block)

    def evaluate(self, borrower: str, collateral: str, debt: str, *, block="latest", gas_price_wei: int | None = None,
                 decimals: tuple[int, int] | None = None, rules: pf.LiquidationRules | None = None,
                 max_flash_raw: int | None = None, slippage_bps: int = 50) -> FastEvaluation:
        t0 = time.monotonic()
        rules = rules or pf.LiquidationRules()
        user, coll, dbt = (Web3.to_checksum_address(x) for x in (borrower, collateral, debt))
        native = Web3.to_checksum_address(NATIVE_PROXY.get(self.net.chain_id, coll))
        tim = StageTimings()
        reasons: list[str] = []
        # --- stage 1: one round trip for every protocol read ---------------------------------------
        calls = [
            (self.pool.address, self.pool.functions.getUserAccountData(user)._encode_transaction_data()),
            (self.dp.address, self.dp.functions.getUserReserveData(coll, user)._encode_transaction_data()),
            (self.dp.address, self.dp.functions.getUserReserveData(dbt, user)._encode_transaction_data()),
            (self.pool.address, self.pool.functions.getConfiguration(coll)._encode_transaction_data()),
            (self.pool.address, self.pool.functions.getConfiguration(dbt)._encode_transaction_data()),
            (self.oracle.address, self.oracle.functions.getAssetsPrices([coll, dbt, native])._encode_transaction_data()),
            (self.pool.address, self.pool.functions.FLASHLOAN_PREMIUM_TOTAL()._encode_transaction_data()),
            (self.pool.address, self.pool.functions.getUserEMode(user)._encode_transaction_data()),
        ]
        if decimals is None:
            c_erc, d_erc = (self.w3.eth.contract(address=a, abi=self.erc20_abi) for a in (coll, dbt))
            calls += [(coll, c_erc.functions.decimals()._encode_transaction_data()), (dbt, d_erc.functions.decimals()._encode_transaction_data())]
        t = time.monotonic()
        res = self._agg([(a, bytes.fromhex(d[2:])) for a, d in calls], block)
        tim.rpc_reads_ms = (time.monotonic() - t) * 1000
        tim.rpc_round_trips += 1
        dec = self.w3.codec.decode
        try:
            acct = UserAccountData(*[int(x) for x in dec(["uint256"] * 6, res[0][1])])
            urc = dec(["uint256"] * 7 + ["uint40", "bool"], res[1][1])   # aToken, stableDebt, variableDebt, principalStable, scaledVariable, stableRate, liquidityRate, ts, collateral
            urd = dec(["uint256"] * 7 + ["uint40", "bool"], res[2][1])
            cfg_c = ReserveConfig.from_bitmap(int(dec(["uint256"], res[3][1])[0]))
            cfg_d = ReserveConfig.from_bitmap(int(dec(["uint256"], res[4][1])[0]))
            prices = [int(x) for x in dec(["uint256[]"], res[5][1])[0]]
            premium_bps = int(dec(["uint256"], res[6][1])[0])
            emode = int(dec(["uint256"], res[7][1])[0])
            if decimals is None:
                decimals = (int(dec(["uint8"], res[8][1])[0]), int(dec(["uint8"], res[9][1])[0]))
        except Exception as e:  # noqa: BLE001 - a decode failure is an incomplete result, not a guess
            tim.total_ms = (time.monotonic() - t0) * 1000
            return FastEvaluation(user, coll, dbt, block, False, None, [f"read/decode failed: {str(e)[:120]}"], None, None, None, None, None, None, None, None,
                                  "unknown", None, None, False, timings=tim)
        c_dec, d_dec = decimals
        cp, dp_, native_p = prices
        # --- stage 2: pure math ----------------------------------------------------------------------
        t = time.monotonic()
        hf = Decimal(acct.health_factor_wad) / Decimal(WAD) if acct.health_factor_wad < 2**256 - 1 else Decimal("Infinity")
        ur_c = UserReserveData(int(urc[0]), int(urc[1]), int(urc[2]), bool(urc[8]))
        ur_d = UserReserveData(int(urd[0]), int(urd[1]), int(urd[2]), bool(urd[8]))
        if emode:
            reasons.append(f"user in e-mode category {emode}: category bonus not read on the fast path (reserve bonus used; full path reads it)")
        pos = pf.PositionInputs(health_factor_wad=acct.health_factor_wad, total_collateral_base=acct.total_collateral_base, total_debt_base=acct.total_debt_base,
                                debt=pf.ReserveParams(dbt, "", d_dec, dp_, cfg_d.liquidation_bonus_bps, cfg_d.liquidation_protocol_fee_bps, cfg_d.liquidation_threshold_bps,
                                                      cfg_d.active, cfg_d.paused),
                                collateral=pf.ReserveParams(coll, "", c_dec, cp, cfg_c.liquidation_bonus_bps, cfg_c.liquidation_protocol_fee_bps, cfg_c.liquidation_threshold_bps,
                                                            cfg_c.active, cfg_c.paused),
                                user_debt_raw=ur_d.variable_debt + ur_d.stable_debt, user_collateral_raw=ur_c.a_token_balance, collateral_enabled=ur_c.usage_as_collateral)
        elig = pf.check_eligibility(pos, rules)
        if not elig.eligible:
            tim.math_ms = (time.monotonic() - t) * 1000
            tim.total_ms = (time.monotonic() - t0) * 1000
            return FastEvaluation(user, coll, dbt, block, False, str(hf), elig.reasons, None, None, None, None, None, None, None, None, "not_applicable",
                                  None, None, True, timings=tim)
        outcome, problems = pf.choose_debt_to_cover(pos, rules, elig, max_flash_raw)
        reasons += problems
        tim.math_ms = (time.monotonic() - t) * 1000
        # --- stage 3: one round trip for swap quotes (all fee tiers) + L1 fee ------------------------
        t = time.monotonic()
        q = Quote("incomplete", None, None, None, None, "uniswap_v3_quoter_v2", "no quoter on this chain")
        l1 = FeeAccounting(None, 0, "not_applicable", "chain has no separate L1 data fee")
        gas_units = self.net.gas.base_gas_units_liquidation
        same_asset = coll == dbt
        qcalls: list[tuple[str, bytes]] = []
        if self.quoter is not None and not same_asset:
            qcalls.extend((self.quoter.address, bytes.fromhex(self.quoter.functions.quoteExactInputSingle((coll, dbt, outcome.collateral_to_liquidator_raw, fee, 0))._encode_transaction_data()[2:]))
                          for fee in FEE_TIERS)
        gp = gas_price_wei if gas_price_wei is not None else None
        calldata = self.pool.functions.liquidationCall(coll, dbt, user, outcome.debt_to_cover_raw, False)._encode_transaction_data()
        if self.gpo is not None:
            raw = serialize_unsigned_1559(self.net.chain_id, self.net.market.pool, bytes.fromhex(calldata[2:]) + b"\x00" * 160, gas_units, gp or 10**6)
            qcalls.append((self.gpo.address, bytes.fromhex(self.gpo.functions.getL1Fee(raw)._encode_transaction_data()[2:])))
        if qcalls:
            qres = self._agg(qcalls, block)
            tim.rpc_round_trips += 1
            best = None
            n_q = len(FEE_TIERS) if (self.quoter is not None and not same_asset) else 0
            for fee, (ok, data) in zip(FEE_TIERS[:n_q], qres[:n_q], strict=False):
                if ok and len(data) >= 128:
                    out, _s, _t, gas = dec(["uint256", "uint160", "uint32", "uint256"], data)
                    if best is None or int(out) > best.amount_out_raw:
                        best = Quote("ok", int(out), fee, int(gas), None, "uniswap_v3_quoter_v2")
            if best is not None:
                q = best
            elif n_q:
                q = Quote("incomplete", None, None, None, None, "uniswap_v3_quoter_v2", "no fee tier returned a quote")
            if self.gpo is not None:
                ok, data = qres[-1]
                l1 = FeeAccounting(None, int(dec(["uint256"], data)[0]), "estimated", "op_gas_price_oracle.getL1Fee(serialized_tx)") if ok and data else \
                    FeeAccounting(None, None, "unknown", "getL1Fee failed")
        if same_asset:
            q = Quote("ok", outcome.collateral_to_liquidator_raw, 0, 0, None, "same_asset_no_swap")
        tim.quote_ms = (time.monotonic() - t) * 1000
        # --- stage 4: gas price (one call unless supplied) and the report ------------------------------
        t = time.monotonic()
        if gp is None:
            gp = int(self.client.call(lambda: self.w3.eth.gas_price))
            tim.rpc_round_trips += 1
        if self.net.chain_id == 42161:
            l1 = FeeAccounting(None, None, "unknown", "Arbitrum NodeInterface not queried on the fast path")
            reasons.append("Arbitrum L1 component not estimated on the fast path (full path reads it on the real node)")
        gas_units += q.gas_estimate or 0
        costs = pf.CostInputs(flash_premium_total_bps=premium_bps, gas_units=gas_units, gas_price_wei=gp, native_price_raw=native_p,
                              l1_data_fee_wei=l1.l1_data_wei, l1_fee_required=l1.source != "not_applicable", swap_out_raw=q.amount_out_raw, swap_source=q.source)
        if q.status != "ok":
            reasons.append(f"no swap quote: {q.error}")
        report = pf.profit_report(pos, rules, outcome, costs, prior_incomplete=reasons)
        tim.fee_estimate_ms = (time.monotonic() - t) * 1000
        tim.total_ms = (time.monotonic() - t0) * 1000
        return FastEvaluation(user, coll, dbt, block, True, str(hf), report.incomplete_reasons, outcome.debt_to_cover_raw, outcome.collateral_to_liquidator_raw,
                              q.amount_out_raw, q.fee_tier, report.flash_premium_raw, gas_units, gp, l1.l1_data_wei, l1.source,
                              str(report.gross_bonus_usd), None if report.net_profit_usd is None else str(report.net_profit_usd), report.complete,
                              timings=tim, assumptions=list(outcome.assumptions) + ["fast path: theoretical candidate from one Multicall3 read and one quote round trip; nothing executed"])
