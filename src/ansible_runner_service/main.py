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
    LocalPlaybookSource,
    LocalRoleSource,
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
    LocalPlaybookSourceConfig,
    LocalRoleSourceConfig,
    GitPlaybookSourceConfig,
    GitRoleSourceConfig,
    UnifiedSourceConfig,
)
from ansible_runner_service.git_service import generate_role_wrapper_playbook
from ansible_runner_service.repository import JobRepository
from ansible_runner_service.database import get_engine, get_session
from sqlalchemy.orm import Session
from ansible_runner_service.health import check_redis, check_mariadb

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
COLLECTIONS_DIR = Path(__file__).parent.parent.parent / "collections"


def get_playbooks_dir() -> Path:
    return PLAYBOOKS_DIR


def get_collections_dir() -> Path:
    return COLLECTIONS_DIR


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


def get_db_session():
    """Get a database session for health checks."""
    engine = get_engine_singleton()
    with Session(engine) as session:
        yield session


@app.post(
    "/api/v1/jobs",
    response_model=Union[JobSubmitResponse, JobResponse],
    status_code=202,
)
def submit_job(
    request: JobRequest,
    sync: bool = Query(default=False, description="Run synchronously"),
    playbooks_dir: Path = Depends(get_playbooks_dir),
    collections_dir: Path = Depends(get_collections_dir),
    job_store: JobStore = Depends(get_job_store),
    redis: Redis = Depends(get_redis),
) -> Union[JobSubmitResponse, JobResponse]:
    """Submit a job for execution."""
    source = request.source

    # Validate sync mode constraints
    if sync:
        if source.type == "git":
            raise HTTPException(
                status_code=400,
                detail="Sync mode not supported for git sources. Use async mode.",
            )
        if isinstance(request.inventory, GitInventory):
            raise HTTPException(
                status_code=400,
                detail="Sync mode does not support git inventory. Use async mode.",
            )

    # Validate git repo if applicable
    if source.type == "git":
        providers = load_providers()
        try:
            validate_repo_url(source.repo, providers)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Determine playbook name for storage
    if source.target == "playbook":
        playbook_name = source.path
    else:  # role
        playbook_name = source.role

    # Build source_config for queue
    source_config = _build_source_config(source)

    # Serialize inventory
    inventory = request.inventory
    if not isinstance(inventory, str):
        inventory = inventory.model_dump()

    # Serialize options
    options = request.options.model_dump(exclude_defaults=True) or None

    if sync:
        return _execute_sync(
            source=source,
            extra_vars=request.extra_vars,
            inventory=request.inventory,
            options=options,
            playbooks_dir=playbooks_dir,
            collections_dir=collections_dir,
        )

    # Async mode
    job = job_store.create_job(
        playbook=playbook_name,
        extra_vars=request.extra_vars,
        inventory=inventory,
        source_type=source.type,
        source_target=source.target,
        source_repo=getattr(source, "repo", None),
        source_branch=getattr(source, "branch", None),
        options=options,
    )

    enqueue_job(
        job_id=job.job_id,
        playbook=playbook_name,
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


def _build_source_config(source) -> UnifiedSourceConfig:
    """Build TypedDict source config for queue serialization."""
    if isinstance(source, LocalPlaybookSource):
        return LocalPlaybookSourceConfig(
            type="local",
            target="playbook",
            path=source.path,
        )
    elif isinstance(source, LocalRoleSource):
        return LocalRoleSourceConfig(
            type="local",
            target="role",
            collection=source.collection,
            role=source.role,
            role_vars=source.role_vars,
        )
    elif isinstance(source, GitPlaybookSource):
        return GitPlaybookSourceConfig(
            type="git",
            target="playbook",
            repo=source.repo,
            branch=source.branch,
            path=source.path,
        )
    elif isinstance(source, GitRoleSource):
        return GitRoleSourceConfig(
            type="git",
            target="role",
            repo=source.repo,
            branch=source.branch,
            role=source.role,
            role_vars=source.role_vars,
        )
    else:
        raise ValueError(f"Unknown source type: {type(source)}")


def _execute_sync(
    source,
    extra_vars: dict,
    inventory,
    options: dict | None,
    playbooks_dir: Path,
    collections_dir: Path,
) -> JSONResponse:
    """Execute job synchronously - only for local sources with string/inline inventory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Resolve inventory
        if isinstance(inventory, str):
            resolved_inventory = inventory
        else:  # InlineInventory
            inv_path = os.path.join(tmpdir, "inventory.yml")
            with open(inv_path, "w") as f:
                yaml.dump(inventory.data, f, default_flow_style=False)
            resolved_inventory = inv_path

        if isinstance(source, LocalPlaybookSource):
            # Validate path
            if ".." in source.path or source.path.startswith("/"):
                raise HTTPException(status_code=400, detail="Invalid playbook path")

            playbook_path = playbooks_dir / source.path
            if not playbook_path.exists():
                raise HTTPException(status_code=404, detail=f"Playbook not found: {source.path}")

            result = run_playbook(
                playbook=source.path,
                extra_vars=extra_vars,
                inventory=resolved_inventory,
                playbooks_dir=playbooks_dir,
                options=options,
            )
        elif isinstance(source, LocalRoleSource):
            # Generate wrapper playbook for local role
            fqcn = f"{source.collection}.{source.role}"
            wrapper_content = generate_role_wrapper_playbook(fqcn=fqcn, role_vars=source.role_vars)
            wrapper_path = os.path.join(tmpdir, "wrapper_playbook.yml")
            with open(wrapper_path, "w") as f:
                f.write(wrapper_content)

            result = run_playbook(
                playbook=wrapper_path,
                extra_vars=extra_vars,
                inventory=resolved_inventory,
                envvars={"ANSIBLE_COLLECTIONS_PATH": str(collections_dir)},
                options=options,
            )
        else:
            raise HTTPException(status_code=400, detail="Sync mode only supports local sources")

    return JSONResponse(
        status_code=200,
        content=JobResponse(
            status=result.status,
            rc=result.rc,
            stdout=result.stdout,
            stats=result.stats,
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


@app.get("/health/live")
async def health_live():
    """Liveness probe - returns ok if process is running."""
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(
    redis: Redis = Depends(get_redis),
    session: Session = Depends(get_db_session),
):
    """Readiness probe - returns ok if Redis and MariaDB are reachable."""
    redis_ok, _ = check_redis(redis)
    mariadb_ok, _ = check_mariadb(session)

    if redis_ok and mariadb_ok:
        return {"status": "ok"}

    reasons = []
    if not redis_ok:
        reasons.append("redis unreachable")
    if not mariadb_ok:
        reasons.append("mariadb unreachable")

    return JSONResponse(
        status_code=503,
        content={"status": "error", "reason": ", ".join(reasons)}
    )
