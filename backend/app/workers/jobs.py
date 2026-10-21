"""Job-run auditing: every background job writes a worker_job_runs row, including failures and skips."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.database import models as m
from app.database.base import get_sessionmaker

log = get_logger(__name__)


@contextmanager
def job_run(job_name: str, chain_id: int | None = None):
    Session = get_sessionmaker()
    with Session() as db:
        run = m.WorkerJobRun(job_name=job_name, chain_id=chain_id, status=m.JobStatus.running)
        db.add(run)
        db.commit()
        run_id = run.id
    detail: dict = {}
    try:
        yield detail
        status, error = (m.JobStatus.skipped if detail.get("skipped") else m.JobStatus.succeeded), None
    except Exception as e:
        status, error = m.JobStatus.failed, f"{type(e).__name__}: {str(e)[:500]}"
        log.error("job_failed", job=job_name, chain_id=chain_id, error=error)
        raise
    finally:
        with Session() as db:
            run = db.get(m.WorkerJobRun, run_id)
            if run:
                run.status, run.error, run.detail = status, error, detail
                run.finished_at = datetime.now(UTC)
                db.commit()
