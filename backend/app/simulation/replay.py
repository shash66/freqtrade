"""Fork simulation of a liquidation (spec §7-§8): manual candidates and historical replays.

Methodology (also in docs/simulation-methodology.md):
- Fork the archive RPC at block N-1 (historical) or at the latest block (candidate). Intra-block ordering is
  NOT reproduced: every historical result is labeled `block_boundary_approximation = True`.
- Execution path `flash_loan_receiver` (default when the compiled FlashLiquidator artifact is available and
  Uniswap SwapRouter02 exists on the fork): the receiver is deployed on the fork and takes a real Aave
  flashLoanSimple, repays the borrower, swaps the collateral on-chain and repays principal + premium. Gas,
  premium, swap output and surplus are measured from the transaction and its SimulatedLiquidation event.
- Execution path `direct_liquidation_call` (fallback, always labeled as simplified): the simulator account is
  funded with the debt asset from the reserve's aToken (impersonated), Pool.liquidationCall is executed,
  swap proceeds are QUOTED (QuoterV2, not executed) and the flash premium is added analytically.
- Gas: measured gas of liquidationCall plus the modeled overhead of flash loan + swap from the chain gas model.
"""
from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from decimal import Decimal

from web3 import Web3

from app.aave.reads import AaveReader
from app.chains.config import NetworkConfig
from app.chains.l1fees import FeeAccounting, estimate_l1_fee
from app.chains.rpc import RpcClient
from app.chains.validation import load_abi
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.money import WAD, percent_mul
from app.simulation.accounting import reconcile_profit
from app.simulation.anvil import AnvilFork, AnvilUnavailable
from app.simulation.receiver import ReceiverArtifactMissing, run_receiver_liquidation
from app.strategies.quotes import UniswapV3Quoter, price_impact_bps

log = get_logger(__name__)
FLASH_LOAN_OVERHEAD_GAS = 150_000  # flashLoanSimple entry/exit + receiver callback; modeled, labeled in assumptions
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
    force_direct_path: bool = False   # True = skip the receiver and run the labeled simplified path


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
    execution_path: str = "direct_liquidation_call"   # or flash_loan_receiver
    fees: dict | None = None                          # FeeAccounting.as_dict()
    retried: bool = False
    attempts: int = 1
    eligible_at_execution: bool | None = None         # HF < 1 at the execution timestamp, before block N's own txs
    health_factor_at_execution: str | None = None
    surplus_raw: int | None = None
    profit_complete: bool | None = None
    reconciliation: dict | None = None


AAVE_ELIGIBILITY_SELECTORS = {"0x930bb771": "HealthFactorNotBelowThreshold", "0xb629b0e4": "MustNotLeaveDust"}


def run_replay(req: ReplayRequest) -> ReplayResult:
    """Run once; if an *eligible* position reverts with an Aave eligibility/dust error, rerun once on a fresh
    fork. Rate-limited archive providers have produced one-off inconsistent fork state in live validation;
    a result that changes between two forks is reported with `retried` so it can be triaged as infrastructure."""
    r = _run_with_fork_retries(req)
    attempts = r.attempts
    if (not r.success and r.eligible_at_fork and r.revert_reason
            and any(sel in r.revert_reason for sel in AAVE_ELIGIBILITY_SELECTORS)):
        first = r.revert_reason
        r = _run_with_fork_retries(req)
        attempts += r.attempts
        if not r.success and r.revert_reason and _decode_sel(r.revert_reason):
            r.revert_reason = f"{r.revert_reason} [{_decode_sel(r.revert_reason)}]"
        r.warnings.append(f"retried on a fresh fork after an Aave eligibility revert ({_decode_sel(first)}); "
                          + ("second attempt succeeded: first revert attributed to inconsistent fork state" if r.success
                             else "second attempt reverted too: treated as a real protocol-rule revert"))
        r.retried = True
    r.attempts = attempts
    if not r.success and r.revert_reason:
        r.revert_reason = f"{r.revert_reason} [{_decode_sel(r.revert_reason)}]" if _decode_sel(r.revert_reason) else r.revert_reason
    return r


FORK_FETCH_MARKERS = ("failed to get storage", "failed to get account", "max retries", "429", "database error")


def _run_with_fork_retries(req: ReplayRequest, attempts: int = 3) -> ReplayResult:
    """A rate-limited archive provider makes Anvil's lazy state fetches fail mid-replay (surfacing as -32603
    errors from raw calls). Cool down and rerun the replay from a fresh fork a bounded number of times."""
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            r = _run_replay_once(req)
            r.attempts = i
            return r
        except Exception as e:  # noqa: BLE001 - classified below
            msg = str(e).lower()
            if not any(k in msg for k in FORK_FETCH_MARKERS):
                raise
            last = e
            log.warning("replay_fork_fetch_failed", attempt=i, error=str(e)[:160])
            time.sleep(min(30 * i, 90))
    assert last is not None
    r = ReplayResult(False, f"fork state fetch failed after {attempts} attempts: {str(last)[:200]}", None, None, True, None, None,
                     None, None, None, None, None, None, None, None, None, None,
                     warnings=["infrastructure: archive provider rate limit exhausted during fork state fetch"],
                     assumptions=[], rpc_capability={}, execution_path="none")
    r.attempts = attempts
    return r


def _decode_sel(reason: str) -> str:
    for sel, name in AAVE_ELIGIBILITY_SELECTORS.items():
        if sel in reason:
            return name
    return ""


def _run_replay_once(req: ReplayRequest) -> ReplayResult:
    s = get_settings()
    net = req.network
    fork_src = net.archive_rpc if req.block_number is not None else (net.rpc_http or net.archive_rpc)
    cap = {"archive_rpc": bool(net.archive_rpc), "http_rpc": bool(net.rpc_http), "anvil": s.anvil_rpc_url}
    base_assumptions = [
        "fork at the block boundary; intra-block transaction ordering is not reproduced",
        "simulator funded from the reserve aToken (impersonated) instead of a flash loan; premium added analytically",
        "swap proceeds are quoted with Uniswap v3 QuoterV2 on the fork, not executed",
        f"gas = measured liquidationCall gas + quoted swap gas + {FLASH_LOAN_OVERHEAD_GAS} modeled flash-loan overhead",
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
    # 1. eligibility at the fork block (state of N-1, as first observable)
    acct = reader.user_account_data(req.borrower)
    hf = Decimal(acct.health_factor_wad) / Decimal(WAD) if acct.health_factor_wad < 2**256 - 1 else Decimal("Infinity")
    eligible = acct.total_debt_base > 0 and acct.health_factor_wad < WAD
    # 1b. eligibility at execution: advance an empty block to the historical block's timestamp (interest accrues,
    #     no block-N transactions applied) and read again. This separates "observable at N-1" from "liquidatable
    #     when our transaction would execute".
    exec_hf, exec_eligible = None, None
    if req.block_number is not None:
        try:
            src = RpcClient(f"{net.slug}-archive", fork_src, s.rpc_timeout_seconds, s.rpc_max_retries)
            hist_blk = src.call(src.w3.eth.get_block, req.block_number)
            anvil.rpc("evm_setNextBlockTimestamp", [int(hist_blk["timestamp"])])
            anvil.mine(1)
            acct2 = reader.user_account_data(req.borrower)
            exec_hf = Decimal(acct2.health_factor_wad) / Decimal(WAD) if acct2.health_factor_wad < 2**256 - 1 else Decimal("Infinity")
            exec_eligible = acct2.total_debt_base > 0 and acct2.health_factor_wad < WAD
        except Exception as e:  # recorded, never guessed
            warnings.append(f"eligibility at execution timestamp not determined: {str(e)[:100]}")
    if not eligible and exec_eligible:
        warnings.append("not liquidatable at N-1 but liquidatable at block N's timestamp before its transactions (interest accrual)")
    elif not eligible:
        warnings.append("borrower not liquidatable at the fork block (N-1): the historical liquidation depended on intra-block state")
    sim = anvil.accounts()[0]
    anvil.set_balance(sim, 100 * 10**18)
    prices = reader.asset_prices([req.debt_asset, req.collateral_asset, NATIVE_PROXY[net.chain_id]])
    dp = prices[Web3.to_checksum_address(req.debt_asset)]
    native_p = prices[Web3.to_checksum_address(NATIVE_PROXY[net.chain_id])]
    gas_price = req.gas_price_wei if req.gas_price_wei is not None else int(w3.eth.gas_price)
    if req.gas_price_wei is None:
        warnings.append("gas priced at the fork node's gas price, not the original block's")
    premium_bps, _ = reader.flashloan_premium_bps()
    debt_token = anvil.erc20(req.debt_asset)
    coll_token = anvil.erc20(req.collateral_asset)
    d_dec = int(debt_token.functions.decimals().call())
    c_dec = int(coll_token.functions.decimals().call())
    quoter = UniswapV3Quoter(net.chain_id, client)

    # 2a. preferred path: the real flash-loan receiver on the fork
    if not req.force_direct_path:
        try:
            snap = anvil.snapshot()
            same_asset = req.collateral_asset.lower() == req.debt_asset.lower()
            if same_asset:  # the receiver skips the swap; the seized collateral already is the debt asset
                fee_tier, out_min = 0, 0
                warnings.append("same-asset liquidation: no swap executed; swapOut = collateral received")
            else:
                pre = quoter.quote_exact_in(req.collateral_asset, req.debt_asset, _expected_collateral(reader, req, prices, c_dec, d_dec))
                fee_tier = pre.fee_tier or 500
                out_min = (pre.amount_out_raw * (10_000 - req.slippage_bps) // 10_000) if pre.amount_out_raw else 0
            rr = run_receiver_liquidation(anvil, sim, net.market.addresses_provider, net.chain_id, req.collateral_asset, req.debt_asset,
                                          req.borrower, req.debt_to_cover_raw, fee_tier, out_min)
            l1 = estimate_l1_fee(net.chain_id, client, rr.receiver_address, bytes.fromhex(rr.calldata[2:]), int(rr.gas_used or 0) or 1, gas_price)
            fees = FeeAccounting(int(rr.gas_used or 0) * gas_price, l1.l1_data_wei, l1.source if rr.gas_used else "unknown", l1.method, l1.notes)
            assumptions = base_assumptions[:1] + [
                "real flash-loan receiver executed on the fork: premium, swap output, gas and surplus are measured, not modeled",
                f"swap routed through Uniswap SwapRouter02 fee tier {fee_tier} with amountOutMinimum from a QuoterV2 pre-quote",
                "L1 data fee is an estimate from the chain's fee oracle at the fork block",
            ]
            if rr.status != 1:
                return ReplayResult(False, rr.revert_reason or "receiver liquidation reverted", fork_block, fork_hash, True, str(hf), eligible,
                                    None, None, None, None, rr.gas_used, gas_price, None, None, None, rr.calldata,
                                    trace_summary=[c.__dict__ for c in anvil.trace(rr.tx_hash)] if rr.tx_hash else [], warnings=warnings,
                                    assumptions=assumptions, rpc_capability=cap, funding_path="aave_flash_loan",
                                    execution_path="flash_loan_receiver", fees=fees.as_dict(), eligible_at_execution=exec_eligible,
                                    health_factor_at_execution=None if exec_hf is None else str(exec_hf))
            ev = rr.event or {}
            swap_out = int(ev["swapOut"]) if "swapOut" in ev else None
            repaid = int(ev["debtToCover"]) if "debtToCover" in ev else None
            seized = int(ev["collateralReceived"]) if "collateralReceived" in ev else None
            premium = int(ev["premium"]) if "premium" in ev else None
            surplus = int(ev["surplus"]) if "surplus" in ev else None
            rec = reconcile_profit(swap_out_raw=swap_out, repaid_raw=repaid, premium_raw=premium, event_surplus_raw=surplus,
                                   gas_used=rr.gas_used, gas_price_wei=gas_price, l1_data_wei=l1.l1_data_wei, l1_source=l1.source,
                                   native_price_raw=native_p, debt_price_raw=dp, debt_decimals=d_dec,
                                   l1_required=l1.source != "not_applicable")
            if not rec.complete:
                warnings.append("profit INCOMPLETE: " + "; ".join(rec.missing))
            if rec.surplus_matches_event is False:
                warnings.append("event surplus does not equal swapOut − repaid − premium; reported surplus is the event value")
            return ReplayResult(True, None, fork_block, fork_hash, True, str(hf), eligible, repaid, seized, swap_out, premium, rr.gas_used, gas_price,
                                rec.fee_usd, rec.net_profit_debt_raw, rec.net_profit_usd, rr.calldata, trace_summary=[c.__dict__ for c in anvil.trace(rr.tx_hash)],
                                warnings=warnings, assumptions=assumptions, rpc_capability=cap, funding_path="aave_flash_loan",
                                execution_path="flash_loan_receiver", fees=fees.as_dict(), eligible_at_execution=exec_eligible,
                                health_factor_at_execution=None if exec_hf is None else str(exec_hf), surplus_raw=rec.surplus_raw,
                                profit_complete=rec.complete, reconciliation=rec.as_dict())
        except ReceiverArtifactMissing as e:
            warnings.append(f"simplified path: {e}")
        except Exception as e:
            warnings.append(f"simplified path: receiver path failed ({type(e).__name__}: {str(e)[:120]})")
        with contextlib.suppress(Exception):
            anvil.revert(snap)

    # 2b. simplified path: funding from the aToken of the debt reserve (labeled)
    warnings.append("SIMPLIFIED SIMULATION: direct liquidationCall with impersonated funding; swap quoted, premium analytical")
    a_token, _, _ = reader.reserve_tokens(req.debt_asset)
    try:
        anvil.fund_erc20_from(req.debt_asset, a_token, sim, req.debt_to_cover_raw)
    except AnvilUnavailable as e:
        return ReplayResult(False, f"funding failed: {e}", fork_block, fork_hash, True, str(hf), eligible, None, None, None, None, None,
                            None, None, None, None, None, warnings=warnings, assumptions=base_assumptions, rpc_capability=cap)
    debt_token.functions.approve(pool_addr, req.debt_to_cover_raw).transact({"from": sim})
    coll_before = coll_token.functions.balanceOf(sim).call()
    debt_before = debt_token.functions.balanceOf(sim).call()
    # 3. the real liquidationCall
    fn = pool.functions.liquidationCall(Web3.to_checksum_address(req.collateral_asset), Web3.to_checksum_address(req.debt_asset),
                                        Web3.to_checksum_address(req.borrower), req.debt_to_cover_raw, False)
    calldata = fn._encode_transaction_data()
    rc = anvil.send(sim, pool_addr, calldata)
    premium = percent_mul(req.debt_to_cover_raw, premium_bps)
    if rc.get("status") != 1:
        return ReplayResult(False, rc.get("revert_reason") or "liquidationCall reverted", fork_block, fork_hash, True, str(hf), eligible,
                            None, None, None, premium, rc.get("gasUsed"), gas_price, None, None, None, calldata,
                            trace_summary=[c.__dict__ for c in anvil.trace(rc["transactionHash"])] if rc.get("transactionHash") else [],
                            warnings=warnings, assumptions=base_assumptions, rpc_capability=cap, execution_path="direct_liquidation_call")
    # 4. results from the event and balances
    seized = coll_token.functions.balanceOf(sim).call() - coll_before
    repaid = debt_before - debt_token.functions.balanceOf(sim).call()
    # 5. swap quote on the fork
    q = quoter.quote_exact_in(req.collateral_asset, req.debt_asset, seized)
    swap_out = q.amount_out_raw
    if q.status != "ok":
        warnings.append(f"swap quote unavailable on fork: {q.error}")
    # measured liquidationCall gas + quoted swap gas + flash-loan overhead (modeled constant)
    gas_used = int(rc["gasUsed"]) + (q.gas_estimate or 0) + FLASH_LOAN_OVERHEAD_GAS
    if q.gas_estimate is None:
        warnings.append("swap gas not quoted; gas figure excludes the swap")
    l1 = estimate_l1_fee(net.chain_id, client, pool_addr, bytes.fromhex(calldata[2:]) + b"\x00" * 160, gas_used, gas_price)
    fees = FeeAccounting(gas_used * gas_price, l1.l1_data_wei, l1.source, l1.method, l1.notes)
    rec = reconcile_profit(swap_out_raw=swap_out, repaid_raw=repaid, premium_raw=premium, event_surplus_raw=None, gas_used=gas_used,
                           gas_price_wei=gas_price, l1_data_wei=l1.l1_data_wei, l1_source=l1.source, native_price_raw=native_p,
                           debt_price_raw=dp, debt_decimals=d_dec, l1_required=l1.source != "not_applicable")
    gas_usd, net_raw, net_usd = rec.fee_usd, rec.net_profit_debt_raw, rec.net_profit_usd
    if not rec.complete:
        warnings.append("profit INCOMPLETE: " + "; ".join(rec.missing))
    if swap_out is not None and net_usd is not None:
        if req.min_profit_usd and net_usd < req.min_profit_usd:
            warnings.append(f"net profit {net_usd} below requested minimum {req.min_profit_usd}")
        cp = prices[Web3.to_checksum_address(req.collateral_asset)]
        impact = price_impact_bps(seized, c_dec, cp, swap_out, d_dec, dp)
        if impact > req.slippage_bps:
            warnings.append(f"quoted price impact {impact} bps exceeds slippage limit {req.slippage_bps} bps")
    trace = [c.__dict__ for c in anvil.trace(rc["transactionHash"])]
    return ReplayResult(True, None, fork_block, fork_hash, True, str(hf), eligible, repaid, seized, swap_out, premium, gas_used, gas_price,
                        gas_usd, net_raw, net_usd, calldata, trace_summary=trace, warnings=warnings, assumptions=base_assumptions,
                        rpc_capability=cap, execution_path="direct_liquidation_call", fees=fees.as_dict(), eligible_at_execution=exec_eligible,
                        health_factor_at_execution=None if exec_hf is None else str(exec_hf), surplus_raw=rec.surplus_raw,
                        profit_complete=rec.complete, reconciliation=rec.as_dict())


def _expected_collateral(reader: AaveReader, req: ReplayRequest, prices: dict, c_dec: int, d_dec: int) -> int:
    """Rough collateral amount for the pre-quote (bonus from reserve config); the receiver swaps the real amount."""
    cfg = reader.reserve_config(req.collateral_asset)
    dp = prices[Web3.to_checksum_address(req.debt_asset)]
    cp = prices[Web3.to_checksum_address(req.collateral_asset)]
    base = req.debt_to_cover_raw * dp * 10**c_dec // (cp * 10**d_dec)
    return percent_mul(base, cfg.liquidation_bonus_bps or 10_000)
