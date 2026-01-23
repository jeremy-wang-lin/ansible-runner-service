# tests/test_worker.py
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from ansible_runner_service.worker import execute_job
from ansible_runner_service.job_store import JobStatus, JobResult
from ansible_runner_service.runner import RunResult


class TestExecuteJob:
    @patch("ansible_runner_service.worker.get_job_store")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_successful_execution(
        self, mock_get_playbooks_dir, mock_run_playbook, mock_get_job_store
    ):
        mock_store = MagicMock()
        mock_get_job_store.return_value = mock_store
        mock_get_playbooks_dir.return_value = "/playbooks"
        mock_run_playbook.return_value = RunResult(
            status="successful",
            rc=0,
            stdout="Hello, World!",
            stats={"localhost": {"ok": 1}},
        )

        execute_job(
            job_id="test-123",
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
        )

        # Verify status updated to running
        calls = mock_store.update_status.call_args_list
        assert calls[0].args[1] == JobStatus.RUNNING

        # Verify status updated to successful with result
        assert calls[1].args[1] == JobStatus.SUCCESSFUL
        assert calls[1].kwargs["result"].rc == 0

    @patch("ansible_runner_service.worker.get_job_store")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_failed_execution(
        self, mock_get_playbooks_dir, mock_run_playbook, mock_get_job_store
    ):
        mock_store = MagicMock()
        mock_get_job_store.return_value = mock_store
        mock_get_playbooks_dir.return_value = "/playbooks"
        mock_run_playbook.side_effect = Exception("Playbook error")

        execute_job(
            job_id="test-123",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )

        # Verify status updated to failed with error
        calls = mock_store.update_status.call_args_list
        assert calls[1].args[1] == JobStatus.FAILED
        assert "Playbook error" in calls[1].kwargs["error"]
