"""Opportunity observability (Phase 4, priorities 3–5): for every recorded liquidation, what could a public
observer have known before the winning transaction, from legitimate on-chain data only?

Per event (block N, borrower, collateral, debt) the tool records, all at the state of N−1 unless stated:

- the Aave oracle's price source (proxy) for each of the two assets and the aggregator behind it;
- the proxy's round/answer (what the Pool used) and the aggregator's round/answer when called from an
  ordinary address (what anyone can read); when they differ, the first block at which the aggregator
  already carried the newer round ("lead" in blocks and seconds before N);
- the borrower's health factor under the Pool's own prices, and under the aggregator's newer price (the
  oracle source is re-pointed on a private fork; no contract is modified on any public chain);
- the DEX spot price of collateral in debt terms (Uniswap v3 QuoterV2) and its deviation from the oracle;
- the category: publicly_observable (liquidatable at N−1 with the Pool's own prices), predictable_not_yet_eligible
  (not yet liquidatable, but the aggregator's newer round or interest accrual at block N's timestamp makes it
  so), intra_block_only (no public signal before block N), unknown (a probe failed).

The lead is what a bot could have used; whether it could act is a separate question (an SVR-style feed
gives the auction winner an exclusive window), reported as `proxy_reveal_after_seconds` from the Phase 3
time-gate probe when available.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from sqlalchemy import select
from web3 import Web3

from app.aave.reads import AaveReader
from app.chains.config import NetworkConfig, build_networks
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.money import WAD
from app.database import models as m
from app.database.base import get_sessionmaker
from app.simulation.anvil import AnvilFork
from app.strategies.quotes import UniswapV3Quoter

log = get_logger(__name__)

SEL = {"getSourceOfAsset": "0x92bf2be0", "aggregator": "0x245a7bfc", "latestRoundData": "0xfeaf968c", "typeAndVersion": "0x181f5a77",
       "getAssetPrice": "0xb3596f07"}
CATEGORIES = ("publicly_observable", "predictable_not_yet_eligible", "intra_block_only", "unknown")


def _call(client: RpcClient, to: str, data: str, block, sender: str | None = None) -> str | None:
    tx = {"to": Web3.to_checksum_address(to), "data": data}
    if sender:
        tx["from"] = Web3.to_checksum_address(sender)
    try:
        out = client.call(client.w3.eth.call, tx, block)
        return out.hex() if hasattr(out, "hex") else str(out)
    except Exception:  # noqa: BLE001 - absence is recorded as None, never guessed
        return None


def _addr(word: str | None) -> str | None:
    if not word:
        return None
    h = word[2:] if word.startswith("0x") else word
    return Web3.to_checksum_address("0x" + h[-40:]) if len(h) >= 40 else None


def _round(data: str | None) -> dict | None:
    if not data:
        return None
    h = data[2:] if data.startswith("0x") else data
    if len(h) < 320:
        return None
    rid = int(h[0:64], 16)
    ans = int(h[64:128], 16)
    ans = ans - 2**256 if ans >= 2**255 else ans
    return {"round_id": rid & 0xFFFFFFFFFFFFFFFF, "round_id_full": rid, "answer": ans, "updated_at": int(h[192:256], 16)}


def _text(data: str | None) -> str | None:
    if not data:
        return None
    try:
        h = data[2:] if data.startswith("0x") else data
        n = int(h[64:128], 16)
        return bytes.fromhex(h[128:128 + 2 * n]).decode("utf8", "ignore")
    except Exception:  # noqa: BLE001
        return None


@dataclass
class FeedView:
    asset: str
    source: str | None
    aggregator: str | None
    aggregator_type: str | None
    proxy_round: dict | None
    aggregator_round: dict | None
    aggregator_leads: bool
    lead_first_block: int | None = None
    lead_blocks: int | None = None
    lead_seconds: int | None = None
    proxy_reveal_after_seconds: int | None = None   # from the Phase 3 time-gate probe
    revealed_price_matches_aggregator: bool | None = None


@dataclass
class EventObservability:
    tx: str
    log_index: int
    block: int
    block_timestamp: int
    borrower: str
    collateral: str
    debt: str
    health_factor_pool_prices: str | None
    eligible_pool_prices: bool | None
    health_factor_aggregator_prices: str | None
    eligible_aggregator_prices: bool | None
    eligible_at_block_timestamp: bool | None
    collateral_feed: FeedView | None
    debt_feed: FeedView | None
    dex_price_in_debt: str | None
    dex_vs_oracle_bps: int | None
    category: str
    reason: str
    observable_before_winner: bool | None
    lead_seconds_before_winner: int | None
    notes: list[str] = field(default_factory=list)


def _feed_view(client: RpcClient, oracle: str, asset: str, block_n: int, ts_n: int, gate: dict | None) -> FeedView:
    src = _addr(_call(client, oracle, SEL["getSourceOfAsset"] + asset[2:].lower().rjust(64, "0"), block_n - 1))
    agg = _addr(_call(client, src, SEL["aggregator"], block_n - 1)) if src else None
    agg = agg or src
    atype = _text(_call(client, agg, SEL["typeAndVersion"], block_n - 1)) if agg else None
    proxy_rd = _round(_call(client, src, SEL["latestRoundData"], block_n - 1)) if src else None
    agg_rd = _round(_call(client, agg, SEL["latestRoundData"], block_n - 1)) if agg else None
    leads = bool(proxy_rd and agg_rd and agg_rd["round_id"] > proxy_rd["round_id"])
    fv = FeedView(asset, src, agg, atype, proxy_rd, agg_rd, leads)
    if gate:
        fv.proxy_reveal_after_seconds = gate.get("reveal_after_seconds")
        if gate.get("revealed_price") is not None and agg_rd:
            fv.revealed_price_matches_aggregator = int(gate["revealed_price"]) == agg_rd["answer"]
    if leads:
        # earliest block (within 90 blocks) at which the aggregator already returned this round, read as an ordinary caller
        target = agg_rd["round_id"]
        lo, hi = max(1, block_n - 90), block_n - 1      # invariant: hi has the round; lo may not
        rd_lo = _round(_call(client, agg, SEL["latestRoundData"], lo))
        if rd_lo and rd_lo["round_id"] >= target:
            fv.lead_first_block = lo
        else:
            while hi - lo > 1:
                mid = (lo + hi) // 2
                rd = _round(_call(client, agg, SEL["latestRoundData"], mid))
                if rd and rd["round_id"] >= target:
                    hi = mid
                else:
                    lo = mid
            fv.lead_first_block = hi
        fv.lead_blocks = block_n - fv.lead_first_block
        try:
            ts_first = int(client.call(client.w3.eth.get_block, fv.lead_first_block)["timestamp"])
            fv.lead_seconds = ts_n - ts_first
        except Exception:  # noqa: BLE001
            fv.lead_seconds = None
    return fv


def _hf(reader: AaveReader, borrower: str) -> tuple[str, bool]:
    acct = reader.user_account_data(borrower)
    hf = Decimal(acct.health_factor_wad) / Decimal(WAD) if acct.health_factor_wad < 2**256 - 1 else Decimal("Infinity")
    return str(hf), acct.total_debt_base > 0 and acct.health_factor_wad < WAD


def _hf_under_aggregators(anvil: AnvilFork, reader: AaveReader, net: NetworkConfig, borrower: str, feeds: list[FeedView], notes: list[str]) -> tuple[str, bool] | None:
    """On a snapshot of the fork at N−1, point the Aave oracle's source for each leading asset at the aggregator
    itself (storage slot 0 mapping of AaveOracle), verify getAssetPrice now equals the aggregator's answer, read
    the health factor, revert. Nothing leaves the private fork."""
    leading = [f for f in feeds if f.aggregator_leads and f.aggregator and f.aggregator_round]
    if not leading:
        return None
    snap = anvil.snapshot()
    try:
        oracle = Web3.to_checksum_address(net.market.oracle)
        for f in leading:
            slot = Web3.keccak(bytes.fromhex(f.asset[2:].lower().rjust(64, "0")) + (0).to_bytes(32, "big"))
            anvil.rpc("anvil_setStorageAt", [oracle, slot.hex() if slot.hex().startswith("0x") else "0x" + slot.hex(),
                                             "0x" + f.aggregator[2:].lower().rjust(64, "0")])
            got = reader.asset_prices([f.asset])[Web3.to_checksum_address(f.asset)]
            if got != f.aggregator_round["answer"]:
                notes.append(f"oracle re-point verification failed for {f.asset}: got {got}, aggregator {f.aggregator_round['answer']}")
                return None
        return _hf(reader, borrower)
    except Exception as e:  # noqa: BLE001
        notes.append(f"aggregator-price health factor not determined: {str(e)[:100]}")
        return None
    finally:
        anvil.revert(snap)


def classify(eligible_pool: bool | None, eligible_agg: bool | None, eligible_ts: bool | None, any_lead: bool, probe_ok: bool) -> tuple[str, str]:
    if eligible_pool is None or not probe_ok:
        return "unknown", "a probe failed; no category assigned"
    if eligible_pool:
        return "publicly_observable", "liquidatable at N-1 with the Pool's own oracle prices"
    if eligible_agg:
        return "predictable_not_yet_eligible", "not liquidatable at N-1 with the Pool's prices, but liquidatable under the newer round the aggregator already carried"
    if eligible_ts:
        return "predictable_not_yet_eligible", "not liquidatable at N-1, liquidatable at block N's timestamp from interest accrual alone"
    if any_lead:
        return "intra_block_only", "the aggregator carried a newer round at N-1 but it did not make the position liquidatable; eligibility arose inside block N"
    return "intra_block_only", "no public price or time signal before block N; eligibility arose inside block N"


def analyse_network(slug: str, anvil_url: str, timegate: dict | None, replays: dict | None, limit: int = 100) -> dict:
    s = get_settings()
    net = build_networks(s)[slug]
    if not net.archive_rpc:
        return {"network": slug, "status": "BLOCKED", "reason": "archive RPC required"}
    src = RpcClient(f"{slug}-archive", net.archive_rpc, s.rpc_timeout_seconds, s.rpc_max_retries)
    quoter = UniswapV3Quoter(net.chain_id, src)
    anvil = AnvilFork(anvil_url, timeout=300)
    fork_reader = AaveReader(net, RpcClient(f"{slug}-anvil", anvil_url, 300.0, 1))
    with get_sessionmaker()() as db:
        liqs = db.scalars(select(m.HistoricalLiquidation).join(m.ProtocolDeployment, m.ProtocolDeployment.id == m.HistoricalLiquidation.deployment_id)
                          .where(m.HistoricalLiquidation.chain_id == net.chain_id, m.ProtocolDeployment.pool_address.ilike(net.market.pool),
                                 (m.HistoricalLiquidation.tx_status.is_(None)) | (m.HistoricalLiquidation.tx_status >= 0))
                          .order_by(m.HistoricalLiquidation.block_number.desc()).limit(limit)).all()
        assets = {a.address.lower(): a for a in db.scalars(select(m.Asset).where(m.Asset.chain_id == net.chain_id))}
    gate_by = {}
    for e in (timegate or {}).get("events", []):
        for a, v in (e["probe"].get("assets") or {}).items():
            gate_by[(e["block"], a.lower())] = v
    rep_by = {}
    rd = next((i["detail"] for i in (replays or {}).get("items", []) if i.get("key") == "replay"), {})
    for r in rd.get("results", []):
        rep_by[(r["tx"].lower(), r.get("log_index"))] = r
    out = {"network": slug, "status": "EXECUTED", "events": [], "summary": {}}
    current_fork: int | None = None
    for h in liqs:
        notes: list[str] = []
        blk = h.block_number
        try:
            ts_n = int(src.call(src.w3.eth.get_block, blk)["timestamp"])
        except Exception as e:  # noqa: BLE001
            out["events"].append(asdict(EventObservability(h.tx_hash, h.log_index, blk, 0, h.borrower, h.collateral_asset, h.debt_asset, None, None, None, None, None,
                                                           None, None, None, None, "unknown", f"block header unavailable: {str(e)[:80]}", None, None, [])))
            continue
        coll, dbt = Web3.to_checksum_address(h.collateral_asset), Web3.to_checksum_address(h.debt_asset)
        cf = _feed_view(src, net.market.oracle, coll, blk, ts_n, gate_by.get((blk, coll.lower())))
        df = _feed_view(src, net.market.oracle, dbt, blk, ts_n, gate_by.get((blk, dbt.lower()))) if dbt != coll else cf
        # fork at N-1 (reset once per block)
        probe_ok = True
        hf_pool = elig_pool = hf_agg = elig_agg = None
        try:
            if current_fork != blk:
                anvil.reset_fork(net.archive_rpc, blk - 1)
                current_fork = blk
            hf_pool, elig_pool = _hf(fork_reader, h.borrower)
            r = _hf_under_aggregators(anvil, fork_reader, net, h.borrower, [cf] + ([df] if df is not cf else []), notes)
            if r is not None:
                hf_agg, elig_agg = r
        except Exception as e:  # noqa: BLE001
            probe_ok = False
            notes.append(f"fork probe failed: {str(e)[:120]}")
        rep = rep_by.get((h.tx_hash.lower(), h.log_index))
        elig_ts = rep.get("eligible_at_execution") if rep else None
        if rep and elig_pool is None:
            elig_pool = rep.get("eligible_at_fork")
        # DEX price of collateral in debt terms at N-1 (QuoterV2 on the archive node)
        dex_px = dex_bps = None
        if coll != dbt and coll.lower() in assets and dbt.lower() in assets:
            try:
                ca, da = assets[coll.lower()], assets[dbt.lower()]
                q = quoter.quote_exact_in(coll, dbt, 10**ca.decimals, block=blk - 1)
                if q.status == "ok" and q.amount_out_raw:
                    dex = Decimal(q.amount_out_raw) / Decimal(10**da.decimals)
                    dex_px = str(dex)
                    if cf.proxy_round and df.proxy_round and df.proxy_round["answer"]:
                        oracle_ratio = Decimal(cf.proxy_round["answer"]) / Decimal(df.proxy_round["answer"])
                        dex_bps = int((dex / oracle_ratio - 1) * 10_000)
            except Exception as e:  # noqa: BLE001
                notes.append(f"dex quote failed: {str(e)[:80]}")
        any_lead = cf.aggregator_leads or df.aggregator_leads
        cat, why = classify(elig_pool, elig_agg, elig_ts, any_lead, probe_ok)
        lead_s = max([x.lead_seconds for x in (cf, df) if x.lead_seconds is not None] or [0]) if any_lead else None
        observable_before = None if cat == "unknown" else cat in ("publicly_observable", "predictable_not_yet_eligible")
        ev = EventObservability(h.tx_hash, h.log_index, blk, ts_n, h.borrower, coll, dbt, hf_pool, elig_pool, hf_agg, elig_agg, elig_ts,
                                cf, df if df is not cf else None, dex_px, dex_bps, cat, why, observable_before,
                                lead_s if observable_before else None, notes)
        out["events"].append(asdict(ev))
        log.info("observability", tx=h.tx_hash[:10], block=blk, category=cat, lead_seconds=lead_s)
    evs = out["events"]
    cats = {c: sum(1 for e in evs if e["category"] == c) for c in CATEGORIES}
    leads = [e["lead_seconds_before_winner"] for e in evs if e.get("lead_seconds_before_winner") is not None]
    out["summary"] = {"events": len(evs), "categories": cats,
                      "observable_before_winner": sum(1 for e in evs if e["observable_before_winner"]),
                      "observable_before_winner_rate": round(sum(1 for e in evs if e["observable_before_winner"]) / len(evs), 3) if evs else None,
                      "lead_seconds": {"n": len(leads), "min": min(leads) if leads else None, "median": sorted(leads)[len(leads) // 2] if leads else None, "max": max(leads) if leads else None},
                      "aggregator_types": sorted({(e["collateral_feed"] or {}).get("aggregator_type") or "?" for e in evs}),
                      "events_where_aggregator_led_proxy": sum(1 for e in evs if (e["collateral_feed"] or {}).get("aggregator_leads") or (e.get("debt_feed") or {}).get("aggregator_leads")),
                      "note": "lead = seconds between the aggregator first carrying the newer round (read as an ordinary caller) and the winning block; "
                              "whether a public bot could act during the lead depends on the proxy's reveal rule (proxy_reveal_after_seconds)"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="base")
    ap.add_argument("--anvil", required=True)
    ap.add_argument("--timegate-json", default=None)
    ap.add_argument("--replays-json", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    tg = rp = None
    if a.timegate_json:
        with open(a.timegate_json) as f:
            tg = json.load(f)
    if a.replays_json:
        with open(a.replays_json) as f:
            rp = json.load(f)
    out = analyse_network(a.network, a.anvil, tg, rp)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1, default=str)
    print(json.dumps(out.get("summary") or out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
