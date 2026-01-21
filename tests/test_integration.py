# tests/test_integration.py
"""Integration tests requiring Redis.

Run with: pytest tests/test_integration.py -v -m integration
Requires: docker-compose up -d
"""
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
