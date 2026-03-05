# src/ansible_runner_service/models.py
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import String, Integer, Text, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobModel(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    playbook: Mapped[str] = mapped_column(String(255), nullable=False)
    extra_vars: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    inventory: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_rc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, insert_default="local")
    source_target: Mapped[str] = mapped_column(String(20), nullable=False, insert_default="playbook")
    source_repo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("source_type", "local")
        kwargs.setdefault("source_target", "playbook")
        super().__init__(**kwargs)


class ClientModel(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("id", str(uuid4()))
        kwargs.setdefault("created_at", datetime.now(timezone.utc))
        super().__init__(**kwargs)
