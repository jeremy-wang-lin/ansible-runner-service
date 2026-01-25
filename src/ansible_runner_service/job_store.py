# src/ansible_runner_service/job_store.py
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

from redis import Redis

if TYPE_CHECKING:
    from ansible_runner_service.repository import JobRepository


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESSFUL = "successful"
    FAILED = "failed"


@dataclass
class JobResult:
    rc: int
    stdout: str
    stats: dict[str, Any]


@dataclass
class Job:
    job_id: str
    status: JobStatus
    playbook: str
    extra_vars: dict[str, Any]
    inventory: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: JobResult | None = None
    error: str | None = None


class JobStore:
    def __init__(
        self,
        redis: Redis,
        ttl: int = 86400,
        repository: "JobRepository | None" = None,
    ):
        self.redis = redis
        self.ttl = ttl  # 24 hours default
        self.repository = repository

    def _job_key(self, job_id: str) -> str:
        return f"job:{job_id}"

    def create_job(
        self,
        playbook: str,
        extra_vars: dict[str, Any],
        inventory: str,
    ) -> Job:
        job = Job(
            job_id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            playbook=playbook,
            extra_vars=extra_vars,
            inventory=inventory,
            created_at=datetime.now(timezone.utc),
        )
        self._save_job(job)

        # Write-through to DB
        if self.repository:
            self.repository.create(
                job_id=job.job_id,
                playbook=playbook,
                extra_vars=extra_vars,
                inventory=inventory,
                created_at=job.created_at,
            )

        return job

    def get_job(self, job_id: str) -> Job | None:
        data = self.redis.hgetall(self._job_key(job_id))
        if not data:
            return None
        return self._deserialize_job(data)

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        result: JobResult | None = None,
        error: str | None = None,
    ) -> None:
        updates = {"status": status.value}
        if started_at:
            updates["started_at"] = started_at.isoformat()
        if finished_at:
            updates["finished_at"] = finished_at.isoformat()
        if result:
            updates["result"] = json.dumps(asdict(result))
        if error:
            updates["error"] = error
        self.redis.hset(self._job_key(job_id), mapping=updates)

        # Write-through to DB
        if self.repository:
            self.repository.update_status(
                job_id,
                status.value,
                started_at=started_at,
                finished_at=finished_at,
                result_rc=result.rc if result else None,
                result_stdout=result.stdout if result else None,
                result_stats=result.stats if result else None,
                error=error,
            )

    def _save_job(self, job: Job) -> None:
        data = {
            "job_id": job.job_id,
            "status": job.status.value,
            "playbook": job.playbook,
            "extra_vars": json.dumps(job.extra_vars),
            "inventory": job.inventory,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else "",
            "finished_at": job.finished_at.isoformat() if job.finished_at else "",
            "result": json.dumps(asdict(job.result)) if job.result else "",
            "error": job.error or "",
        }
        self.redis.hset(self._job_key(job.job_id), mapping=data)
        self.redis.expire(self._job_key(job.job_id), self.ttl)

    def _deserialize_job(self, data: dict[bytes, bytes]) -> Job:
        def get_str(key: str) -> str:
            return data.get(key.encode(), b"").decode()

        result_str = get_str("result")
        result = None
        if result_str:
            result_dict = json.loads(result_str)
            result = JobResult(**result_dict)

        started_str = get_str("started_at")
        finished_str = get_str("finished_at")

        return Job(
            job_id=get_str("job_id"),
            status=JobStatus(get_str("status")),
            playbook=get_str("playbook"),
            extra_vars=json.loads(get_str("extra_vars")),
            inventory=get_str("inventory"),
            created_at=datetime.fromisoformat(get_str("created_at")),
            started_at=datetime.fromisoformat(started_str) if started_str else None,
            finished_at=datetime.fromisoformat(finished_str) if finished_str else None,
            result=result,
            error=get_str("error") or None,
        )
