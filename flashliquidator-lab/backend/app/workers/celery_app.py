from __future__ import annotations

from celery import Celery
from celery.schedules import schedule

from app.core.config import get_settings

s = get_settings()
celery = Celery("fll", broker=s.redis_url, backend=s.redis_url, include=["app.workers.tasks"])
celery.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"], timezone="UTC",
    task_acks_late=True, worker_prefetch_multiplier=1, task_time_limit=600, task_soft_time_limit=540,
    worker_max_tasks_per_child=200,
    task_routes={"app.workers.tasks.simulate_*": {"queue": "simulation"}, "app.workers.tasks.index_*": {"queue": "indexer"}},
    beat_schedule={
        "rpc-health": {"task": "app.workers.tasks.record_rpc_health", "schedule": schedule(s.monitor_interval_seconds)},
        "index-pool-events": {"task": "app.workers.tasks.index_pool_events_all", "schedule": schedule(s.indexer_poll_interval_seconds)},
        "reconcile-borrowers": {"task": "app.workers.tasks.reconcile_borrowers_all", "schedule": schedule(s.monitor_interval_seconds)},
        "build-candidates": {"task": "app.workers.tasks.build_candidates_all", "schedule": schedule(s.monitor_interval_seconds)},
        "shadow-tick": {"task": "app.workers.tasks.shadow_tick_all", "schedule": schedule(s.monitor_interval_seconds)},
        "retention-cleanup": {"task": "app.workers.tasks.retention_cleanup", "schedule": 6 * 3600},
    },
)
