# src/ansible_runner_service/main.py
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Union

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from redis import Redis

from ansible_runner_service.git_config import load_providers, validate_repo_url
from ansible_runner_service.job_store import JobStore
from ansible_runner_service.queue import enqueue_job
from ansible_runner_service.runner import run_playbook
from ansible_runner_service.schemas import (
    GitPlaybookSource,
    GitRoleSource,
    GitInventory,
    InlineInventory,
    JobRequest,
    JobResponse,
    JobSubmitResponse,
    JobDetail,
    JobResultSchema,
    JobSummary,
    JobListResponse,
    PlaybookSourceConfig,
    RoleSourceConfig,
    SourceConfig,
)
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


def recover_stale_jobs(repository: JobRepository, redis: Redis) -> None:
    """Mark stale running jobs as failed on startup."""
    stale_jobs = repository.list_stale_running_jobs()

    for job in stale_jobs:
        # Only mark as failed if not in Redis (truly abandoned)
        if not redis.exists(f"job:{job.id}"):
            repository.update_status(
                job.id,
                "failed",
                error="Worker crashed or timed out",
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: recover stale jobs
    try:
        engine = get_engine_singleton()
        Session = get_session(engine)
        session = Session()
        try:
            repository = JobRepository(session)
            redis = get_redis()
            recover_stale_jobs(repository, redis)
        finally:
            session.close()
    except Exception:
        pass  # Don't block startup if DB not ready

    yield

    # Shutdown: nothing to do


app = FastAPI(title="Ansible Runner Service", lifespan=lifespan)


PLAYBOOKS_DIR = Path(__file__).parent.parent.parent / "playbooks"


def get_playbooks_dir() -> Path:
    return PLAYBOOKS_DIR


def get_job_store():
    """Dependency that provides a JobStore with proper session lifecycle."""
    engine = get_engine_singleton()
    Session = get_session(engine)
    session = Session()
    try:
        repository = JobRepository(session)
        yield JobStore(get_redis(), repository=repository)
    finally:
        session.close()


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
    if request.source:
        return _handle_git_source(request, sync, job_store, redis)
    else:
        return _handle_local_source(request, sync, playbooks_dir, job_store, redis)


def _handle_local_source(request, sync, playbooks_dir, job_store, redis):
    """Handle legacy local playbook source."""
    # Block path traversal attempts
    if ".." in request.playbook or request.playbook.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid playbook name")

    playbook_path = playbooks_dir / request.playbook

    if not playbook_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Playbook not found: {request.playbook}"
        )

    # Serialize inventory for storage/queue
    inventory = request.inventory
    if not isinstance(inventory, str):
        inventory = inventory.model_dump()

    # Serialize options (exclude defaults for compact storage)
    options = request.options.model_dump(exclude_defaults=True) or None

    if sync:
        # Git inventory requires clone - not supported in sync mode
        if isinstance(request.inventory, GitInventory):
            raise HTTPException(
                status_code=400,
                detail="Sync mode does not support git inventory. Use async mode.",
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Resolve inventory to string or file path
            if isinstance(request.inventory, str):
                resolved_inventory = request.inventory
            else:  # InlineInventory
                inv_path = os.path.join(tmpdir, "inventory.yml")
                with open(inv_path, "w") as f:
                    yaml.dump(request.inventory.data, f, default_flow_style=False)
                resolved_inventory = inv_path

            result = run_playbook(
                playbook=request.playbook,
                extra_vars=request.extra_vars,
                inventory=resolved_inventory,
                playbooks_dir=playbooks_dir,
                options=options,
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

    job = job_store.create_job(
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=inventory,
        options=options,
    )

    enqueue_job(
        job_id=job.job_id,
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=inventory,
        options=options,
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


def _handle_git_source(request, sync, job_store, redis):
    """Handle Git playbook/role source."""
    source = request.source

    # Validate repo URL against allowed providers
    providers = load_providers()
    try:
        validate_repo_url(source.repo, providers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Determine playbook name and source_config for the queue.
    # Note: the `playbook` field stores the role name for role sources.
    # Use `source_type` column to disambiguate in queries.
    source_config: SourceConfig
    if isinstance(source, GitPlaybookSource):
        playbook = source.path
        source_config = PlaybookSourceConfig(
            type="playbook",
            repo=source.repo,
            branch=source.branch,
            path=source.path,
        )
    elif isinstance(source, GitRoleSource):
        playbook = source.role
        source_config = RoleSourceConfig(
            type="role",
            repo=source.repo,
            branch=source.branch,
            role=source.role,
            role_vars=source.role_vars,
        )
    else:
        raise HTTPException(status_code=400, detail="Unknown source type")

    # Serialize inventory for storage/queue
    inventory = request.inventory
    if not isinstance(inventory, str):
        inventory = inventory.model_dump()

    # Serialize options (exclude defaults for compact storage)
    options = request.options.model_dump(exclude_defaults=True) or None

    if sync:
        raise HTTPException(
            status_code=400,
            detail="Sync mode not supported for Git sources. Use async mode.",
        )

    job = job_store.create_job(
        playbook=playbook,
        extra_vars=request.extra_vars,
        inventory=inventory,
        source_type=source.type,
        source_repo=source.repo,
        source_branch=source.branch,
        options=options,
    )

    enqueue_job(
        job_id=job.job_id,
        playbook=playbook,
        extra_vars=request.extra_vars,
        inventory=inventory,
        source_config=source_config,
        options=options,
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
