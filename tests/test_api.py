# tests/test_api.py
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from ansible_runner_service.main import app, get_playbooks_dir


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
            "/api/v1/jobs",
            json={"playbook": "hello.yml"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert data["rc"] == 0
        assert "Hello, World!" in data["stdout"]

    async def test_with_extra_vars(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "hello.yml", "extra_vars": {"name": "Claude"}},
        )

        assert response.status_code == 200
        assert "Hello, Claude!" in response.json()["stdout"]
