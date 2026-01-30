# src/ansible_runner_service/schemas.py
from typing import Any, Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class GitPlaybookSource(BaseModel):
    type: Literal["playbook"]
    repo: str
    branch: str = "main"
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("Path traversal not allowed")
        return v


class GitRoleSource(BaseModel):
    type: Literal["role"]
    repo: str
    branch: str = "main"
    role: str
    role_vars: dict[str, Any] = Field(default_factory=dict)


GitSource = Annotated[
    Union[GitPlaybookSource, GitRoleSource],
    Field(discriminator="type"),
]


class JobRequest(BaseModel):
    playbook: str | None = Field(default=None, min_length=1)
    source: GitSource | None = None
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str = "localhost,"

    @model_validator(mode="after")
    def validate_playbook_or_source(self):
        if self.playbook and self.source:
            raise ValueError("Provide either 'playbook' or 'source', not both")
        if not self.playbook and not self.source:
            raise ValueError("Must provide either 'playbook' or 'source'")
        return self


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


class JobSummary(BaseModel):
    """Job summary for list endpoint."""
    job_id: str
    status: str
    playbook: str
    created_at: str
    finished_at: str | None = None


class JobListResponse(BaseModel):
    """Response for GET /jobs list endpoint."""
    jobs: list[JobSummary]
    total: int
    limit: int
    offset: int
