# tests/test_job_store.py
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from ansible_runner_service.job_store import JobStore, Job, JobStatus


@pytest.fixture
def mock_redis():
    return MagicMock()


@pytest.fixture
def job_store(mock_redis):
    return JobStore(mock_redis)


class TestJobStore:
    def test_create_job(self, job_store, mock_redis):
        job = job_store.create_job(
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
        )

        assert job.job_id is not None
        assert job.status == JobStatus.PENDING
        assert job.playbook == "hello.yml"
        assert job.extra_vars == {"name": "World"}
        assert job.created_at is not None
        mock_redis.hset.assert_called()

    def test_get_job(self, job_store, mock_redis):
        mock_redis.hgetall.return_value = {
            b"job_id": b"test-123",
            b"status": b"pending",
            b"playbook": b"hello.yml",
            b"extra_vars": b'{"name": "World"}',
            b"inventory": b"localhost,",
            b"created_at": b"2026-01-21T10:00:00+00:00",
            b"started_at": b"",
            b"finished_at": b"",
            b"result": b"",
            b"error": b"",
        }

        job = job_store.get_job("test-123")

        assert job is not None
        assert job.job_id == "test-123"
        assert job.status == JobStatus.PENDING
        mock_redis.hgetall.assert_called_with("job:test-123")

    def test_get_job_not_found(self, job_store, mock_redis):
        mock_redis.hgetall.return_value = {}

        job = job_store.get_job("nonexistent")

        assert job is None

    def test_update_job_status(self, job_store, mock_redis):
        job_store.update_status("test-123", JobStatus.RUNNING)

        mock_redis.hset.assert_called()
