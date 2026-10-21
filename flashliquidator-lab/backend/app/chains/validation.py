"""Startup verification of protocol assumptions (spec §5.1, §15): chain id, bytecode present,
pool ↔ addresses-provider round trip, flash-loan premium readable. Failures are recorded, never hidden."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources

from app.aave.address_book import ADDRESS_BOOK_VERSION
from app.chains.config import NetworkConfig
from app.chains.rpc import RpcClient, RpcUnavailable
from app.core.logging import get_logger

log = get_logger(__name__)


def load_abi(name: str) -> list:
    with resources.files("app.aave.abis").joinpath(f"{name}.json").open() as f:
        return json.load(f)


def verify_deployment(net: NetworkConfig, client: RpcClient) -> dict:
    out = {"address_book_version": ADDRESS_BOOK_VERSION, "verified": False, "error": None, "checked_at": None}
    if not client.configured:
        out["error"] = "no RPC configured"
        return out
    try:
        w3 = client.w3
        cid = client.call(lambda: w3.eth.chain_id)
        if cid != net.chain_id:
            out["error"] = f"RPC chain id {cid} != expected {net.chain_id}"
            return out
        for label, addr in (("pool", net.market.pool), ("oracle", net.market.oracle),
                            ("data_provider", net.market.data_provider)):
            code = client.call(w3.eth.get_code, w3.to_checksum_address(addr))
            if len(code) == 0:
                out["error"] = f"{label} {addr} has no bytecode on chain {cid}"
                return out
        pool = w3.eth.contract(address=w3.to_checksum_address(net.market.pool), abi=load_abi("pool"))
        provider = client.call(pool.functions.ADDRESSES_PROVIDER().call)
        if provider.lower() != net.market.addresses_provider.lower():
            out["error"] = f"pool.ADDRESSES_PROVIDER()={provider} != address book {net.market.addresses_provider}"
            return out
        out["flashloan_premium_total_bps"] = int(client.call(pool.functions.FLASHLOAN_PREMIUM_TOTAL().call))
        out["flashloan_premium_to_protocol_bps"] = int(client.call(pool.functions.FLASHLOAN_PREMIUM_TO_PROTOCOL().call))
        out["verified"] = True
        out["checked_at"] = datetime.now(UTC).isoformat()
        log.info("deployment_verified", network=net.slug, chain_id=cid, premium_bps=out["flashloan_premium_total_bps"])
    except RpcUnavailable as e:
        out["error"] = f"rpc unavailable: {e}"
    except Exception as e:  # unexpected -> fail safe, keep network unverified
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out
