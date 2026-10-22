"""Phase 2: live blockchain validation runner (read-only).

Executes the validation items against the configured RPCs and writes a report that states, per item, whether it
was EXECUTED (with the measured numbers), SKIPPED by the tool, or BLOCKED with the reason. Nothing here can
broadcast a transaction: every write goes to the private Anvil fork. Run with:

    python -m app.validation.phase2 --network base --blocks 20000 --replays 25 --out docs/phase2-results.md
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.chains.config import NetworkConfig, build_networks
from app.chains.rpc import RpcClient
from app.chains.validation import verify_deployment
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database import models as m
from app.database.base import get_sessionmaker
from app.indexer.pool_indexer import PoolIndexer
from app.indexer.reconcile import reconcile_network
from app.simulation.anvil import AnvilFork
from app.simulation.replay import ReplayRequest, run_replay


@dataclass
class Item:
    key: str
    title: str
    status: str = "BLOCKED"          # EXECUTED | SKIPPED | BLOCKED | FAILED
    detail: dict = field(default_factory=dict)
    reason: str | None = None


@dataclass
class Report:
    generated_at: str
    network: str
    items: list[Item] = field(default_factory=list)

    def add(self, item: Item) -> Item:
        self.items.append(item)
        return item

    def to_markdown(self) -> str:
        lines = [f"# Phase 2 live validation: {self.network}", "", f"Generated {self.generated_at}. Read-only; all executions on a private Anvil fork.", "",
                 "| # | Item | Status | Detail |", "|---|---|---|---|"]
        for i, it in enumerate(self.items, 1):
            detail = it.reason if it.status in ("BLOCKED", "FAILED", "SKIPPED") and it.reason else json.dumps(it.detail, default=str)[:600]
            lines.append(f"| {i} | {it.title} | **{it.status}** | {detail} |")
        return "\n".join(lines) + "\n"


def _net(slug: str) -> NetworkConfig:
    return build_networks(get_settings())[slug]


def step_rpc_and_deployment(rep: Report, net: NetworkConfig, client: RpcClient) -> bool:
    it = rep.add(Item("rpc", "Connect to RPC and verify the Aave deployment (chain id, bytecode, ADDRESSES_PROVIDER, premium)"))
    if not client.configured:
        it.reason = f"no RPC configured for {net.slug} ({net.slug.upper()}_RPC_HTTP empty)"
        return False
    h = client.health()
    if not h.ok:
        it.reason = f"RPC unreachable: {h.error}"
        return False
    v = verify_deployment(net, client)
    it.detail = {"rpc_block": h.block_number, "latency_ms": h.latency_ms, **v}
    it.status = "EXECUTED" if v["verified"] else "FAILED"
    it.reason = None if v["verified"] else v["error"]
    return bool(v["verified"])


def step_index(rep: Report, net: NetworkConfig, client: RpcClient, blocks: int) -> dict:
    it = rep.add(Item("index", f"Index the last {blocks} blocks of Pool events: borrowers and LiquidationCall records"))
    try:
        with get_sessionmaker()() as db:
            ix = PoolIndexer(net, client, db)
            summary = ix.run_once(max_blocks=blocks)
            borrowers = db.scalar(select(m.Borrower).where(m.Borrower.chain_id == net.chain_id).limit(1)) is not None
            n_b = len(db.scalars(select(m.Borrower.id).where(m.Borrower.chain_id == net.chain_id)).all())
            liqs = db.scalars(select(m.HistoricalLiquidation).where(m.HistoricalLiquidation.chain_id == net.chain_id)
                              .order_by(m.HistoricalLiquidation.block_number.desc()).limit(5)).all()
            n_l = len(db.scalars(select(m.HistoricalLiquidation.id).where(m.HistoricalLiquidation.chain_id == net.chain_id)).all())
        it.detail = {**summary, "borrowers_total": n_b, "liquidations_total": n_l,
                     "sample_liquidations": [{"tx": h.tx_hash, "block": h.block_number, "liquidator": h.liquidator, "fee_source": h.fee_source} for h in liqs]}
        it.status = "EXECUTED" if borrowers else "FAILED"
        it.reason = None if borrowers else "no borrowers discovered in the range"
        return it.detail
    except Exception as e:
        it.status, it.reason = "FAILED", f"{type(e).__name__}: {str(e)[:300]}"
        return {}


def step_reconcile(rep: Report, net: NetworkConfig) -> None:
    it = rep.add(Item("reconcile", "Reconcile indexed borrowers against on-chain account data; count eligible and near-liquidation positions"))
    try:
        out = reconcile_network(net)
        it.detail, it.status = out, ("SKIPPED" if out.get("skipped") else "EXECUTED")
        it.reason = out.get("reason")
    except Exception as e:
        it.status, it.reason = "FAILED", f"{type(e).__name__}: {str(e)[:300]}"


def step_replays(rep: Report, net: NetworkConfig, max_replays: int, batch: str = "phase2") -> None:
    """Replay recorded liquidation *events* (one row per LiquidationCall log). Reporting is normalised:
    unique historical transactions, liquidation events, and simulation attempts are counted separately, and
    every outcome is taken from the final attempt of the event's run in this batch, so retries never inflate
    success or profit counts."""
    it = rep.add(Item("replay", f"Replay up to {max_replays} recorded liquidations through the flash-loan receiver on Anvil forks (batch {batch})"))
    s = get_settings()
    if not net.archive_rpc:
        it.reason = f"no archive RPC configured ({net.slug.upper()}_ARCHIVE_RPC empty); forks at historical blocks need archive state"
        return
    anvil_ok = AnvilFork(s.anvil_rpc_url).health()
    if not anvil_ok.get("ok"):
        it.reason = f"Anvil not reachable at {s.anvil_rpc_url}: {anvil_ok.get('error')}"
        return
    with get_sessionmaker()() as db:
        liqs = db.scalars(select(m.HistoricalLiquidation).where(m.HistoricalLiquidation.chain_id == net.chain_id,
                                                                (m.HistoricalLiquidation.tx_status.is_(None)) | (m.HistoricalLiquidation.tx_status >= 0))
                          .order_by(m.HistoricalLiquidation.block_number.desc()).limit(max_replays)).all()
    if not liqs:
        it.status, it.reason = "SKIPPED", "no recorded liquidations to replay (index a wider range first)"
        return
    results = []
    from app.simulation.service import _store_result
    for h in liqs:
        with get_sessionmaker()() as db:
            done = db.execute(select(m.SimulationRun, m.SimulationResult).join(m.SimulationResult, m.SimulationResult.run_id == m.SimulationRun.id)
                              .where(m.SimulationRun.kind == "historical_replay", m.SimulationRun.historical_liquidation_id == h.id,
                                     m.SimulationRun.batch == batch).order_by(m.SimulationRun.id.desc())).first()
        if done is not None:  # resume: reuse the persisted result of an earlier run
            run, res = done
            r = _replay_from_db(run, res)
        else:
            try:
                r = run_replay(ReplayRequest(network=net, borrower=h.borrower, collateral_asset=h.collateral_asset, debt_asset=h.debt_asset,
                                             debt_to_cover_raw=int(h.debt_repaid_raw), block_number=h.block_number,
                                             gas_price_wei=int(h.effective_gas_price_wei) if h.effective_gas_price_wei else None, historical_tx=h.tx_hash))
            except Exception as e:  # noqa: BLE001 - every replay is isolated; the failure is recorded, never dropped
                from app.simulation.replay import ReplayResult
                r = ReplayResult(False, f"replay crashed: {type(e).__name__}: {str(e)[:200]}", None, None, True, None, None, None, None, None,
                                 None, None, None, None, None, None, None, warnings=["code or infrastructure failure; see revert_reason"],
                                 assumptions=[], rpc_capability={}, execution_path="none", profit_complete=False)
            with get_sessionmaker()() as db:
                run = m.SimulationRun(chain_id=net.chain_id, kind="historical_replay", status=m.JobStatus.running, fork_block_number=h.block_number - 1,
                                      historical_liquidation_id=h.id, pool_address=net.market.pool, pool_impl_address=h.pool_impl_address,
                                      input_params={"borrower": h.borrower, "collateral_asset": h.collateral_asset, "debt_asset": h.debt_asset,
                                                    "debt_to_cover_raw": str(h.debt_repaid_raw), "block_number": h.block_number, "historical_tx": h.tx_hash,
                                                    "batch": batch}, code_version=s.app_version, batch=batch)
                db.add(run)
                db.flush()
                _store_result(db, run, r)
                db.commit()
        results.append({"tx": h.tx_hash, "block": h.block_number, "success": r.success, "execution_path": r.execution_path,
                        "simplified": r.execution_path != "flash_loan_receiver", "revert_reason": r.revert_reason,
                        "eligible_at_fork": r.eligible_at_fork, "net_profit_usd": None if r.net_profit_usd is None else str(r.net_profit_usd),
                        "premium_raw": r.flash_premium_raw, "swap_out_raw": r.swap_proceeds_raw, "gas_used": r.gas_used, "fees": r.fees,
                        "collateral_seized_raw": r.collateral_seized_raw, "debt_repaid_raw": r.debt_repaid_raw, "gas_cost_usd": None if r.gas_cost_usd is None else str(r.gas_cost_usd),
                        "retried": r.retried, "attempts": r.attempts, "historical_collateral_seized_raw": h.collateral_seized_raw,
                        "historical_gas_used": h.gas_used, "eligible_at_execution": r.eligible_at_execution,
                        "health_factor_at_fork": r.health_factor_at_fork, "health_factor_at_execution": r.health_factor_at_execution,
                        "surplus_raw": r.surplus_raw, "profit_complete": r.profit_complete, "reconciliation": r.reconciliation,
                        "warnings": r.warnings, "historical_liquidator": h.liquidator, "historical_l1_fee_wei": h.l1_fee_wei,
                        "historical_fee_source": h.fee_source, "event_id": h.id, "log_index": h.log_index})
    it.detail = summarize_replays(results, batch)
    it.status = "EXECUTED"


def summarize_replays(results: list[dict], batch: str) -> dict:
    """Normalised counts. `profitable` requires a complete cost picture (profit_complete True) and net > 0;
    successes whose costs are not all known are reported separately and never as net profitable."""
    paths = Counter(x["execution_path"] for x in results)
    ok = [x for x in results if x["success"]]
    complete_ok = [x for x in ok if x.get("profit_complete") is True and x["net_profit_usd"] is not None]
    profits = [Decimal(x["net_profit_usd"]) for x in complete_ok]
    eligibility = Counter(f"fork={x['eligible_at_fork']},execution={x.get('eligible_at_execution')}" for x in results)
    return {"batch": batch,
            "unique_historical_txs": len({x["tx"] for x in results}),
            "liquidation_events": len(results),
            "simulation_attempts": sum(int(x.get("attempts") or 1) for x in results),
            "events_with_retry": sum(1 for x in results if (x.get("attempts") or 1) > 1),
            "succeeded": len(ok), "reverted": len(results) - len(ok), "execution_paths": dict(paths),
            "profitable": sum(1 for p in profits if p > 0),
            "unprofitable_after_costs": sum(1 for p in profits if p <= 0),
            "succeeded_with_incomplete_costs": len(ok) - len(complete_ok),
            "net_profit_usd_total_complete": str(sum(profits, Decimal(0))) if profits else None,
            "fee_sources": dict(Counter((x["fees"] or {}).get("source") for x in results)),
            "not_eligible_at_block_boundary": sum(1 for x in results if x["eligible_at_fork"] is False),
            "eligible_at_execution_only": sum(1 for x in results if x["eligible_at_fork"] is False and x.get("eligible_at_execution") is True),
            "eligibility": dict(eligibility), "results": results}


def _replay_from_db(run: m.SimulationRun, res: m.SimulationResult):
    from app.simulation.replay import ReplayResult
    ts = res.trace_summary or {}
    return ReplayResult(res.success, res.revert_reason, run.fork_block_number, run.fork_block_hash, run.block_boundary_approximation,
                        ts.get("health_factor_at_fork"), ts.get("eligible_at_fork"), res.debt_repaid_raw, res.collateral_seized_raw,
                        res.swap_proceeds_raw, res.flash_premium_raw, res.gas_used, res.gas_price_wei, None, res.net_profit_debt_raw,
                        res.net_profit_usd, res.calldata_hex, trace_summary=ts.get("calls", []), warnings=list(res.warnings or []) + ["result reused from an earlier run"],
                        assumptions=list(res.assumptions or []), rpc_capability=run.rpc_capability or {}, execution_path=res.execution_path or "none",
                        fees=res.fees, attempts=run.attempts or 1, eligible_at_execution=res.eligible_at_execution,
                        health_factor_at_execution=ts.get("health_factor_at_execution"), surplus_raw=int(res.surplus_raw) if res.surplus_raw is not None else None,
                        profit_complete=res.profit_complete, reconciliation=res.reconciliation)


def step_failure_triage(rep: Report) -> None:
    """Separate code defects from unprofitable or boundary-affected opportunities using the replay details."""
    it = rep.add(Item("triage", "Classify failed simulations: code defect vs. not eligible at block boundary vs. unprofitable"))
    replay = next((x for x in rep.items if x.key == "replay"), None)
    if replay is None or replay.status != "EXECUTED":
        it.reason = "no executed replays to triage"
        return
    classes = Counter()
    examples = {}
    for r in replay.detail["results"]:
        if r["success"] and r.get("profit_complete") is not True:
            c = "succeeded_but_cost_unknown"
        elif r["success"] and Decimal(r["net_profit_usd"]) <= 0:
            c = "unprofitable_after_costs"
        elif r["success"]:
            c = "profitable"
        elif r["eligible_at_fork"] is False:
            c = "not_eligible_at_block_boundary"
        elif r["revert_reason"] and any(k in r["revert_reason"] for k in ("MustNotLeaveDust", "HealthFactorNotBelowThreshold", "0xb629b0e4", "0x930bb771")):
            c = "aave_rule_revert_while_eligible_at_boundary"
        elif r["revert_reason"] and any(k in r["revert_reason"] for k in ("InsufficientProfit", "Too little received", "SwapFailed")):
            c = "unprofitable_revert_by_receiver_guard"
        elif r["revert_reason"] and any(k in r["revert_reason"] for k in ("fork state fetch failed", "replay crashed", "anvil")):
            c = "infrastructure_failure"
        elif r["revert_reason"] and any(k in r["revert_reason"] for k in ("funding failed", "artifact", "RouterNotAllowed", "TokenNotAllowed", "NotPool", "BadInitiator")):
            c = "possible_code_or_setup_defect"
        else:
            c = "unclassified_revert"
        classes[c] += 1
        examples.setdefault(c, r["tx"])
    it.detail = {"classes": dict(classes), "examples": examples}
    it.status = "EXECUTED"


def step_competitors(rep: Report, net: NetworkConfig) -> None:
    it = rep.add(Item("competitors", "Observed competing liquidators from recorded LiquidationCall events"))
    with get_sessionmaker()() as db:
        liqs = db.scalars(select(m.HistoricalLiquidation).where(m.HistoricalLiquidation.chain_id == net.chain_id)).all()
    if not liqs:
        it.status, it.reason = "SKIPPED", "no recorded liquidations"
        return
    c = Counter(h.liquidator for h in liqs)
    it.detail = {"liquidations": len(liqs), "unique_liquidators": len(c),
                 "top": [{"address": a, "count": n, "share": round(n / len(liqs), 3)} for a, n in c.most_common(5)],
                 "gas_used_median": sorted(h.gas_used for h in liqs if h.gas_used)[len([h for h in liqs if h.gas_used]) // 2] if any(h.gas_used for h in liqs) else None,
                 "verified_fee_rows": sum(1 for h in liqs if h.fee_source == "verified")}
    it.status = "EXECUTED"


def run(slug: str, blocks: int, replays: int, batch: str = "phase2") -> Report:
    s = get_settings()
    configure_logging(s.log_level, json_output=False)
    net = _net(slug)
    rep = Report(datetime.now(UTC).isoformat(), slug)
    rep.add(Item("readonly", "Application read-only, no broadcast path", "EXECUTED",
                 {"live_trading_enabled": s.live_trading_enabled, "app_mode": s.app_mode, "signer": "none", "broadcast_code_path": "none"}))
    client = RpcClient(slug, net.rpc_http, s.rpc_timeout_seconds, s.rpc_max_retries)
    if not step_rpc_and_deployment(rep, net, client):
        for key, title in (("index", "Index Pool events"), ("reconcile", "Reconcile borrowers"), ("replay", "Replay through the receiver"),
                           ("triage", "Classify failures"), ("competitors", "Observed competitors")):
            rep.add(Item(key, title, "BLOCKED", reason="blocked by the RPC/deployment step above"))
        return rep
    step_index(rep, net, client, blocks)
    step_reconcile(rep, net)
    step_replays(rep, net, replays, batch)
    step_failure_triage(rep)
    step_competitors(rep, net)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="base")
    ap.add_argument("--blocks", type=int, default=20_000)
    ap.add_argument("--replays", type=int, default=25)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--batch", default="phase2", help="label grouping persisted replay runs; a new label replays every event afresh")
    a = ap.parse_args()
    rep = run(a.network, a.blocks, a.replays, a.batch)
    md = rep.to_markdown()
    print(md)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"generated_at": rep.generated_at, "network": rep.network,
                       "items": [{"key": i.key, "title": i.title, "status": i.status, "reason": i.reason, "detail": i.detail} for i in rep.items]}, f, default=str, indent=1)
    return 0 if all(i.status in ("EXECUTED", "SKIPPED") for i in rep.items) else 2


if __name__ == "__main__":
    raise SystemExit(main())
