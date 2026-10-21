from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.chains.config import build_networks
from app.chains.rpc import RpcClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.base import get_sessionmaker
from app.database import models as m
from app.workers.celery_app import celery
from app.workers.jobs import job_run

log = get_logger(__name__)


def _networks():
    s = get_settings()
    return {k: v for k, v in build_networks(s).items() if v.enabled}


@celery.task(name="app.workers.tasks.record_rpc_health")
def record_rpc_health():
    s = get_settings()
    with job_run("record_rpc_health") as detail:
        Session = get_sessionmaker()
        with Session() as db:
            for slug, net in _networks().items():
                for kind, url in (("http", net.rpc_http), ("archive", net.archive_rpc)):
                    h = RpcClient(slug, url, s.rpc_timeout_seconds, 1).health()
                    db.add(m.RpcHealthMetric(chain_id=net.chain_id, endpoint_kind=kind, ok=h.ok, latency_ms=h.latency_ms,
                                             block_number=h.block_number, error=h.error))
                    detail[f"{slug}:{kind}"] = h.ok
            db.commit()


@celery.task(name="app.workers.tasks.index_pool_events_all")
def index_pool_events_all():
    for slug in _networks():
        index_pool_events.delay(slug)


@celery.task(name="app.workers.tasks.index_pool_events", bind=True, max_retries=0)
def index_pool_events(self, slug: str):
    from app.indexer.pool_indexer import run_indexer_once  # lazy: heavy import
    net = _networks()[slug]
    with job_run("index_pool_events", net.chain_id) as detail:
        detail.update(run_indexer_once(net))


@celery.task(name="app.workers.tasks.reconcile_borrowers_all")
def reconcile_borrowers_all():
    for slug in _networks():
        reconcile_borrowers.delay(slug)


@celery.task(name="app.workers.tasks.reconcile_borrowers")
def reconcile_borrowers(slug: str):
    from app.indexer.reconcile import reconcile_network
    net = _networks()[slug]
    with job_run("reconcile_borrowers", net.chain_id) as detail:
        detail.update(reconcile_network(net))


@celery.task(name="app.workers.tasks.retention_cleanup")
def retention_cleanup():
    s = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=s.raw_data_retention_days)
    with job_run("retention_cleanup") as detail:
        Session = get_sessionmaker()
        with Session() as db:
            for model, col in ((m.RpcHealthMetric, m.RpcHealthMetric.observed_at),
                               (m.OraclePriceObservation, m.OraclePriceObservation.observed_at),
                               (m.PositionSnapshot, m.PositionSnapshot.observed_at),
                               (m.WorkerJobRun, m.WorkerJobRun.started_at)):
                res = db.execute(delete(model).where(col < cutoff))
                detail[model.__tablename__] = res.rowcount
            db.commit()


@celery.task(name="app.workers.tasks.build_candidates_all")
def build_candidates_all():
    for slug in _networks():
        build_candidates_task.delay(slug)


@celery.task(name="app.workers.tasks.build_candidates")
def build_candidates_task(slug: str):
    from app.strategies.engine import build_candidates
    net = _networks()[slug]
    with job_run("build_candidates", net.chain_id) as detail:
        detail.update(build_candidates(net))


@celery.task(name="app.workers.tasks.simulate_run", bind=True)
def simulate_run(self, run_id: int):
    from app.simulation.service import execute_simulation_run
    with job_run("simulate_run") as detail:
        detail.update(execute_simulation_run(run_id, _networks()))


@celery.task(name="app.workers.tasks.run_backtest")
def run_backtest(strategy_run_id: int):
    from app.simulation.service import create_replay_runs_for_backtest, execute_simulation_run
    from app.competition.summary import summarize_backtest
    nets = _networks()
    with job_run("run_backtest") as detail:
        Session = get_sessionmaker()
        with Session() as db:
            sr = db.get(m.StrategyRun, strategy_run_id)
            sr.status, sr.started_at = m.JobStatus.running, datetime.now(timezone.utc)
            db.commit()
            ids = create_replay_runs_for_backtest(db, sr, nets)
        detail["replays"] = len(ids)
        for i, rid in enumerate(ids, 1):
            execute_simulation_run(rid, nets)
            with Session() as db:
                sr = db.get(m.StrategyRun, strategy_run_id)
                sr.progress = 100 * i // max(len(ids), 1)
                db.commit()
        with Session() as db:
            sr = db.get(m.StrategyRun, strategy_run_id)
            sr.summary, sr.assumptions, sr.data_quality = summarize_backtest(db, sr)
            sr.status, sr.finished_at, sr.progress = m.JobStatus.succeeded, datetime.now(timezone.utc), 100
            db.commit()


@celery.task(name="app.workers.tasks.shadow_tick_all")
def shadow_tick_all():
    from app.strategies.shadow import shadow_tick
    for slug, net in _networks().items():
        with job_run("shadow_tick", net.chain_id) as detail:
            detail.update(shadow_tick(net))
