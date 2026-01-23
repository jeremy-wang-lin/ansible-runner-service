# tests/test_queue.py
import pytest
from unittest.mock import MagicMock, patch

from ansible_runner_service.queue import enqueue_job


class TestEnqueueJob:
    def test_enqueue_job(self):
        mock_queue = MagicMock()

        with patch("ansible_runner_service.queue.Queue", return_value=mock_queue):
            enqueue_job(
                job_id="test-123",
                playbook="hello.yml",
                extra_vars={"name": "World"},
                inventory="localhost,",
            )

        mock_queue.enqueue.assert_called_once()
        call_args = mock_queue.enqueue.call_args
        assert call_args.kwargs["job_id"] == "test-123"
