"""Competitive shadow benchmark (read-only, live chain, no transaction path).

Measures, with a monotonic clock, what a liquidator built on this stack would actually experience:

- block observation lag: wall-clock time between a block's timestamp and the moment this process first saw it
  (includes sequencer clock skew, propagation and the polling interval; reported as such, never as "latency
  to the sequencer");
- detection latency: monotonic time from first seeing a new head to having re-read the health factor of every
  watched position (one Multicall3 round trip);
- simulation latency: monotonic time to fork at the head on a private Anvil and execute the flash-loan receiver
  for the most urgent candidate (optional, needs --anvil);
- candidate lifetime: for each position that became liquidatable while we watched, the number of blocks until it
  was liquidated on-chain (by whom, at what gas price, at which index in the block) or healed;
- observed competitor transactions: every LiquidationCall on the Pool during the window, with whether the
  borrower was in our watch set and how many blocks our detection preceded (or trailed) the competitor.

Everything is written to a JSON file; nothing is modeled here. Modeled numbers live in app.competition.montecarlo.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from web3 import Web3

from app.aave.reads import AaveReader
from app.chains.config import NetworkConfig, build_networks
from app.chains.rpc import RpcClient
from app.chains.validation import load_abi
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.money import WAD
from app.database import models as m
from app.database.base import get_sessionmaker

log = get_logger(__name__)

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
MULTICALL3_ABI = [{"inputs": [{"components": [{"name": "target", "type": "address"}, {"name": "allowFailure", "type": "bool"},
                                              {"name": "callData", "type": "bytes"}], "name": "calls", "type": "tuple[]"}],
                   "name": "aggregate3", "outputs": [{"components": [{"name": "success", "type": "bool"}, {"name": "returnData", "type": "bytes"}],
                                                      "name": "returnData", "type": "tuple[]"}], "stateMutability": "payable", "type": "function"}]


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    return ys[min(len(ys) - 1, int(round(q * (len(ys) - 1))))]


@dataclass
class Candidate:
    borrower: str
    first_eligible_block: int
    first_eligible_seen_monotonic: float
    health_factor: str
    total_debt_base: int
    status: str = "open"                 # open | liquidated | healed
    end_block: int | None = None
    liquidation_tx: str | None = None
    liquidator: str | None = None
    blocks_alive: int | None = None
    simulation: dict | None = None


@dataclass
class CompetitorTx:
    block_number: int
    tx_hash: str
    tx_index: int | None
    log_index: int
    liquidator: str
    borrower: str
    collateral_asset: str
    debt_asset: str
    debt_to_cover_raw: int
    collateral_seized_raw: int
    gas_used: int | None
    effective_gas_price_wei: int | None
    max_priority_fee_wei: int | None
    in_watch_set: bool
    our_first_eligible_block: int | None
    blocks_after_our_detection: int | None    # negative/None = we never flagged it before the competitor did
    note: str | None = None


@dataclass
class BenchmarkResult:
    network: str
    started_at: str
    duration_seconds: float
    rpc: str
    anvil: str | None
    watch_set_size: int
    watch_hf_max: str
    blocks_observed: int
    first_block: int | None
    last_block: int | None
    block_lag_wallclock_seconds: dict
    detection_latency_seconds: dict
    multicall_batch_size: int
    simulation_latency_seconds: dict
    simulations: list[dict]
    candidates: list[Candidate]
    competitor_txs: list[CompetitorTx]
    summary: dict
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class WatchSet:
    def __init__(self, net: NetworkConfig, client: RpcClient, addresses: list[str]):
        self.net = net
        self.client = client
        self.w3 = client.w3
        self.pool = self.w3.eth.contract(address=Web3.to_checksum_address(net.market.pool), abi=load_abi("pool"))
        self.mc = self.w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=MULTICALL3_ABI)
        self.addresses = [Web3.to_checksum_address(a) for a in addresses]

    def read_all(self, block: int | str = "latest", chunk: int = 150) -> tuple[dict[str, tuple[int, int]], int]:
        """borrower -> (health_factor_wad, total_debt_base); also returns the number of RPC round trips."""
        out: dict[str, tuple[int, int]] = {}
        trips = 0
        for i in range(0, len(self.addresses), chunk):
            part = self.addresses[i:i + chunk]
            calls = [(self.pool.address, True, self.pool.functions.getUserAccountData(a)._encode_transaction_data()) for a in part]
            res = self.client.call(self.mc.functions.aggregate3(calls).call, block_identifier=block)
            trips += 1
            for a, (ok, data) in zip(part, res, strict=True):
                if ok and len(data) >= 192:
                    vals = self.w3.codec.decode(["uint256"] * 6, data)
                    out[a] = (int(vals[5]), int(vals[1]))
        return out, trips


def load_watch_set(net: NetworkConfig, hf_max: Decimal) -> list[str]:
    limit = int(hf_max * WAD)
    with get_sessionmaker()() as db:
        rows = db.execute(select(m.Borrower.address, m.Borrower.health_factor_wad).where(m.Borrower.chain_id == net.chain_id,
                                                                                         m.Borrower.total_debt_base > 0)).all()
    return [a for a, hf in sorted(rows, key=lambda r: int(r[1] or 0)) if hf is not None and int(hf) < limit]


def _liquidation_logs(client: RpcClient, pool, from_block: int, to_block: int) -> list:
    ev = pool.events.LiquidationCall()
    return client.call(ev.get_logs, from_block=from_block, to_block=to_block)


def run_benchmark(net: NetworkConfig, *, minutes: float, hf_max: Decimal, anvil_url: str | None, sim_every_blocks: int,
                  poll_seconds: float, max_simulations: int, out_path: str | None) -> BenchmarkResult:
    if anvil_url:  # the replay engine takes its Anvil from settings; point it at the benchmark's private node
        import os
        os.environ["ANVIL_RPC_URL"] = anvil_url
        get_settings.cache_clear()
    s = get_settings()
    rpc_url = net.rpc_http or net.archive_rpc
    client = RpcClient(f"{net.slug}-bench", rpc_url, s.rpc_timeout_seconds, s.rpc_max_retries, min_interval_ms=0)
    w3 = client.w3
    reader = AaveReader(net, client)
    addresses = load_watch_set(net, hf_max)
    ws = WatchSet(net, client, addresses)
    started = datetime.now(UTC)
    t_end = time.monotonic() + minutes * 60
    lags: list[float] = []
    detects: list[float] = []
    sim_lat: list[float] = []
    sims: list[dict] = []
    cands: dict[str, Candidate] = {}
    comp: list[CompetitorTx] = []
    warnings: list[str] = []
    last_block = first_block = None
    blocks = 0
    trips_per_block = 0
    while time.monotonic() < t_end:
        t0 = time.monotonic()
        try:
            head = int(client.call(lambda: w3.eth.block_number))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"head poll failed: {str(e)[:80]}")
            time.sleep(poll_seconds)
            continue
        if last_block is not None and head <= last_block:
            time.sleep(poll_seconds)
            continue
        t_seen = time.monotonic()
        wall_seen = time.time()
        try:
            hdr = client.call(w3.eth.get_block, head)
            lags.append(wall_seen - int(hdr["timestamp"]))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"header read failed at {head}: {str(e)[:80]}")
        # 1. detection: re-read every watched position at this head
        try:
            states, trips = ws.read_all(block=head)
            trips_per_block = trips
        except Exception as e:  # noqa: BLE001
            warnings.append(f"multicall failed at {head}: {str(e)[:80]}")
            states = {}
        t_det = time.monotonic()
        detects.append(t_det - t_seen)
        if first_block is None:
            first_block = head
        # 2. candidate lifecycle
        for a, (hf, debt) in states.items():
            eligible = debt > 0 and hf < WAD
            c = cands.get(a)
            if eligible and c is None:
                cands[a] = Candidate(a, head, t_det, str(Decimal(hf) / Decimal(WAD)), debt)
                log.info("candidate_eligible", borrower=a, block=head, hf=cands[a].health_factor)
            elif not eligible and c is not None and c.status == "open":
                c.status, c.end_block, c.blocks_alive = "healed", head, head - c.first_eligible_block
        # 3. competitor transactions in the new block range
        lo = (last_block + 1) if last_block is not None else head
        try:
            for lg in _liquidation_logs(client, ws.pool, lo, head):
                args = lg["args"]
                borrower = Web3.to_checksum_address(args["user"])
                txh = lg["transactionHash"].hex() if hasattr(lg["transactionHash"], "hex") else str(lg["transactionHash"])
                txh = txh if txh.startswith("0x") else "0x" + txh
                gas_used = eff = prio = None
                try:
                    rc = client.call(w3.eth.get_transaction_receipt, txh)
                    tx = client.call(w3.eth.get_transaction, txh)
                    gas_used, eff = int(rc["gasUsed"]), int(rc["effectiveGasPrice"])
                    prio = int(tx.get("maxPriorityFeePerGas")) if tx.get("maxPriorityFeePerGas") is not None else None
                except Exception as e:  # noqa: BLE001
                    warnings.append(f"receipt read failed for {txh[:12]}: {str(e)[:60]}")
                c = cands.get(borrower)
                in_ws = borrower in ws.addresses
                comp.append(CompetitorTx(int(lg["blockNumber"]), txh, lg.get("transactionIndex"), int(lg["logIndex"]),
                                         Web3.to_checksum_address(args["liquidator"]), borrower, args["collateralAsset"], args["debtAsset"],
                                         int(args["debtToCover"]), int(args["liquidatedCollateralAmount"]), gas_used, eff, prio, in_ws,
                                         c.first_eligible_block if c else None,
                                         (int(lg["blockNumber"]) - c.first_eligible_block) if c else None,
                                         None if c else ("borrower in watch set but never read as eligible by us before the liquidation" if in_ws
                                                         else "borrower outside the watch set (HF above threshold at last reconcile or unknown)")))
                if c is not None and c.status == "open":
                    c.status, c.end_block, c.liquidation_tx, c.liquidator = "liquidated", int(lg["blockNumber"]), txh, Web3.to_checksum_address(args["liquidator"])
                    c.blocks_alive = int(lg["blockNumber"]) - c.first_eligible_block
        except Exception as e:  # noqa: BLE001
            warnings.append(f"log scan failed {lo}-{head}: {str(e)[:80]}")
        # 4. simulation latency probe on the most urgent position (open candidate, else lowest HF)
        if anvil_url and blocks % sim_every_blocks == 0 and len(sims) < max_simulations and states:
            target = next((a for a, c in cands.items() if c.status == "open"), None) or min(states, key=lambda a: states[a][0])
            r = _simulate_latency(net, anvil_url, rpc_url, reader, target, states[target])
            r.update({"block": head, "borrower": target})
            sims.append(r)
            if r.get("seconds") is not None:
                sim_lat.append(r["seconds"])
            if target in cands and cands[target].simulation is None:
                cands[target].simulation = r
        blocks += 1
        last_block = head
        spent = time.monotonic() - t0
        time.sleep(max(0.0, poll_seconds - spent))
    dur = (datetime.now(UTC) - started).total_seconds()

    def stats(xs: list[float]) -> dict:
        return {"n": len(xs), "median": _pct(xs, 0.5), "p90": _pct(xs, 0.9), "max": max(xs) if xs else None,
                "mean": statistics.fmean(xs) if xs else None}

    taken = [c for c in cands.values() if c.status == "liquidated"]
    res = BenchmarkResult(net.slug, started.isoformat(), dur, rpc_url, anvil_url, len(addresses), str(hf_max), blocks, first_block, last_block,
                          stats(lags), stats(detects), trips_per_block, stats(sim_lat), sims, list(cands.values()), comp,
                          {"candidates_total": len(cands), "liquidated_by_competitor": len(taken), "healed": sum(1 for c in cands.values() if c.status == "healed"),
                           "still_open": sum(1 for c in cands.values() if c.status == "open"),
                           "blocks_alive_when_taken": [c.blocks_alive for c in taken],
                           "competitor_txs": len(comp), "competitor_txs_same_block_as_our_detection": sum(1 for x in comp if x.blocks_after_our_detection == 0),
                           "competitor_txs_before_our_detection": sum(1 for x in comp if x.blocks_after_our_detection is None),
                           "unique_liquidators": len({x.liquidator for x in comp}),
                           "median_competitor_priority_fee_wei": _pct([x.max_priority_fee_wei for x in comp if x.max_priority_fee_wei is not None], 0.5),
                           "median_competitor_effective_gas_price_wei": _pct([x.effective_gas_price_wei for x in comp if x.effective_gas_price_wei is not None], 0.5)},
                          assumptions=[
                              "block lag = wall clock at first observation minus block timestamp; includes sequencer clock skew and the polling interval",
                              "detection latency = monotonic time from first observation of the head to one Multicall3 read of every watched position",
                              "watch set = borrowers with debt whose last reconciled health factor was below the threshold; positions outside it are not detected",
                              "simulation latency = fork reset at the head + receiver deploy + liquidate() on a private Anvil (the call may revert when the target is not eligible; the time is what is measured)",
                              "no transaction is ever built or sent; 'blocks after our detection' is a lower bound on how much faster a competitor was",
                          ], warnings=warnings[:50])
    if out_path:
        with open(out_path, "w") as f:
            json.dump(res.as_dict(), f, indent=1, default=str)
    return res


def _simulate_latency(net: NetworkConfig, anvil_url: str, fork_url: str, reader: AaveReader, borrower: str, state: tuple[int, int]) -> dict:
    """Time a full fork + receiver execution at the head for `borrower`. Read-only w.r.t. the public chain."""
    from app.simulation.replay import ReplayRequest, run_replay
    hf, debt = state
    try:
        # pick the borrower's largest debt / collateral reserves for the probe
        reserves = reader.reserves_list()
        best_d = best_c = None
        for a in reserves:
            urd = reader.user_reserve_data(a, borrower)
            if urd.variable_debt + urd.stable_debt > 0 and (best_d is None or urd.variable_debt > best_d[1]):
                best_d = (a, urd.variable_debt)
            if urd.usage_as_collateral and urd.a_token_balance > 0 and (best_c is None or urd.a_token_balance > best_c[1]):
                best_c = (a, urd.a_token_balance)
        if best_d is None or best_c is None:
            return {"seconds": None, "error": "no debt/collateral reserve found"}
        t0 = time.monotonic()
        r = run_replay(ReplayRequest(network=net, borrower=borrower, collateral_asset=best_c[0], debt_asset=best_d[0],
                                     debt_to_cover_raw=max(1, best_d[1] // 2), block_number=None))
        return {"seconds": time.monotonic() - t0, "success": r.success, "revert_reason": r.revert_reason, "execution_path": r.execution_path,
                "hf_at_probe": str(Decimal(hf) / Decimal(WAD)), "debt_asset": best_d[0], "collateral_asset": best_c[0]}
    except Exception as e:  # noqa: BLE001
        return {"seconds": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Live competitive shadow benchmark (read-only)")
    ap.add_argument("--network", default="base")
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--hf-max", default="1.2")
    ap.add_argument("--anvil", default=None)
    ap.add_argument("--sim-every", type=int, default=30, help="probe simulation latency every N blocks (needs --anvil)")
    ap.add_argument("--max-sims", type=int, default=10)
    ap.add_argument("--poll", type=float, default=0.5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    net = build_networks(s)[a.network]
    res = run_benchmark(net, minutes=a.minutes, hf_max=Decimal(a.hf_max), anvil_url=a.anvil, sim_every_blocks=a.sim_every,
                        poll_seconds=a.poll, max_simulations=a.max_sims, out_path=a.out)
    print(json.dumps({k: v for k, v in res.as_dict().items() if k not in ("candidates", "competitor_txs", "simulations")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
