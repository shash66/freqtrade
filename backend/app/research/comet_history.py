"""Historical Compound V3 (Comet) liquidation record over a block window: AbsorbDebt / AbsorbCollateral (the absorb)
and BuyCollateral (the paid leg) with receipts, so competitors, gas and timing can be analysed like the Aave
record. Spans are small so a provider with a 10-block limit (Alchemy free) works; progress is checkpointed to the
output file so the scan can resume.

    python -m app.research.comet_history --network base --rpc $BASE_ARCHIVE_RPC --from 52560000 --to 52640886 --span 10 --out ../docs/phase5-comet-history-base.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

from web3 import Web3

from app.chains.config import build_networks
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.protocols.compound_v3 import COMET_ABI, COMET_MARKETS


def scan(slug: str, rpc: str, from_block: int, to_block: int, span: int, out: str, min_interval_ms: int = 150) -> dict:
    s = get_settings()
    build_networks(s)[slug]
    client = RpcClient(f"{slug}-comet-history", rpc, s.rpc_timeout_seconds, s.rpc_max_retries, min_interval_ms=min_interval_ms)
    w3 = client.w3
    comets = {k: v for k, v in COMET_MARKETS.items() if v.network == slug}
    doc = {"network": slug, "from_block": from_block, "to_block": to_block, "span": span, "markets": {k: {"absorbs": [], "buys": []} for k in comets},
           "cursor": from_block, "errors": []}
    if os.path.exists(out):
        with open(out) as f:
            prev = json.load(f)
        if prev.get("from_block") == from_block and prev.get("to_block") == to_block:
            doc = prev
    contracts = {k: w3.eth.contract(address=Web3.to_checksum_address(v.primary_address), abi=COMET_ABI) for k, v in comets.items()}
    addresses = [c.address for c in contracts.values()]
    sigs = {Web3.keccak(text="AbsorbDebt(address,address,uint256,uint256)").hex(): "AbsorbDebt",
            Web3.keccak(text="AbsorbCollateral(address,address,address,uint256,uint256)").hex(): "AbsorbCollateral",
            Web3.keccak(text="BuyCollateral(address,address,uint256,uint256)").hex(): "BuyCollateral"}
    sigs = {(k if k.startswith("0x") else "0x" + k): v for k, v in sigs.items()}
    by_addr = {c.address.lower(): k for k, c in contracts.items()}
    b = int(doc["cursor"])
    t_last = time.monotonic()
    while b <= to_block:
        hi = min(b + span - 1, to_block)
        try:
            logs = client.call(w3.eth.get_logs, {"address": addresses, "fromBlock": b, "toBlock": hi, "topics": [list(sigs.keys())]})
        except Exception as e:  # noqa: BLE001
            doc["errors"].append(f"{b}-{hi}: {str(e)[:100]}")
            if len(doc["errors"]) > 50:
                break
            b = hi + 1
            continue
        for lg in logs:
            t0 = lg["topics"][0].hex()
            t0 = t0 if t0.startswith("0x") else "0x" + t0
            name = sigs.get(t0)
            mk = by_addr.get(lg["address"].lower())
            if not name or not mk:
                continue
            c = contracts[mk]
            ev = getattr(c.events, name)().process_log(lg)
            h = lg["transactionHash"].hex()
            h = h if h.startswith("0x") else "0x" + h
            row = {"tx": h, "log_index": int(lg["logIndex"]), "block": int(lg["blockNumber"]), **{k: (str(v) if isinstance(v, int) else v) for k, v in dict(ev["args"]).items()}}
            try:
                rc = client.call(w3.eth.get_transaction_receipt, h)
                row.update(gas_used=int(rc["gasUsed"]), effective_gas_price_wei=int(rc["effectiveGasPrice"]), tx_index=int(rc["transactionIndex"]), sender=rc["from"],
                           l1_fee_wei=int(rc["l1Fee"], 16) if isinstance(rc.get("l1Fee"), str) else (int(rc["l1Fee"]) if rc.get("l1Fee") is not None else None))
            except Exception as e:  # noqa: BLE001
                row["receipt_error"] = str(e)[:80]
            doc["markets"][mk]["absorbs" if name.startswith("Absorb") else "buys"].append(row)
        b = hi + 1
        doc["cursor"] = b
        if time.monotonic() - t_last > 30:
            with open(out, "w") as f:
                json.dump(doc, f, indent=1, default=str)
            t_last = time.monotonic()
    doc["summary"] = {k: {"absorb_events": len(v["absorbs"]), "absorb_txs": len({r["tx"] for r in v["absorbs"]}),
                          "absorbers": len({r.get("absorber") for r in v["absorbs"]}), "buy_events": len(v["buys"]), "buyers": len({r.get("buyer") for r in v["buys"]})}
                      for k, v in doc["markets"].items()}
    doc["summary"]["scanned_blocks"] = min(doc["cursor"], to_block + 1) - from_block
    doc["summary"]["errors"] = len(doc["errors"])
    with open(out, "w") as f:
        json.dump(doc, f, indent=1, default=str)
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="base")
    ap.add_argument("--rpc", required=True)
    ap.add_argument("--from", dest="from_block", type=int, required=True)
    ap.add_argument("--to", dest="to_block", type=int, required=True)
    ap.add_argument("--span", type=int, default=10)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    doc = scan(a.network, a.rpc, a.from_block, a.to_block, a.span, a.out)
    print(json.dumps(doc["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
