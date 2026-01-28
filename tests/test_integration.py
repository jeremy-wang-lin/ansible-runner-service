# tests/test_integration.py
"""End-to-end integration tests.

Run with: pytest tests/test_integration.py -v -m integration
Requires: docker-compose up -d

For TestE2EWithWorker tests, also run: rq worker
"""
import asyncio
import pytest
from pathlib import Path

from redis import Redis
from httpx import AsyncClient, ASGITransport

from ansible_runner_service.main import app, get_playbooks_dir, get_redis, get_job_store
from ansible_runner_service.job_store import JobStore


pytestmark = pytest.mark.integration


@pytest.fixture
def redis():
    """Real Redis connection."""
    r = Redis()
    r.flushdb()  # Clean slate
    yield r
    r.flushdb()


@pytest.fixture
def job_store(redis):
    return JobStore(redis)


@pytest.fixture
def playbooks_dir(tmp_path: Path):
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
def client(playbooks_dir: Path, redis: Redis, job_store: JobStore):
    app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_job_store] = lambda: job_store
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


class TestAsyncFlow:
    async def test_submit_and_poll(self, client: AsyncClient, job_store: JobStore):
        """Submit job async, poll until complete."""
        # Submit
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "hello.yml"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        # Simulate worker execution (in real test, worker would run separately)
        from ansible_runner_service.worker import execute_job
        execute_job(
            job_id=job_id,
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )

        # Poll
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert "Hello, World!" in data["result"]["stdout"]

    async def test_sync_mode(self, client: AsyncClient):
        """Sync mode bypasses queue."""
        response = await client.post(
            "/api/v1/jobs?sync=true",
            json={"playbook": "hello.yml"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert "Hello, World!" in data["stdout"]


@pytest.mark.e2e
class TestE2EWithWorker:
    """End-to-end tests requiring a running rq worker.

    Prerequisites:
        1. docker-compose up -d (Redis + MariaDB)
        2. rq worker (in separate terminal)

    Run with: pytest tests/test_integration.py::TestE2EWithWorker -v -m "integration and e2e"
    """

    @pytest.fixture
    def e2e_client(self, playbooks_dir: Path, redis: Redis):
        """Client for E2E tests - no dependency overrides for job_store."""
        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
        app.dependency_overrides[get_redis] = lambda: redis
        # Don't override get_job_store - let it use real implementation
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        app.dependency_overrides.clear()

    async def test_submit_job_with_extra_vars_e2e(self, e2e_client: AsyncClient, redis: Redis):
        """E2E: Submit job with extra_vars, worker processes it, verify result.

        This test verifies the full flow through rq, catching bugs like
        the job_id kwarg collision where arguments weren't passed correctly
        to the worker.

        Requires: rq worker running
        """
        # Submit job with custom extra_vars
        response = await e2e_client.post(
            "/api/v1/jobs",
            json={
                "playbook": "hello.yml",
                "extra_vars": {"name": "E2E-Test"},
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        # Poll until job completes (with timeout)
        max_attempts = 30
        for attempt in range(max_attempts):
            response = await e2e_client.get(f"/api/v1/jobs/{job_id}")
            assert response.status_code == 200
            data = response.json()

            if data["status"] in ("successful", "failed"):
                break

            await asyncio.sleep(0.5)
        else:
            pytest.fail(f"Job {job_id} did not complete within timeout. Is rq worker running?")

        # Verify job succeeded and extra_vars were passed correctly
        assert data["status"] == "successful", f"Job failed: {data.get('error')}"
        assert "Hello, E2E-Test!" in data["result"]["stdout"], (
            "extra_vars not passed correctly through rq to worker"
        )
