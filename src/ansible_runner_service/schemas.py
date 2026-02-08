# src/ansible_runner_service/schemas.py
from typing import Any, Annotated, Literal, TypedDict, Union

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator, model_validator


class PlaybookSourceConfig(TypedDict):
    type: Literal["playbook"]
    repo: str
    branch: str
    path: str


class RoleSourceConfig(TypedDict):
    type: Literal["role"]
    repo: str
    branch: str
    role: str
    role_vars: dict[str, Any]


SourceConfig = PlaybookSourceConfig | RoleSourceConfig


class InlineInventoryConfig(TypedDict):
    type: Literal["inline"]
    data: dict[str, Any]


class GitInventoryConfig(TypedDict):
    type: Literal["git"]
    repo: str
    branch: str
    path: str


InventoryConfig = InlineInventoryConfig | GitInventoryConfig


class ExecutionOptionsConfig(TypedDict, total=False):
    check: bool
    diff: bool
    tags: list[str]
    skip_tags: list[str]
    limit: str
    verbosity: int
    vault_password_file: str


class InlineInventory(BaseModel):
    type: Literal["inline"]
    data: dict[str, Any]


class GitInventory(BaseModel):
    type: Literal["git"]
    repo: str
    branch: str = "main"
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("Path traversal not allowed")
        return v


StructuredInventory = Annotated[
    Union[InlineInventory, GitInventory],
    Field(discriminator="type"),
]


class ExecutionOptions(BaseModel):
    check: bool = False
    diff: bool = False
    tags: list[str] = Field(default_factory=list)
    skip_tags: list[str] = Field(default_factory=list)
    limit: str | None = None
    verbosity: int = Field(default=0, ge=0, le=4)
    vault_password_file: str | None = None


# Local sources (bundled content)
class LocalPlaybookSource(BaseModel):
    type: Literal["local"]
    target: Literal["playbook"]
    path: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("Path traversal not allowed")
        return v


class LocalRoleSource(BaseModel):
    type: Literal["local"]
    target: Literal["role"]
    collection: str
    role: str
    role_vars: dict[str, Any] = Field(default_factory=dict)


# Git sources (remote content)
class GitPlaybookSource(BaseModel):
    type: Literal["git"]
    target: Literal["playbook"]
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
    type: Literal["git"]
    target: Literal["role"]
    repo: str
    branch: str = "main"
    role: str
    role_vars: dict[str, Any] = Field(default_factory=dict)


# Unified source type with two-level discriminator
LocalSource = Annotated[
    Union[LocalPlaybookSource, LocalRoleSource],
    Field(discriminator="target"),
]

GitSource = Annotated[
    Union[GitPlaybookSource, GitRoleSource],
    Field(discriminator="target"),
]


def _source_discriminator(v: Any) -> str:
    """Custom discriminator for unified Source type.

    Uses (type, target) tuple to uniquely identify the source model.
    """
    if isinstance(v, dict):
        type_val = v.get("type", "")
        target_val = v.get("target", "")
    else:
        type_val = getattr(v, "type", "")
        target_val = getattr(v, "target", "")
    return f"{type_val}_{target_val}"


Source = Annotated[
    Union[
        Annotated[LocalPlaybookSource, Tag("local_playbook")],
        Annotated[LocalRoleSource, Tag("local_role")],
        Annotated[GitPlaybookSource, Tag("git_playbook")],
        Annotated[GitRoleSource, Tag("git_role")],
    ],
    Discriminator(_source_discriminator),
]


class JobRequest(BaseModel):
    source: Source
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str | StructuredInventory = "localhost,"
    options: ExecutionOptions = Field(default_factory=ExecutionOptions)


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
