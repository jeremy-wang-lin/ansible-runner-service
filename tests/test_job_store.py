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


class TestJobStoreWithDB:
    def test_create_job_writes_to_db(self):
        from ansible_runner_service.job_store import JobStore

        mock_redis = MagicMock()
        mock_repo = MagicMock()

        store = JobStore(mock_redis, repository=mock_repo)
        job = store.create_job(
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
        )

        # Verify DB write
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        assert call_kwargs["playbook"] == "hello.yml"
        assert call_kwargs["extra_vars"] == {"name": "World"}
        assert call_kwargs["inventory"] == "localhost,"

    def test_update_status_writes_to_db(self):
        from ansible_runner_service.job_store import JobStore, JobStatus
        from datetime import datetime, timezone

        mock_redis = MagicMock()
        mock_repo = MagicMock()

        store = JobStore(mock_redis, repository=mock_repo)
        now = datetime.now(timezone.utc)

        store.update_status(
            "test-123",
            JobStatus.RUNNING,
            started_at=now,
        )

        # Verify DB update
        mock_repo.update_status.assert_called_once_with(
            "test-123",
            "running",
            started_at=now,
            finished_at=None,
            result_rc=None,
            result_stdout=None,
            result_stats=None,
            error=None,
        )

    def test_create_job_works_without_repo(self):
        """Backwards compatibility: works without repository."""
        from ansible_runner_service.job_store import JobStore

        mock_redis = MagicMock()
        store = JobStore(mock_redis)  # No repository

        job = store.create_job(
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )

        assert job.playbook == "hello.yml"
