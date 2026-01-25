# src/ansible_runner_service/worker.py
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from redis import Redis

from ansible_runner_service.job_store import JobStore, JobStatus, JobResult
from ansible_runner_service.runner import run_playbook
from ansible_runner_service.repository import JobRepository
from ansible_runner_service.database import get_engine, get_session


# Engine singleton for connection reuse
_engine = None


def get_engine_singleton():
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def get_redis() -> Redis:
    return Redis()


def get_repository() -> JobRepository:
    engine = get_engine_singleton()
    Session = get_session(engine)
    return JobRepository(Session())


def get_job_store() -> JobStore:
    return JobStore(get_redis(), repository=get_repository())


def get_playbooks_dir() -> Path:
    return Path(__file__).parent.parent.parent / "playbooks"


def execute_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str,
) -> None:
    """Execute a job - called by rq worker."""
    store = get_job_store()
    playbooks_dir = get_playbooks_dir()

    # Mark as running
    store.update_status(
        job_id,
        JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )

    try:
        result = run_playbook(
            playbook=playbook,
            extra_vars=extra_vars,
            inventory=inventory,
            playbooks_dir=playbooks_dir,
        )

        job_result = JobResult(
            rc=result.rc,
            stdout=result.stdout,
            stats=result.stats,
        )

        status = JobStatus.SUCCESSFUL if result.rc == 0 else JobStatus.FAILED
        store.update_status(
            job_id,
            status,
            finished_at=datetime.now(timezone.utc),
            result=job_result,
        )

    except Exception as e:
        store.update_status(
            job_id,
            JobStatus.FAILED,
            finished_at=datetime.now(timezone.utc),
            error=str(e),
        )
