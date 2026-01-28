# tests/test_repository.py
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock


class TestJobRepository:
    def test_create_job(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        repo = JobRepository(mock_session)

        job = repo.create(
            job_id="test-123",
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
            created_at=datetime(2026, 1, 24, 10, 0, 0, tzinfo=timezone.utc),
        )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

        added_job = mock_session.add.call_args[0][0]
        assert added_job.id == "test-123"
        assert added_job.status == "pending"
        assert added_job.playbook == "hello.yml"

    def test_get_job_found(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        mock_job = JobModel(
            id="test-123",
            status="pending",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )
        mock_session.get.return_value = mock_job

        repo = JobRepository(mock_session)
        job = repo.get("test-123")

        assert job == mock_job
        mock_session.get.assert_called_once_with(JobModel, "test-123")

    def test_get_job_not_found(self):
        from ansible_runner_service.repository import JobRepository

        mock_session = MagicMock()
        mock_session.get.return_value = None

        repo = JobRepository(mock_session)
        job = repo.get("nonexistent")

        assert job is None

    def test_update_status(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        mock_job = JobModel(
            id="test-123",
            status="pending",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )
        mock_session.get.return_value = mock_job

        repo = JobRepository(mock_session)
        now = datetime.now(timezone.utc)
        result = repo.update_status("test-123", "running", started_at=now)

        assert result is True
        assert mock_job.status == "running"
        assert mock_job.started_at == now
        mock_session.commit.assert_called_once()

    def test_update_status_job_not_found(self):
        from ansible_runner_service.repository import JobRepository

        mock_session = MagicMock()
        mock_session.get.return_value = None

        repo = JobRepository(mock_session)
        result = repo.update_status("nonexistent", "running")

        assert result is False
        mock_session.commit.assert_not_called()

    def test_list_jobs(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        mock_jobs = [
            JobModel(
                id="job-1",
                status="successful",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
                created_at=datetime.now(timezone.utc),
            ),
        ]

        # Mock the query chain
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = mock_jobs
        mock_query.count.return_value = 1
        mock_session.query.return_value = mock_query

        repo = JobRepository(mock_session)
        jobs, total = repo.list_jobs(limit=20, offset=0)

        assert len(jobs) == 1
        assert total == 1

    def test_list_jobs_with_status_filter(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        mock_query.count.return_value = 0
        mock_session.query.return_value = mock_query

        repo = JobRepository(mock_session)
        jobs, total = repo.list_jobs(status="failed", limit=20, offset=0)

        # Verify filter was called with status condition
        mock_query.filter.assert_called_once()
