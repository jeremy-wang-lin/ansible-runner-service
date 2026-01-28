# src/ansible_runner_service/models.py
from datetime import datetime
from typing import Any

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
    inventory: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_rc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
