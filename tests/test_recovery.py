# tests/test_recovery.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


class TestRecoverStaleJobs:
    def test_marks_stale_running_jobs_as_failed(self):
        from ansible_runner_service.main import recover_stale_jobs
        from ansible_runner_service.models import JobModel

        # Job that was "running" for over 1 hour and not in Redis
        stale_job = JobModel(
            id="stale-123",
            status="running",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        mock_repo = MagicMock()
        mock_repo.list_stale_running_jobs.return_value = [stale_job]

        mock_redis = MagicMock()
        mock_redis.exists.return_value = False  # Not in Redis

        recover_stale_jobs(mock_repo, mock_redis)

        mock_repo.update_status.assert_called_once_with(
            "stale-123",
            "failed",
            error="Worker crashed or timed out",
        )

    def test_skips_jobs_still_in_redis(self):
        from ansible_runner_service.main import recover_stale_jobs
        from ansible_runner_service.models import JobModel

        stale_job = JobModel(
            id="stale-123",
            status="running",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        mock_repo = MagicMock()
        mock_repo.list_stale_running_jobs.return_value = [stale_job]

        mock_redis = MagicMock()
        mock_redis.exists.return_value = True  # Still in Redis

        recover_stale_jobs(mock_repo, mock_redis)

        # Should NOT update since job is still active in Redis
        mock_repo.update_status.assert_not_called()
