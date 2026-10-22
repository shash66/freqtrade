"""Intra-block reconstruction of a historical liquidation (research only, private Anvil fork).

Replay at the block boundary (state of N-1) cannot tell *when* inside block N a position became liquidatable.
This module forks at N-1, pins the timestamp of block N, then re-executes block N's transactions one by one in
their original order by impersonating each sender, reading the borrower's health factor and the oracle prices of
the two reserves after every transaction. The output names the transaction that first made the position
liquidatable ("trigger"), classifies it (oracle update / borrower action / other), locates the winning
liquidation inside the block, and optionally executes our flash-loan receiver at exactly that position.

Fidelity limits (recorded in the result as assumptions):
- System/deposit transactions (type 0x7e on OP-stack chains) cannot be re-sent and are skipped; they only set
  L1 attributes, which do not affect Aave state.
- Every replayed transaction is mined in its own fork block with base fee 0 and gas price 0, so contracts that
  read block.number, basefee or tx.gasprice see different values than on chain. Aave accounting depends on the
  timestamp, which is pinned to block N (+1 s per replayed transaction).
- A transaction whose status on the fork differs from the on-chain receipt is reported; the reconstruction is
  then approximate from that index on.
"""
from __future__ import annotations

import argparse
import contextlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from web3 import Web3

from app.aave.reads import AaveReader
from app.chains.config import NetworkConfig, build_networks
from app.chains.rpc import RpcClient
from app.chains.validation import load_abi
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.money import WAD, percent_mul
from app.simulation.anvil import AnvilFork
from app.simulation.receiver import run_receiver_liquidation
from app.strategies.quotes import UniswapV3Quoter

log = get_logger(__name__)

DEPOSIT_TX_TYPES = {"0x7e", 126}


def _sig_topic(signature: str) -> str:
    h = Web3.keccak(text=signature).hex()
    return (h if h.startswith("0x") else "0x" + h).lower()


CHAINLINK_TOPICS = {
    _sig_topic("AnswerUpdated(int256,uint256,uint256)"): "AnswerUpdated",
    _sig_topic("NewTransmission(uint32,int192,address,uint32,int192[],bytes,int192,bytes32,uint40)"): "NewTransmission(OCR2)",
    _sig_topic("NewTransmission(uint32,int192,address,int192[],bytes,bytes32)"): "NewTransmission(OCR1)",
}
POOL_EVENTS = {"Supply", "Withdraw", "Borrow", "Repay", "LiquidationCall", "ReserveUsedAsCollateralDisabled",
               "ReserveUsedAsCollateralEnabled", "UserEModeSet", "FlashLoan"}


def _topic(t) -> str:
    h = t.hex() if hasattr(t, "hex") else str(t)
    return h if h.startswith("0x") else "0x" + h


def pool_event_topics() -> dict[str, str]:
    """topic0 -> event name for the Aave Pool events we attribute borrower actions with."""
    out = {}
    for item in load_abi("pool"):
        if item.get("type") == "event" and item["name"] in POOL_EVENTS:
            out[_sig_topic(f"{item['name']}({','.join(i['type'] for i in item['inputs'])})")] = item["name"]
    return out


def classify_tx(logs: list[dict], pool: str, borrower: str, pool_topics: dict[str, str]) -> tuple[str, list[str]]:
    """Classify a transaction from its (on-chain) receipt logs. Pure function, unit-tested.

    Returns (kind, evidence). kind is one of: oracle_update, borrower_action, liquidation_of_borrower,
    other_liquidation, pool_activity, other."""
    evidence: list[str] = []
    kinds: set[str] = set()
    b_topic = "0x" + borrower[2:].lower().rjust(64, "0")
    for lg in logs:
        topics = [_topic(t).lower() for t in lg.get("topics", [])]
        if not topics:
            continue
        t0 = topics[0]
        if t0 in CHAINLINK_TOPICS:
            kinds.add("oracle_update")
            evidence.append(f"{CHAINLINK_TOPICS[t0]} from {lg['address']}")
        elif t0 in pool_topics and str(lg["address"]).lower() == pool.lower():
            name = pool_topics[t0]
            touches = b_topic in topics[1:]
            if name == "LiquidationCall":
                kinds.add("liquidation_of_borrower" if touches else "other_liquidation")
            elif touches:
                kinds.add("borrower_action")
            else:
                kinds.add("pool_activity")
            evidence.append(f"Pool.{name}{' (borrower)' if touches else ''}")
    for k in ("liquidation_of_borrower", "borrower_action", "oracle_update", "other_liquidation", "pool_activity"):
        if k in kinds:
            return k, evidence
    return "other", evidence


@dataclass
class TxStep:
    index: int
    tx_hash: str
    sender: str
    to: str | None
    kind: str
    evidence: list[str]
    status_onchain: int | None
    status_fork: int | None
    health_factor: str | None
    eligible: bool | None
    collateral_price: int | None
    debt_price: int | None
    note: str | None = None


@dataclass
class Reconstruction:
    network: str
    block_number: int
    block_timestamp: int
    fork_block_hash: str | None
    tx_count: int
    replayed: int
    skipped_system_txs: int
    skipped_irrelevant_txs: int
    status_mismatches: int
    health_factor_at_boundary: str | None
    eligible_at_boundary: bool | None
    health_factor_at_timestamp: str | None
    eligible_at_timestamp: bool | None
    prices_at_boundary: dict
    trigger: dict | None
    liquidation_tx_index: int | None
    liquidation_tx_hash: str | None
    txs_between_trigger_and_liquidation: int | None
    health_factor_before_liquidation: str | None
    eligible_before_liquidation: bool | None
    prices_before_liquidation: dict
    changes: list[TxStep] = field(default_factory=list)      # only steps where HF or a price changed, plus key txs
    simulated_at_position: dict | None = None
    oracle_time_gate: dict | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _touches(rc: dict | None, addrs: set[str]) -> bool:
    if not rc:
        return False
    for lg in rc.get("logs", []):
        if str(lg.get("address", "")).lower() in addrs:
            return True
        topics = [_topic(t).lower() for t in lg.get("topics", [])]
        if topics and topics[0] in CHAINLINK_TOPICS:
            return True
    return False


def probe_time_gate(reader: AaveReader, anvil: AnvilFork, assets: list[str], base_ts: int, max_seconds: int = 120, step: int = 5) -> dict:
    """Detect time-gated oracle rounds: on a fork at N-1 with no transactions applied, advance only the block
    timestamp and watch the Aave oracle prices. A price that changes with time alone means the feed already
    holds a newer round at N-1 that it only reveals after a delay (Chainlink SVR-style dual aggregators). The
    probe runs on a snapshot and reverts; it never touches the replay state."""
    snap = anvil.snapshot()
    try:
        cs = [Web3.to_checksum_address(a) for a in assets]
        p0 = reader.asset_prices(cs)
        out = {a: {"price_at_boundary": p0[a], "revealed_price": None, "reveal_after_seconds": None} for a in cs}
        for extra in range(0, max_seconds + 1, step):
            anvil.rpc("evm_setNextBlockTimestamp", [base_ts + extra])
            anvil.mine(1)
            p = reader.asset_prices(cs)
            for a in cs:
                if out[a]["reveal_after_seconds"] is None and p[a] != p0[a]:
                    out[a].update({"revealed_price": p[a], "reveal_after_seconds": extra})
            if all(v["reveal_after_seconds"] is not None for v in out.values()):
                break
        return {"base_timestamp": base_ts, "max_seconds": max_seconds, "step": step, "assets": out,
                "note": "price change with no transaction applied = pending oracle round hidden behind a time gate at N-1"}
    finally:
        anvil.revert(snap)


def _hf(acct) -> tuple[str, bool]:
    hf = Decimal(acct.health_factor_wad) / Decimal(WAD) if acct.health_factor_wad < 2**256 - 1 else Decimal("Infinity")
    return str(hf), acct.total_debt_base > 0 and acct.health_factor_wad < WAD


def reconstruct_block(net: NetworkConfig, block_number: int, borrower: str, collateral: str, debt: str, *, anvil_url: str,
                      archive_rpc: str, liquidation_tx: str | None = None, debt_to_cover_raw: int | None = None,
                      simulate_at_position: bool = False, stop_after_liquidation: bool = True, relevant_only: bool = False) -> Reconstruction:
    s = get_settings()
    src = RpcClient(f"{net.slug}-archive", archive_rpc, s.rpc_timeout_seconds, s.rpc_max_retries)
    blk = src.call(src.w3.eth.get_block, block_number, True)
    txs = list(blk["transactions"])
    ts = int(blk["timestamp"])
    receipts: dict[str, dict] = {}
    try:
        rs = src.call(lambda: src.w3.provider.make_request("eth_getBlockReceipts", [hex(block_number)]))
        for r in rs.get("result") or []:
            receipts[_topic(r["transactionHash"]).lower()] = r
    except Exception as e:  # noqa: BLE001 - classification then falls back to fork-side logs
        log.warning("block_receipts_unavailable", error=str(e)[:120])

    anvil = AnvilFork(anvil_url, timeout=300)
    info = anvil.reset_fork(archive_rpc, block_number - 1)
    anvil.rpc("evm_setNextBlockTimestamp", [ts])
    anvil.mine(1)
    anvil.rpc("anvil_setBlockTimestampInterval", [1])
    client = RpcClient(f"{net.slug}-anvil", anvil_url, 300.0, 1)
    reader = AaveReader(net, client)
    pool_topics = pool_event_topics()
    coll, dbt = Web3.to_checksum_address(collateral), Web3.to_checksum_address(debt)
    relevant_addrs: set[str] = {net.market.pool.lower(), net.market.oracle.lower()}
    if relevant_only:
        if not receipts:
            raise RuntimeError("relevant-only mode needs eth_getBlockReceipts from the archive provider")
        for a in reader.reserves_list(block=block_number - 1):
            relevant_addrs.add(a.lower())
            with contextlib.suppress(Exception):
                relevant_addrs.update(x.lower() for x in reader.reserve_tokens(a, block=block_number - 1))

    def observe():
        acct = reader.user_account_data(borrower)
        hf, elig = _hf(acct)
        p = reader.asset_prices([coll, dbt])
        return hf, elig, p[coll], p[dbt]

    # boundary (N-1) then at N's timestamp before any tx
    hf0, el0 = _hf(reader.user_account_data(borrower, block=block_number - 1))
    p0 = reader.asset_prices([coll, dbt], block=block_number - 1)
    hf_ts, el_ts, cp, dp = observe()

    rec = Reconstruction(net.slug, block_number, ts, info.get("block_hash"), len(txs), 0, 0, 0, 0, hf0, el0, hf_ts, el_ts,
                         {"collateral": p0[coll], "debt": p0[dbt]}, None, None, None, None, None, None, {})
    rec.assumptions = [
        "fork at N-1 with block N's timestamp pinned; each replayed transaction mined in its own block (+1 s)",
    ] + (["relevant-only mode: only transactions whose on-chain receipt logs touch the Pool, the oracle, a reserve token or a Chainlink "
          "feed are replayed; other transactions cannot change this position's health factor or prices and are skipped"] if relevant_only else []) + [
        "replayed transactions use base fee 0 and gas price 0 so senders need no ETH for gas; value transfers are real",
        "OP-stack system (deposit) transactions are skipped: they set L1 attributes only",
        "classification uses the on-chain receipt logs of block N; health factor and prices are read from the fork after each transaction",
    ]
    try:
        rec.oracle_time_gate = probe_time_gate(reader, anvil, [coll, dbt], ts)
        for a, v in rec.oracle_time_gate["assets"].items():
            if v["reveal_after_seconds"] is not None:
                rec.warnings.append(f"oracle for {a} reveals a newer round {v['reveal_after_seconds']} s after block N's timestamp with no transaction: "
                                    f"time-gated feed (price {v['price_at_boundary']} -> {v['revealed_price']})")
    except Exception as e:  # noqa: BLE001
        rec.warnings.append(f"time-gate probe failed: {str(e)[:100]}")
    liq_hash = liquidation_tx.lower() if liquidation_tx else None
    prev = (hf_ts, cp, dp)
    trigger_seen = False
    for i, tx in enumerate(txs):
        h = _topic(tx["hash"]).lower()
        ttype = tx.get("type")
        rc_chain = receipts.get(h)
        status_chain = int(rc_chain["status"], 16) if rc_chain and isinstance(rc_chain.get("status"), str) else (rc_chain.get("status") if rc_chain else None)
        kind, evidence = classify_tx(rc_chain.get("logs", []) if rc_chain else [], net.market.pool, borrower, pool_topics)
        if ttype in DEPOSIT_TX_TYPES or str(ttype).lower() == "0x7e":
            rec.skipped_system_txs += 1
            continue
        is_liq = liq_hash is not None and h == liq_hash
        if relevant_only and not is_liq and not _touches(rc_chain, relevant_addrs):
            rec.skipped_irrelevant_txs += 1
            continue
        if is_liq and simulate_at_position and debt_to_cover_raw:
            rec.simulated_at_position = _simulate_before(anvil, net, client, reader, borrower, coll, dbt, debt_to_cover_raw, i)
        sender = Web3.to_checksum_address(tx["from"])
        anvil.impersonate(sender)
        anvil.rpc("anvil_setNextBlockBaseFeePerGas", [0])
        call = {"from": sender, "value": int(tx.get("value", 0)), "gas": int(tx["gas"]), "gasPrice": 0,
                "data": _topic(tx.get("input") or tx.get("data") or "0x")}
        if tx.get("to"):
            call["to"] = Web3.to_checksum_address(tx["to"])
        status_fork, note = None, None
        try:
            th = anvil.w3.eth.send_transaction(call)
            r = anvil.w3.eth.wait_for_transaction_receipt(th, timeout=300)
            status_fork = int(r["status"])
        except Exception as e:  # noqa: BLE001 - recorded per tx; a dropped tx makes later state approximate
            note = f"fork send failed: {str(e)[:120]}"
            status_fork = 0
        rec.replayed += 1
        if status_chain is not None and status_fork != status_chain:
            rec.status_mismatches += 1
            note = (note or "") + f" status fork={status_fork} chain={status_chain}"
        hf, elig, cpx, dpx = observe()
        changed = (hf, cpx, dpx) != prev
        if (cpx, dpx) != (prev[1], prev[2]) and kind in ("other", "pool_activity"):
            gate = rec.oracle_time_gate["assets"] if rec.oracle_time_gate else {}
            gated = [a for a, v in gate.items() if v.get("reveal_after_seconds") is not None and v.get("revealed_price") in (cpx, dpx)]
            if gated:
                kind = "time_gated_oracle_reveal"
                evidence = evidence + [f"price change matches the time-gated round of {', '.join(gated)}; fork timestamp {ts + rec.replayed + 1} (block N + {rec.replayed + 1} s); not caused by this transaction"]
            else:
                emitters = sorted({str(lg.get("address")) for lg in (rc_chain.get("logs", []) if rc_chain else [])})
                kind = "oracle_update_by_price_change"
                evidence = evidence + [f"collateral price {prev[1]} -> {cpx}" if cpx != prev[1] else f"debt price {prev[2]} -> {dpx}",
                                       "log emitters: " + ", ".join(emitters[:6])]
        if is_liq:
            rec.liquidation_tx_index, rec.liquidation_tx_hash = i, h
            rec.health_factor_before_liquidation, rec.eligible_before_liquidation = prev[0], Decimal(prev[0]) < 1 if prev[0] not in (None, "Infinity") else False
            rec.prices_before_liquidation = {"collateral": prev[1], "debt": prev[2]}
        if changed or is_liq or kind in ("borrower_action", "liquidation_of_borrower") or note:
            rec.changes.append(TxStep(i, h, sender, tx.get("to"), kind, evidence, status_chain, status_fork, hf, elig, cpx, dpx, note))
        if elig and not trigger_seen and not is_liq:
            trigger_seen = True
            rec.trigger = {"index": i, "tx_hash": h, "kind": kind, "evidence": evidence, "sender": sender, "to": tx.get("to"),
                           "health_factor_after": hf, "health_factor_before": prev[0],
                           "collateral_price_before": prev[1], "collateral_price_after": cpx,
                           "debt_price_before": prev[2], "debt_price_after": dpx}
        if is_liq:
            if rec.trigger is not None:
                rec.txs_between_trigger_and_liquidation = i - rec.trigger["index"] - 1
            if stop_after_liquidation:
                break
        prev = (hf, cpx, dpx)
    if el_ts and rec.trigger is None:
        rec.trigger = {"index": -1, "kind": "already_eligible_at_timestamp", "evidence": [],
                       "health_factor_after": hf_ts, "health_factor_before": hf0}
        rec.txs_between_trigger_and_liquidation = rec.liquidation_tx_index
    if liq_hash and rec.liquidation_tx_index is None:
        rec.warnings.append("liquidation transaction not found in block N")
    if rec.status_mismatches:
        rec.warnings.append(f"{rec.status_mismatches} transaction(s) had a different status on the fork; state after them is approximate")
    return rec


def _simulate_before(anvil: AnvilFork, net: NetworkConfig, client: RpcClient, reader: AaveReader, borrower: str, coll: str, dbt: str,
                     debt_to_cover: int, position: int) -> dict:
    """Execute our receiver on a snapshot of the state right before the winning liquidation (index `position`)."""
    snap = anvil.snapshot()
    try:
        sim = anvil.accounts()[0]
        anvil.set_balance(sim, 100 * 10**18)
        prices = reader.asset_prices([coll, dbt])
        cfg = reader.reserve_config(coll)
        c_dec = int(anvil.erc20(coll).functions.decimals().call())
        d_dec = int(anvil.erc20(dbt).functions.decimals().call())
        base = debt_to_cover * prices[dbt] * 10**c_dec // (prices[coll] * 10**d_dec)
        q = UniswapV3Quoter(net.chain_id, client).quote_exact_in(coll, dbt, percent_mul(base, cfg.liquidation_bonus_bps or 10_000))
        out_min = (q.amount_out_raw * 9_950 // 10_000) if q.amount_out_raw else 0
        rr = run_receiver_liquidation(anvil, sim, net.market.addresses_provider, net.chain_id, coll, dbt, borrower, debt_to_cover,
                                      q.fee_tier or 500, out_min)
        ev = rr.event or {}
        return {"position": position, "status": rr.status, "revert_reason": rr.revert_reason, "gas_used": rr.gas_used,
                "surplus_raw": ev.get("surplus"), "swap_out_raw": ev.get("swapOut"), "repaid_raw": ev.get("debtToCover"),
                "collateral_received_raw": ev.get("collateralReceived"), "premium_raw": ev.get("premium"),
                "note": "state immediately before the winning liquidation inside block N (not the block boundary)"}
    except Exception as e:  # noqa: BLE001
        return {"position": position, "status": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    finally:
        anvil.revert(snap)


def time_gate_only(net: NetworkConfig, block_number: int, collateral: str, debt: str, *, anvil_url: str, archive_rpc: str) -> dict:
    s = get_settings()
    src = RpcClient(f"{net.slug}-archive", archive_rpc, s.rpc_timeout_seconds, s.rpc_max_retries)
    ts = int(src.call(src.w3.eth.get_block, block_number)["timestamp"])
    anvil = AnvilFork(anvil_url, timeout=300)
    anvil.reset_fork(archive_rpc, block_number - 1)
    reader = AaveReader(net, RpcClient(f"{net.slug}-anvil", anvil_url, 300.0, 1))
    out = probe_time_gate(reader, anvil, [collateral, debt], ts)
    out.update({"network": net.slug, "block_number": block_number})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconstruct intra-block state for a historical liquidation on a private Anvil fork")
    ap.add_argument("--network", default="base")
    ap.add_argument("--block", type=int, required=True)
    ap.add_argument("--borrower", required=True)
    ap.add_argument("--collateral", required=True)
    ap.add_argument("--debt", required=True)
    ap.add_argument("--tx", default=None, help="historical liquidation tx hash")
    ap.add_argument("--debt-to-cover", type=int, default=None)
    ap.add_argument("--anvil", default=None)
    ap.add_argument("--simulate", action="store_true", help="also execute our receiver right before the winning liquidation")
    ap.add_argument("--relevant-only", action="store_true", help="replay only transactions whose logs touch Aave or Chainlink contracts")
    ap.add_argument("--time-gate-only", action="store_true", help="only probe for time-gated oracle rounds at N-1 (no transaction replay)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    net = build_networks(s)[a.network]
    if not net.archive_rpc:
        raise SystemExit("archive RPC required")
    if a.time_gate_only:
        out = time_gate_only(net, a.block, a.collateral, a.debt, anvil_url=a.anvil or s.anvil_rpc_url, archive_rpc=net.archive_rpc)
        print(json.dumps(out, indent=1, default=str))
        if a.out:
            with open(a.out, "w") as f:
                json.dump(out, f, indent=1, default=str)
        return 0
    rec = reconstruct_block(net, a.block, a.borrower, a.collateral, a.debt, anvil_url=a.anvil or s.anvil_rpc_url, archive_rpc=net.archive_rpc,
                            liquidation_tx=a.tx, debt_to_cover_raw=a.debt_to_cover, simulate_at_position=a.simulate, relevant_only=a.relevant_only)
    out = json.dumps(rec.as_dict(), indent=1, default=str)
    print(out)
    if a.out:
        with open(a.out, "w") as f:
            f.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
