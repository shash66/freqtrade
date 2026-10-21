"""Fork simulation of a liquidation (spec §7-§8): manual candidates and historical replays.

Methodology (also in docs/simulation-methodology.md):
- Fork the archive RPC at block N-1 (historical) or at the latest block (candidate). Intra-block ordering is
  NOT reproduced: every historical result is labeled `block_boundary_approximation = True`.
- The simulator account is funded with the debt asset from the reserve's own aToken contract (impersonated),
  which holds the pool's liquidity. This stands in for the flash loan; the flash premium is then added
  analytically from FLASHLOAN_PREMIUM_TOTAL read on the fork. If the compiled FlashLiquidator contract
  artifact is present (contracts/out/FlashLiquidator.sol/FlashLiquidator.json) a future version executes the
  real flash-loan path instead; this build records which path was used.
- The real Pool.liquidationCall is executed on the fork; collateral received is read from the LiquidationCall
  event; swap proceeds are QUOTED on the fork via Uniswap v3 QuoterV2 (not executed).
- Gas: measured gas of liquidationCall plus the modeled overhead of flash loan + swap from the chain gas model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from web3 import Web3

from app.aave.reads import AaveReader
from app.chains.config import NetworkConfig
from app.chains.rpc import RpcClient
from app.chains.validation import load_abi
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.money import WAD, percent_mul, quantize_usd, raw_to_usd
from app.simulation.anvil import AnvilFork, AnvilUnavailable
from app.strategies.quotes import UniswapV3Quoter, price_impact_bps

log = get_logger(__name__)
NATIVE_PROXY = {8453: "0x4200000000000000000000000000000000000006", 42161: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"}


@dataclass
class ReplayRequest:
    network: NetworkConfig
    borrower: str
    collateral_asset: str
    debt_asset: str
    debt_to_cover_raw: int
    block_number: int | None          # fork at this block (historical) or None for latest
    gas_price_wei: int | None = None  # what to price gas at; None -> fork's gas price
    slippage_bps: int = 50
    min_profit_usd: Decimal = Decimal(0)
    historical_tx: str | None = None  # the real liquidation being replayed, if any


@dataclass
class ReplayResult:
    success: bool
    revert_reason: str | None
    fork_block: int | None
    fork_block_hash: str | None
    block_boundary_approximation: bool
    health_factor_at_fork: str | None
    eligible_at_fork: bool | None
    debt_repaid_raw: int | None
    collateral_seized_raw: int | None
    swap_proceeds_raw: int | None
    flash_premium_raw: int | None
    gas_used: int | None
    gas_price_wei: int | None
    gas_cost_usd: Decimal | None
    net_profit_debt_raw: int | None
    net_profit_usd: Decimal | None
    calldata_hex: str | None
    trace_summary: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    rpc_capability: dict = field(default_factory=dict)
    funding_path: str = "impersonated_atoken"


def run_replay(req: ReplayRequest) -> ReplayResult:
    s = get_settings()
    net = req.network
    fork_src = net.archive_rpc if req.block_number is not None else (net.rpc_http or net.archive_rpc)
    cap = {"archive_rpc": bool(net.archive_rpc), "http_rpc": bool(net.rpc_http), "anvil": s.anvil_rpc_url}
    base_assumptions = [
        "fork at the block boundary; intra-block transaction ordering is not reproduced",
        "simulator funded from the reserve aToken (impersonated) instead of a flash loan; premium added analytically",
        "swap proceeds are quoted with Uniswap v3 QuoterV2 on the fork, not executed",
        "gas = measured liquidationCall gas + modeled flash-loan/swap overhead from the chain gas model",
    ]
    if not fork_src:
        return ReplayResult(False, "no archive RPC configured for historical fork" if req.block_number is not None else "no RPC configured",
                            None, None, True, None, None, None, None, None, None, None, None, None, None, None, None,
                            warnings=["simulation skipped: RPC capability missing"], assumptions=base_assumptions, rpc_capability=cap)
    anvil = AnvilFork(s.anvil_rpc_url)
    try:
        info = anvil.reset_fork(fork_src, (req.block_number - 1) if req.block_number is not None else None)
    except Exception as e:
        return ReplayResult(False, f"anvil fork reset failed: {str(e)[:200]}", None, None, True, None, None, None, None, None, None,
                            None, None, None, None, None, None, warnings=["anvil unavailable"], assumptions=base_assumptions, rpc_capability=cap)
    fork_block, fork_hash = info["block"], info["block_hash"]
    client = RpcClient(f"{net.slug}-anvil", s.anvil_rpc_url, 120.0, 1)
    reader = AaveReader(net, client)
    w3 = anvil.w3
    pool_addr = Web3.to_checksum_address(net.market.pool)
    pool = w3.eth.contract(address=pool_addr, abi=load_abi("pool"))
    warnings: list[str] = []
    # 1. eligibility at the fork block
    acct = reader.user_account_data(req.borrower)
    hf = Decimal(acct.health_factor_wad) / Decimal(WAD) if acct.health_factor_wad < 2**256 - 1 else Decimal("Infinity")
    eligible = acct.total_debt_base > 0 and acct.health_factor_wad < WAD
    if not eligible:
        warnings.append("borrower not liquidatable at the fork block (N-1): the historical liquidation depended on intra-block state")
    # 2. funding from the aToken of the debt reserve
    sim = anvil.accounts()[0]
    anvil.set_balance(sim, 100 * 10**18)
    a_token, _, _ = reader.reserve_tokens(req.debt_asset)
    try:
        anvil.fund_erc20_from(req.debt_asset, a_token, sim, req.debt_to_cover_raw)
    except AnvilUnavailable as e:
        return ReplayResult(False, f"funding failed: {e}", fork_block, fork_hash, True, str(hf), eligible, None, None, None, None, None,
                            None, None, None, None, None, warnings=warnings, assumptions=base_assumptions, rpc_capability=cap)
    debt_token = anvil.erc20(req.debt_asset)
    coll_token = anvil.erc20(req.collateral_asset)
    debt_token.functions.approve(pool_addr, req.debt_to_cover_raw).transact({"from": sim})
    coll_before = coll_token.functions.balanceOf(sim).call()
    debt_before = debt_token.functions.balanceOf(sim).call()
    # 3. the real liquidationCall
    fn = pool.functions.liquidationCall(Web3.to_checksum_address(req.collateral_asset), Web3.to_checksum_address(req.debt_asset),
                                        Web3.to_checksum_address(req.borrower), req.debt_to_cover_raw, False)
    calldata = fn._encode_transaction_data()
    rc = anvil.send(sim, pool_addr, calldata)
    premium_bps, _ = reader.flashloan_premium_bps()
    premium = percent_mul(req.debt_to_cover_raw, premium_bps)
    gas_price = req.gas_price_wei if req.gas_price_wei is not None else int(w3.eth.gas_price)
    prices = reader.asset_prices([req.debt_asset, req.collateral_asset, NATIVE_PROXY[net.chain_id]])
    dp = prices[Web3.to_checksum_address(req.debt_asset)]
    native_p = prices[Web3.to_checksum_address(NATIVE_PROXY[net.chain_id])]
    d_dec = int(debt_token.functions.decimals().call())
    c_dec = int(coll_token.functions.decimals().call())
    if rc.get("status") != 1:
        return ReplayResult(False, rc.get("revert_reason") or "liquidationCall reverted", fork_block, fork_hash, True, str(hf), eligible,
                            None, None, None, premium, rc.get("gasUsed"), gas_price, None, None, None, calldata,
                            trace_summary=[c.__dict__ for c in anvil.trace(rc["transactionHash"])] if rc.get("transactionHash") else [],
                            warnings=warnings, assumptions=base_assumptions, rpc_capability=cap)
    # 4. results from the event and balances
    seized = coll_token.functions.balanceOf(sim).call() - coll_before
    repaid = debt_before - debt_token.functions.balanceOf(sim).call()
    gas_used = int(rc["gasUsed"]) + net.gas.base_gas_units_liquidation // 2  # modeled flash-loan + swap overhead
    # 5. swap quote on the fork
    quoter = UniswapV3Quoter(net.chain_id, client)
    q = quoter.quote_exact_in(req.collateral_asset, req.debt_asset, seized)
    swap_out = q.amount_out_raw
    if q.status != "ok":
        warnings.append(f"swap quote unavailable on fork: {q.error}")
    gas_cost_wei = gas_used * gas_price
    gas_usd = quantize_usd(raw_to_usd(gas_cost_wei, 18, native_p))
    net_raw = net_usd = None
    if swap_out is not None:
        gas_in_debt = int(gas_usd * Decimal(10**d_dec) * Decimal(10**8) / Decimal(dp))
        net_raw = swap_out - repaid - premium - gas_in_debt
        net_usd = quantize_usd(raw_to_usd(abs(net_raw), d_dec, dp)) * (1 if net_raw >= 0 else -1)
        if req.min_profit_usd and net_usd < req.min_profit_usd:
            warnings.append(f"net profit {net_usd} below requested minimum {req.min_profit_usd}")
        cp = prices[Web3.to_checksum_address(req.collateral_asset)]
        impact = price_impact_bps(seized, c_dec, cp, swap_out, d_dec, dp)
        if impact > req.slippage_bps:
            warnings.append(f"quoted price impact {impact} bps exceeds slippage limit {req.slippage_bps} bps")
    trace = [c.__dict__ for c in anvil.trace(rc["transactionHash"])]
    return ReplayResult(True, None, fork_block, fork_hash, True, str(hf), eligible, repaid, seized, swap_out, premium, gas_used, gas_price,
                        gas_usd, net_raw, net_usd, calldata, trace_summary=trace, warnings=warnings, assumptions=base_assumptions,
                        rpc_capability=cap)
