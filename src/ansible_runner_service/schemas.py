# src/ansible_runner_service/schemas.py
from typing import Any

from pydantic import BaseModel, Field


class JobRequest(BaseModel):
    playbook: str = Field(..., min_length=1)
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str = "localhost,"


class JobResponse(BaseModel):
    status: str
    rc: int
    stdout: str
    stats: dict[str, Any]
