# tests/test_api.py
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

from ansible_runner_service.main import app, get_playbooks_dir, get_job_store, get_redis
from ansible_runner_service.job_store import Job, JobStatus, JobResult


# Override playbooks directory for tests
@pytest.fixture
def playbooks_dir(tmp_path: Path):
    # Create test playbook
    playbook = tmp_path / "hello.yml"
    playbook.write_text("""
---
- name: Hello
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Greet
      ansible.builtin.debug:
        msg: "Hello, {{ name | default('World') }}!"
""")
    return tmp_path


@pytest.fixture
def client(playbooks_dir: Path):
    app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


class TestPostJobs:
    async def test_successful_job(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs?sync=true",
            json={"playbook": "hello.yml"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert data["rc"] == 0
        assert "Hello, World!" in data["stdout"]

    async def test_with_extra_vars(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs?sync=true",
            json={"playbook": "hello.yml", "extra_vars": {"name": "Claude"}},
        )

        assert response.status_code == 200
        assert "Hello, Claude!" in response.json()["stdout"]

    async def test_playbook_not_found(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "nonexistent.yml"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_path_traversal_blocked(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "../etc/passwd"},
        )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()


class TestAsyncJobs:
    async def test_submit_async_job(self, playbooks_dir: Path):
        """Default behavior - async submission."""
        mock_job_store = MagicMock()
        mock_job_store.create_job.return_value = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime(2026, 1, 21, 10, 0, 0, tzinfo=timezone.utc),
        )
        mock_redis = MagicMock()

        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
        app.dependency_overrides[get_job_store] = lambda: mock_job_store
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                with patch("ansible_runner_service.main.enqueue_job") as mock_enqueue:
                    response = await client.post(
                        "/api/v1/jobs",
                        json={"playbook": "hello.yml"},
                    )

            assert response.status_code == 202
            data = response.json()
            assert data["job_id"] == "test-123"
            assert data["status"] == "pending"
            mock_enqueue.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    async def test_submit_sync_job(self, client: AsyncClient):
        """Sync mode with ?sync=true."""
        response = await client.post(
            "/api/v1/jobs?sync=true",
            json={"playbook": "hello.yml"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert "Hello, World!" in data["stdout"]


class TestGetJob:
    async def test_get_job(self, playbooks_dir: Path):
        mock_job_store = MagicMock()
        mock_job_store.get_job.return_value = Job(
            job_id="test-123",
            status=JobStatus.SUCCESSFUL,
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime(2026, 1, 21, 10, 0, 0, tzinfo=timezone.utc),
            started_at=datetime(2026, 1, 21, 10, 0, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 21, 10, 0, 5, tzinfo=timezone.utc),
            result=JobResult(rc=0, stdout="Hello!", stats={}),
            error=None,
        )

        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
        app.dependency_overrides[get_job_store] = lambda: mock_job_store

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/v1/jobs/test-123")

            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "test-123"
            assert data["status"] == "successful"
        finally:
            app.dependency_overrides.clear()

    async def test_get_job_not_found(self, playbooks_dir: Path):
        mock_job_store = MagicMock()
        mock_job_store.get_job.return_value = None

        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
        app.dependency_overrides[get_job_store] = lambda: mock_job_store

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/v1/jobs/nonexistent")

            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()
