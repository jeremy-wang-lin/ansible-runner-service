# src/ansible_runner_service/schemas.py
from typing import Any

from pydantic import BaseModel, Field


class JobRequest(BaseModel):
    playbook: str = Field(..., min_length=1)
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str = "localhost,"


class JobResponse(BaseModel):
    """Sync response - full result."""
    status: str
    rc: int
    stdout: str
    stats: dict[str, Any]


class JobSubmitResponse(BaseModel):
    """Async response - job reference."""
    job_id: str
    status: str
    created_at: str


class JobResultSchema(BaseModel):
    """Job execution result."""
    rc: int
    stdout: str
    stats: dict[str, Any]


class JobDetail(BaseModel):
    """Full job details for GET /jobs/{id}."""
    job_id: str
    status: str
    playbook: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: JobResultSchema | None = None
    error: str | None = None
