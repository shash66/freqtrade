"""Market registry and verification (Phase 4, priority 6): every market every adapter knows, verified live where
an endpoint is configured and reachable, and reported `not_configured` or `blocked` otherwise. Output is the
JSON behind the research dashboard's network/protocol comparison.

    python -m app.protocols.registry --out ../docs/phase4-markets.json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime

from app.aave.address_book import MARKETS
from app.aave.address_book_extra import EXTRA_MARKETS
from app.chains.config import build_networks
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.protocols.aave_v3 import AaveV3Adapter, aave_specs
from app.protocols.base import MarketSpec, Verification
from app.protocols.compound_v3 import COMET_MARKETS, CompoundV3Adapter


def all_specs() -> dict[str, MarketSpec]:
    return {**aave_specs(), **COMET_MARKETS}


def verify_all(sample_history_blocks: int = 0) -> dict:
    s = get_settings()
    nets = build_networks(s)
    out = {"generated_at": datetime.now(UTC).isoformat(), "markets": []}
    for slug, spec in all_specs().items():
        net = nets.get(spec.network) if spec.protocol == "compound_v3" else nets.get(spec.slug.removeprefix("aave_v3_")) or nets.get(spec.network)
        row = {"market": slug, **asdict(spec)}
        if net is None or not (net.rpc_http or net.archive_rpc):
            row.update(status="not_configured", reason=f"set {spec.network.upper()}_RPC_HTTP / {spec.network.upper()}_ARCHIVE_RPC")
            out["markets"].append(row)
            continue
        client = RpcClient(f"{slug}-verify", net.rpc_http or net.archive_rpc, s.rpc_timeout_seconds, 1)
        if spec.protocol == "aave_v3":
            mk = {**MARKETS, **EXTRA_MARKETS}[spec.slug.removeprefix("aave_v3_")]
            adapter = AaveV3Adapter(spec, mk, net, client)
        else:
            adapter = CompoundV3Adapter(spec, client)
        v: Verification = adapter.verify()
        row.update(status=v.status, verification=asdict(v))
        if v.status == "verified" and sample_history_blocks > 0:
            try:
                head = v.head_block or 0
                span = min(sample_history_blocks, 100)
                evs = adapter.historical_liquidations(head - span, head)
                row["recent_liquidation_events_last_blocks"] = {"blocks": span, "events": len(evs)}
            except Exception as e:  # noqa: BLE001
                row["recent_liquidation_events_last_blocks"] = {"error": str(e)[:120]}
        out["markets"].append(row)
    out["summary"] = {"markets": len(out["markets"]), **{st: sum(1 for m in out["markets"] if m["status"] == st) for st in ("verified", "failed", "blocked", "not_configured")},
                      "protocols": sorted({m["protocol"] for m in out["markets"]}), "chains": sorted({m["chain_id"] for m in out["markets"]})}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--sample-history-blocks", type=int, default=100)
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    out = verify_all(a.sample_history_blocks)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1, default=str)
    print(json.dumps(out["summary"], indent=1))
    for m in out["markets"]:
        print(m["market"].ljust(32), m["status"], (m.get("reason") or (m.get("verification") or {}).get("reason") or "")[:100])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
