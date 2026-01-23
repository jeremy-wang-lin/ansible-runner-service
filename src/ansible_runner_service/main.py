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
)

app = FastAPI(title="Ansible Runner Service")

PLAYBOOKS_DIR = Path(__file__).parent.parent.parent / "playbooks"


def get_playbooks_dir() -> Path:
    return PLAYBOOKS_DIR


def get_redis() -> Redis:
    return Redis()


def get_job_store() -> JobStore:
    return JobStore(get_redis())


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


@app.get("/api/v1/jobs/{job_id}", response_model=JobDetail)
def get_job(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
) -> JobDetail:
    """Get job status and details."""
    job = job_store.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

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
