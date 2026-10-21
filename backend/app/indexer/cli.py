"""Operator CLI for bounded backfills and one-off runs.
  python -m app.indexer.cli backfill <network> <from_block> <to_block>
  python -m app.indexer.cli once <network>
  python -m app.indexer.cli reconcile <network>
"""
from __future__ import annotations

import json
import sys

from app.chains.config import build_networks
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.base import get_sessionmaker
from app.database import models as m
from app.indexer.pool_indexer import STREAM, PoolIndexer, run_indexer_once
from app.indexer.reconcile import reconcile_network


def backfill(slug: str, from_block: int, to_block: int) -> dict:
    s = get_settings()
    net = build_networks(s)[slug]
    client = RpcClient(slug, net.rpc_http, s.rpc_timeout_seconds, s.rpc_max_retries)
    if not client.configured:
        return {"error": "no RPC configured"}
    with get_sessionmaker()() as db:
        ix = PoolIndexer(net, client, db)
        total = {"events": 0, "borrowers_touched": 0, "liquidations": 0, "ranges": 0}
        frm = from_block
        while frm <= to_block:
            to = min(frm + s.indexer_max_block_range - 1, to_block)
            st = ix.store(ix.fetch_logs(frm, to))
            for k in ("events", "borrowers_touched", "liquidations"):
                total[k] += st[k]
            total["ranges"] += 1
            # move the checkpoint back if the backfill starts before it, so the regular run re-walks forward
            cp = ix._checkpoint()
            if cp is not None and from_block < cp.last_block and to_block >= cp.last_block:
                pass  # overlapping ranges are idempotent; the checkpoint keeps its tip
            print(json.dumps({"range": [frm, to], **st}), flush=True)
            frm = to + 1
        return total


def main(argv: list[str]) -> int:
    configure_logging(get_settings().log_level, json_output=False)
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "backfill" and len(argv) == 4:
        print(json.dumps(backfill(argv[1], int(argv[2]), int(argv[3]))))
    elif cmd == "once":
        print(json.dumps(run_indexer_once(build_networks(get_settings())[argv[1]])))
    elif cmd == "reconcile":
        print(json.dumps(reconcile_network(build_networks(get_settings())[argv[1]]), default=str))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
