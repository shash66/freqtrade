"""Checkpointed, reorg-aware, idempotent indexer of Aave V3 Pool events (spec §5.2).

Per run: verify the checkpoint block hash still matches the chain (walk back on a reorg and mark orphaned
events), then fetch logs forward in bounded, adaptive ranges, decode, upsert, and update the borrower set.
A failure on one network only fails that network's job."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from web3 import Web3
from web3._utils.events import get_event_data

from app.chains.config import NetworkConfig
from app.chains.rpc import RpcClient, RpcUnavailable
from app.chains.validation import load_abi
from app.core.config import get_settings
from app.core.logging import get_logger
from app.database import models as m
from app.database.base import get_sessionmaker

log = get_logger(__name__)

STREAM = "pool_events"
DEFAULT_LOOKBACK_BLOCKS = 50_000
HASH_HISTORY = 128  # how many recent block hashes we retain for reorg detection

BORROWER_FIELD = {
    "Supply": "onBehalfOf", "Borrow": "onBehalfOf", "Repay": "user", "Withdraw": "user",
    "LiquidationCall": "user", "ReserveUsedAsCollateralEnabled": "user", "ReserveUsedAsCollateralDisabled": "user",
    "UserEModeSet": "user",
}


class ReorgDetected(Exception):
    def __init__(self, common_ancestor: int):
        self.common_ancestor = common_ancestor


@dataclass
class DecodedLog:
    block_number: int
    block_hash: str
    tx_hash: str
    log_index: int
    event: str
    args: dict


class PoolEventDecoder:
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.abi = [e for e in load_abi("pool") if e["type"] == "event"]
        self.by_topic = {self.w3.keccak(text=f"{e['name']}({','.join(i['type'] for i in e['inputs'])})").hex(): e
                         for e in self.abi}

    def topics(self) -> list[str]:
        return ["0x" + t if not t.startswith("0x") else t for t in self.by_topic]

    def decode(self, raw) -> DecodedLog | None:
        t0 = raw["topics"][0]
        key = t0.hex() if hasattr(t0, "hex") else str(t0)
        abi = self.by_topic.get(key) or self.by_topic.get(key.replace("0x", ""))
        if abi is None:
            return None
        ev = get_event_data(self.w3.codec, abi, raw)
        args = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in dict(ev["args"]).items()}
        for k, v in list(args.items()):
            if isinstance(v, int):
                args[k] = str(v)  # keep uint256 exact in JSON
        return DecodedLog(block_number=raw["blockNumber"], block_hash=_hex(raw["blockHash"]), tx_hash=_hex(raw["transactionHash"]),
                          log_index=raw["logIndex"], event=abi["name"], args=args)


def _hex(v) -> str:
    return v.hex() if hasattr(v, "hex") else str(v)


def _norm_hash(h: str) -> str:
    return h if h.startswith("0x") else "0x" + h


class PoolIndexer:
    def __init__(self, net: NetworkConfig, client: RpcClient, db: Session):
        self.net, self.client, self.db = net, client, db
        self.s = get_settings()
        self.w3 = client.w3
        self.decoder = PoolEventDecoder(self.w3)
        self.pool_addr = Web3.to_checksum_address(net.market.pool)
        self.deployment = self._ensure_deployment()

    # ---- bookkeeping -------------------------------------------------------------------------
    def _ensure_deployment(self) -> m.ProtocolDeployment:
        dep = self.db.scalar(select(m.ProtocolDeployment).where(m.ProtocolDeployment.chain_id == self.net.chain_id,
                                                                m.ProtocolDeployment.pool_address == self.net.market.pool))
        if dep is None:
            if self.db.get(m.Network, self.net.chain_id) is None:
                self.db.add(m.Network(chain_id=self.net.chain_id, slug=self.net.slug, name=self.net.name,
                                      native_symbol=self.net.native_symbol, explorer_url=self.net.explorer_url,
                                      enabled=True, has_archive_rpc=self.net.has_archive_rpc,
                                      block_time_seconds=self.net.block_time_seconds))
            from app.aave.address_book import ADDRESS_BOOK_VERSION
            dep = m.ProtocolDeployment(chain_id=self.net.chain_id, protocol="aave_v3", market_name=self.net.market.market_name,
                                       pool_address=self.net.market.pool, pool_impl_address=self.net.market.pool_impl,
                                       addresses_provider=self.net.market.addresses_provider, oracle_address=self.net.market.oracle,
                                       data_provider_address=self.net.market.data_provider,
                                       ui_pool_data_provider=self.net.market.ui_pool_data_provider,
                                       address_book_version=ADDRESS_BOOK_VERSION)
            self.db.add(dep)
            self.db.commit()
        return dep

    def _checkpoint(self) -> m.IndexerCheckpoint | None:
        return self.db.scalar(select(m.IndexerCheckpoint).where(m.IndexerCheckpoint.chain_id == self.net.chain_id,
                                                                m.IndexerCheckpoint.deployment_id == self.deployment.id,
                                                                m.IndexerCheckpoint.stream == STREAM))

    def _start_block(self, head: int) -> int:
        if self.net.indexer_start_block is not None:
            return self.net.indexer_start_block
        return max(0, head - DEFAULT_LOOKBACK_BLOCKS)

    # ---- reorg handling ------------------------------------------------------------------------
    def _block_hash(self, n: int) -> str:
        return _norm_hash(_hex(self.client.call(self.w3.eth.get_block, n)["hash"]))

    def check_reorg(self, cp: m.IndexerCheckpoint) -> int | None:
        """Return the common-ancestor block if the chain diverged from our recorded hashes, else None."""
        if not cp.last_block_hash:
            return None
        if self._block_hash(cp.last_block) == cp.last_block_hash:
            return None
        history = {int(k): v for k, v in (cp.recent_block_hashes or {}).items()}
        for n in sorted(history, reverse=True):
            if self._block_hash(n) == history[n]:
                return n
        # nothing we remember matches: the only block known to be no newer than the divergence is before our oldest entry
        return max(0, min(history) - 1) if history else 0

    def rollback(self, cp: m.IndexerCheckpoint, ancestor: int) -> int:
        res = self.db.execute(update(m.PoolEvent).where(m.PoolEvent.chain_id == self.net.chain_id,
                                                        m.PoolEvent.deployment_id == self.deployment.id,
                                                        m.PoolEvent.block_number > ancestor,
                                                        m.PoolEvent.orphaned.is_(False)).values(orphaned=True))
        self.db.execute(update(m.HistoricalLiquidation).where(m.HistoricalLiquidation.chain_id == self.net.chain_id,
                                                              m.HistoricalLiquidation.deployment_id == self.deployment.id,
                                                              m.HistoricalLiquidation.block_number > ancestor)
                        .values(tx_status=-1))  # -1 marks "orphaned by reorg"; re-indexed rows overwrite it
        cp.last_block = ancestor
        cp.last_block_hash = self._block_hash(ancestor) if ancestor > 0 else None
        cp.recent_block_hashes = {k: v for k, v in (cp.recent_block_hashes or {}).items() if int(k) <= ancestor}
        self.db.commit()
        log.warning("reorg_rollback", network=self.net.slug, ancestor=ancestor, orphaned_events=res.rowcount)
        return res.rowcount

    # ---- fetching ------------------------------------------------------------------------------
    def fetch_logs(self, from_block: int, to_block: int) -> list[DecodedLog]:
        """Adaptive range: halve on provider 'too many results' style errors."""
        try:
            raws = self.client.call(self.w3.eth.get_logs, {"address": self.pool_addr, "fromBlock": from_block,
                                                           "toBlock": to_block})
        except RpcUnavailable as e:
            msg = str(e).lower()
            if to_block > from_block and any(k in msg for k in ("too many", "block range", "response size", "query returned more")):
                mid = (from_block + to_block) // 2
                return self.fetch_logs(from_block, mid) + self.fetch_logs(mid + 1, to_block)
            raise
        out = []
        for r in raws:
            d = self.decoder.decode(r)
            if d is not None:
                out.append(d)
        return out

    # ---- persistence ---------------------------------------------------------------------------
    def store(self, logs: list[DecodedLog]) -> dict:
        if not logs:
            return {"events": 0, "borrowers_touched": 0, "liquidations": 0}
        rows = [{"chain_id": self.net.chain_id, "deployment_id": self.deployment.id, "block_number": d.block_number,
                     "block_hash": _norm_hash(d.block_hash), "tx_hash": _norm_hash(d.tx_hash), "log_index": d.log_index,
                     "event_name": d.event, "user": _user_of(d), "reserve": d.args.get("reserve") or d.args.get("collateralAsset"),
                     "args": d.args, "orphaned": False} for d in logs]
        ins = insert(m.PoolEvent).values(rows)
        stmt = ins.on_conflict_do_update(constraint="uq_pool_event", set_={
            k: getattr(ins.excluded, k) for k in ("deployment_id", "block_number", "block_hash", "event_name", "user", "reserve", "args", "orphaned")})
        self.db.execute(stmt)
        touched = set()
        liqs = 0
        for d in logs:
            u = _user_of(d)
            if u:
                touched.add((u, d.block_number))
            if d.event == "LiquidationCall":
                liqs += 1
                self._store_liquidation(d)
        for user, bn in sorted(touched, key=lambda x: x[1]):
            stmt = insert(m.Borrower).values(chain_id=self.net.chain_id, deployment_id=self.deployment.id, address=user,
                                             first_seen_block=bn, last_event_block=bn, is_active=True)
            stmt = stmt.on_conflict_do_update(constraint="uq_borrower", set_={
                "last_event_block": stmt.excluded.last_event_block, "is_active": True})
            self.db.execute(stmt)
        self.db.commit()
        return {"events": len(logs), "borrowers_touched": len({u for u, _ in touched}), "liquidations": liqs}

    def _store_liquidation(self, d: DecodedLog) -> None:
        a = d.args
        row = {"chain_id": self.net.chain_id, "deployment_id": self.deployment.id, "tx_hash": _norm_hash(d.tx_hash), "log_index": d.log_index,
                   "block_number": d.block_number, "block_hash": _norm_hash(d.block_hash), "borrower": a["user"], "liquidator": a["liquidator"],
                   "debt_asset": a["debtAsset"], "collateral_asset": a["collateralAsset"], "debt_repaid_raw": int(a["debtToCover"]),
                   "collateral_seized_raw": int(a["liquidatedCollateralAmount"]), "receive_a_token": bool(a["receiveAToken"]),
                   "pool_impl_address": self.net.market.pool_impl}
        try:  # receipt enrichment is best-effort; missing gas data is recorded as NULL, never estimated
            rc = self.client.call(self.w3.eth.get_transaction_receipt, d.tx_hash if d.tx_hash.startswith("0x") else "0x" + d.tx_hash)
            blk = self.client.call(self.w3.eth.get_block, d.block_number)
            row.update(gas_used=int(rc["gasUsed"]), effective_gas_price_wei=int(rc["effectiveGasPrice"]) if rc.get("effectiveGasPrice") is not None else None,
                       tx_status=int(rc["status"]), tx_index=int(rc["transactionIndex"]),
                       block_timestamp=datetime.fromtimestamp(int(blk["timestamp"]), tz=UTC))
        except Exception as e:
            row.update(gas_used=None, effective_gas_price_wei=None, tx_status=None, tx_index=None, block_timestamp=None)
            log.warning("liquidation_receipt_unavailable", tx=d.tx_hash, error=str(e)[:120])
        stmt = insert(m.HistoricalLiquidation).values(row)
        self.db.execute(stmt.on_conflict_do_update(constraint="uq_hist_liq_event",
                                                   set_={k: getattr(stmt.excluded, k) for k in row if k not in ("chain_id", "tx_hash", "log_index")}))

    # ---- main loop -----------------------------------------------------------------------------
    def run_once(self, max_blocks: int | None = None) -> dict:
        head = int(self.client.call(lambda: self.w3.eth.block_number))
        safe_head = head  # we index to the tip and rely on hash checks to unwind reorgs
        cp = self._checkpoint()
        summary: dict = {"head": head, "reorg": None, "ranges": 0, "events": 0, "borrowers_touched": 0, "liquidations": 0}
        if cp is None:
            start = self._start_block(head)
            cp = m.IndexerCheckpoint(chain_id=self.net.chain_id, deployment_id=self.deployment.id, stream=STREAM,
                                     last_block=start - 1, last_block_hash=None, recent_block_hashes={})
            self.db.add(cp)
            self.db.commit()
        else:
            ancestor = self.check_reorg(cp)
            if ancestor is not None:
                summary["reorg"] = {"from": cp.last_block, "ancestor": ancestor, "orphaned": self.rollback(cp, ancestor)}
        budget = max_blocks or self.s.indexer_max_block_range * 10
        frm = cp.last_block + 1
        end_limit = min(safe_head, frm + budget - 1)
        while frm <= end_limit:
            to = min(frm + self.s.indexer_max_block_range - 1, end_limit)
            to_hash = self._block_hash(to)          # read BEFORE the logs so a reorg in between is caught below
            logs = self.fetch_logs(frm, to)
            if any(d.block_number == to and _norm_hash(d.block_hash) != to_hash for d in logs):
                log.warning("range_hash_mismatch_retry", network=self.net.slug, block=to)
                continue  # chain moved under us; retry the same range on the next loop iteration
            st = self.store(logs)
            for k in ("events", "borrowers_touched", "liquidations"):
                summary[k] += st[k]
            summary["ranges"] += 1
            cp.last_block = to
            cp.last_block_hash = to_hash
            hist = {int(k): v for k, v in (cp.recent_block_hashes or {}).items()}
            for d in logs:
                hist[d.block_number] = _norm_hash(d.block_hash)
            hist[to] = to_hash
            cp.recent_block_hashes = {str(k): hist[k] for k in sorted(hist)[-HASH_HISTORY:]}
            self.db.commit()
            frm = to + 1
        summary["last_block"] = cp.last_block
        summary["lag_blocks"] = head - cp.last_block
        return summary


def _user_of(d: DecodedLog) -> str | None:
    f = BORROWER_FIELD.get(d.event)
    return d.args.get(f) if f else None


def run_indexer_once(net: NetworkConfig, max_blocks: int | None = None) -> dict:
    s = get_settings()
    client = RpcClient(net.slug, net.rpc_http, s.rpc_timeout_seconds, s.rpc_max_retries)
    if not client.configured:
        return {"skipped": True, "reason": "no RPC configured"}
    with get_sessionmaker()() as db:
        return PoolIndexer(net, client, db).run_once(max_blocks)
