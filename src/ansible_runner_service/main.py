# src/ansible_runner_service/main.py
from pathlib import Path
from typing import Union

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from redis import Redis

from ansible_runner_service.job_store import JobStore
from ansible_runner_service.queue import enqueue_job
from ansible_runner_service.runner import run_playbook
from ansible_runner_service.schemas import (
    JobRequest,
    JobResponse,
    JobSubmitResponse,
    JobDetail,
    JobResultSchema,
    JobSummary,
    JobListResponse,
)
from ansible_runner_service.repository import JobRepository
from ansible_runner_service.database import get_engine, get_session

app = FastAPI(title="Ansible Runner Service")

# Engine singleton for connection reuse
_engine = None


def get_engine_singleton():
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine

PLAYBOOKS_DIR = Path(__file__).parent.parent.parent / "playbooks"


def get_playbooks_dir() -> Path:
    return PLAYBOOKS_DIR


def get_redis() -> Redis:
    return Redis()


def get_job_store() -> JobStore:
    return JobStore(get_redis())


def get_repository():
    """Dependency that provides a JobRepository with proper session lifecycle."""
    engine = get_engine_singleton()
    Session = get_session(engine)
    session = Session()
    try:
        yield JobRepository(session)
    finally:
        session.close()


@app.post(
    "/api/v1/jobs",
    response_model=Union[JobSubmitResponse, JobResponse],
    status_code=202,
)
def submit_job(
    request: JobRequest,
    sync: bool = Query(default=False, description="Run synchronously"),
    playbooks_dir: Path = Depends(get_playbooks_dir),
    job_store: JobStore = Depends(get_job_store),
    redis: Redis = Depends(get_redis),
) -> Union[JobSubmitResponse, JobResponse]:
    """Submit a playbook job for execution."""
    # Block path traversal attempts
    if ".." in request.playbook or request.playbook.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid playbook name")

    playbook_path = playbooks_dir / request.playbook

    if not playbook_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Playbook not found: {request.playbook}"
        )

    if sync:
        # Synchronous execution
        result = run_playbook(
            playbook=request.playbook,
            extra_vars=request.extra_vars,
            inventory=request.inventory,
            playbooks_dir=playbooks_dir,
        )
        return JSONResponse(
            status_code=200,
            content=JobResponse(
                status=result.status,
                rc=result.rc,
                stdout=result.stdout,
                stats=result.stats,
            ).model_dump(),
        )

    # Async execution (default)
    job = job_store.create_job(
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
    )

    enqueue_job(
        job_id=job.job_id,
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
        redis=redis,
    )

    return JSONResponse(
        status_code=202,
        content=JobSubmitResponse(
            job_id=job.job_id,
            status=job.status.value,
            created_at=job.created_at.isoformat(),
        ).model_dump(),
    )


@app.get("/api/v1/jobs", response_model=JobListResponse)
def list_jobs(
    status: str | None = Query(default=None, description="Filter by status"),
    limit: int = Query(default=20, ge=1, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    repository: JobRepository = Depends(get_repository),
) -> JobListResponse:
    """List jobs with optional filtering and pagination."""
    # Cap limit at 100
    limit = min(limit, 100)

    jobs, total = repository.list_jobs(
        status=status,
        limit=limit,
        offset=offset,
    )

    job_summaries = [
        JobSummary(
            job_id=job.id,
            status=job.status,
            playbook=job.playbook,
            created_at=job.created_at.isoformat(),
            finished_at=job.finished_at.isoformat() if job.finished_at else None,
        )
        for job in jobs
    ]

    return JobListResponse(
        jobs=job_summaries,
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/api/v1/jobs/{job_id}", response_model=JobDetail)
def get_job(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
    repository: JobRepository = Depends(get_repository),
) -> JobDetail:
    """Get job status and details."""
    # Try Redis first (fast for active jobs)
    job = job_store.get_job(job_id)

    if job is not None:
        result = None
        if job.result:
            result = JobResultSchema(
                rc=job.result.rc,
                stdout=job.result.stdout,
                stats=job.result.stats,
            )

        return JobDetail(
            job_id=job.job_id,
            status=job.status.value,
            playbook=job.playbook,
            created_at=job.created_at.isoformat(),
            started_at=job.started_at.isoformat() if job.started_at else None,
            finished_at=job.finished_at.isoformat() if job.finished_at else None,
            result=result,
            error=job.error,
        )

    # Fallback to DB (for completed jobs after TTL)
    db_job = repository.get(job_id)

    if db_job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result = None
    if db_job.result_rc is not None:
        result = JobResultSchema(
            rc=db_job.result_rc,
            stdout=db_job.result_stdout or "",
            stats=db_job.result_stats or {},
        )

    return JobDetail(
        job_id=db_job.id,
        status=db_job.status,
        playbook=db_job.playbook,
        created_at=db_job.created_at.isoformat(),
        started_at=db_job.started_at.isoformat() if db_job.started_at else None,
        finished_at=db_job.finished_at.isoformat() if db_job.finished_at else None,
        result=result,
        error=db_job.error,
    )
