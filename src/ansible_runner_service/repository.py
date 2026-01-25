# src/ansible_runner_service/repository.py
from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from ansible_runner_service.models import JobModel


class JobRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        job_id: str,
        playbook: str,
        extra_vars: dict[str, Any],
        inventory: str,
        created_at: datetime,
    ) -> JobModel:
        """Create a new job record."""
        job = JobModel(
            id=job_id,
            status="pending",
            playbook=playbook,
            extra_vars=extra_vars,
            inventory=inventory,
            created_at=created_at,
        )
        self.session.add(job)
        self.session.commit()
        return job

    def get(self, job_id: str) -> JobModel | None:
        """Get a job by ID."""
        return self.session.get(JobModel, job_id)

    def update_status(
        self,
        job_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        result_rc: int | None = None,
        result_stdout: str | None = None,
        result_stats: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Update job status and related fields. Returns True if job was found and updated."""
        job = self.get(job_id)
        if job is None:
            return False

        job.status = status
        if started_at is not None:
            job.started_at = started_at
        if finished_at is not None:
            job.finished_at = finished_at
        if result_rc is not None:
            job.result_rc = result_rc
        if result_stdout is not None:
            job.result_stdout = result_stdout
        if result_stats is not None:
            job.result_stats = result_stats
        if error is not None:
            job.error = error

        self.session.commit()
        return True

    def list_jobs(
        self,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JobModel], int]:
        """List jobs with optional filtering and pagination."""
        query = self.session.query(JobModel)

        if status:
            query = query.filter(JobModel.status == status)

        # Get total count before pagination
        total = query.count()

        # Apply ordering and pagination
        jobs = (
            query
            .order_by(desc(JobModel.created_at))
            .offset(offset)
            .limit(limit)
            .all()
        )

        return jobs, total
