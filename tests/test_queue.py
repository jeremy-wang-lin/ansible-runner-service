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
        # Arguments are passed via explicit kwargs dict to avoid rq reserved keyword collision
        job_kwargs = call_args.kwargs["kwargs"]
        assert job_kwargs["job_id"] == "test-123"
        assert job_kwargs["playbook"] == "hello.yml"
        assert job_kwargs["extra_vars"] == {"name": "World"}
        assert job_kwargs["inventory"] == "localhost,"


class TestEnqueueJobWithSource:
    def test_enqueue_with_source_config(self):
        mock_queue = MagicMock()

        with patch("ansible_runner_service.queue.Queue", return_value=mock_queue):
            enqueue_job(
                job_id="test-git-123",
                playbook="deploy/app.yml",
                extra_vars={},
                inventory="localhost,",
                source_config={
                    "type": "playbook",
                    "repo": "https://dev.azure.com/xxxit/p/_git/r",
                    "branch": "main",
                    "path": "deploy/app.yml",
                },
            )

        call_args = mock_queue.enqueue.call_args
        job_kwargs = call_args.kwargs["kwargs"]
        assert job_kwargs["source_config"]["type"] == "playbook"
        assert job_kwargs["source_config"]["repo"] == "https://dev.azure.com/xxxit/p/_git/r"

    def test_enqueue_without_source_config(self):
        mock_queue = MagicMock()

        with patch("ansible_runner_service.queue.Queue", return_value=mock_queue):
            enqueue_job(
                job_id="test-local-123",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
            )

        call_args = mock_queue.enqueue.call_args
        job_kwargs = call_args.kwargs["kwargs"]
        assert job_kwargs["source_config"] is None


class TestEnqueueJobWithOptions:
    def test_enqueue_with_options(self):
        mock_queue = MagicMock()

        options = {"forks": 10, "verbosity": 2}

        with patch("ansible_runner_service.queue.Queue", return_value=mock_queue):
            enqueue_job(
                job_id="test-opts-123",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
                options=options,
            )

        call_args = mock_queue.enqueue.call_args
        job_kwargs = call_args.kwargs["kwargs"]
        assert job_kwargs["options"] == options
        assert job_kwargs["options"]["forks"] == 10
        assert job_kwargs["options"]["verbosity"] == 2

    def test_enqueue_without_options(self):
        mock_queue = MagicMock()

        with patch("ansible_runner_service.queue.Queue", return_value=mock_queue):
            enqueue_job(
                job_id="test-no-opts-123",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
            )

        call_args = mock_queue.enqueue.call_args
        job_kwargs = call_args.kwargs["kwargs"]
        assert job_kwargs["options"] is None
