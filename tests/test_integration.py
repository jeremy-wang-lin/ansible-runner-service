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

from ansible_runner_service.main import app, get_playbooks_dir, get_redis, get_job_store, get_repository
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


class TestRedisTTLFallback:
    """Test that job data survives Redis TTL expiration by falling back to DB."""

    @pytest.fixture
    def db_session(self):
        """Create a test database session."""
        from ansible_runner_service.database import get_engine, get_session
        from ansible_runner_service.models import Base

        engine = get_engine("mysql+pymysql://root:devpassword@localhost:3306/ansible_runner_test")
        Session = get_session(engine)
        session = Session()

        Base.metadata.create_all(engine)

        yield session

        session.rollback()
        Base.metadata.drop_all(engine)
        session.close()

    @pytest.fixture
    def repository(self, db_session):
        from ansible_runner_service.repository import JobRepository
        return JobRepository(db_session)

    @pytest.fixture
    def job_store_with_db(self, redis, repository):
        return JobStore(redis, repository=repository)

    @pytest.fixture
    def client_with_db(self, playbooks_dir: Path, redis: Redis, job_store_with_db: JobStore, repository):
        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
        app.dependency_overrides[get_redis] = lambda: redis
        app.dependency_overrides[get_job_store] = lambda: job_store_with_db
        from ansible_runner_service.main import get_repository
        app.dependency_overrides[get_repository] = lambda: repository
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        app.dependency_overrides.clear()

    async def test_job_survives_redis_ttl_expiration(
        self, client_with_db: AsyncClient, redis: Redis, job_store_with_db: JobStore, playbooks_dir: Path
    ):
        """Verify job data is retrievable from DB after Redis key expires/deleted."""
        from datetime import datetime, timezone
        from ansible_runner_service.job_store import JobStatus, JobResult
        from ansible_runner_service.runner import run_playbook

        # Submit a job (creates in both Redis and DB)
        response = await client_with_db.post(
            "/api/v1/jobs",
            json={"playbook": "hello.yml"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        # Simulate worker execution using the test's job_store (writes to test DB)
        job_store_with_db.update_status(
            job_id,
            JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        # Actually run the playbook
        run_result = run_playbook(
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            playbooks_dir=playbooks_dir,
        )

        # Update status to successful (writes to both Redis and test DB)
        job_store_with_db.update_status(
            job_id,
            JobStatus.SUCCESSFUL,
            finished_at=datetime.now(timezone.utc),
            result=JobResult(
                rc=run_result.rc,
                stdout=run_result.stdout,
                stats=run_result.stats,
            ),
        )

        # Verify job exists in Redis
        assert redis.exists(f"job:{job_id}") == 1

        # Verify job is retrievable via API (from Redis)
        response = await client_with_db.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"

        # Simulate TTL expiration by deleting Redis key
        redis.delete(f"job:{job_id}")

        # Verify Redis key is gone
        assert redis.exists(f"job:{job_id}") == 0

        # Verify job is STILL retrievable via API (now from DB fallback)
        response = await client_with_db.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "successful"
        assert data["playbook"] == "hello.yml"
        assert data["result"] is not None
        assert "Hello, World!" in data["result"]["stdout"]


class TestGitPlaybookFlow:
    """Integration test for the git clone → run playbook flow.

    Uses a local bare git repo to exercise real git operations and
    ansible-runner execution without network access or credentials.
    """

    @pytest.fixture
    def local_git_repo(self, tmp_path):
        """Create a local bare git repo with a playbook."""
        import subprocess

        # Create a working repo, add a playbook, then create a bare clone
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        playbook_dir = work_dir / "deploy"
        playbook_dir.mkdir()
        (playbook_dir / "app.yml").write_text(
            "---\n"
            "- name: Deploy\n"
            "  hosts: localhost\n"
            "  connection: local\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: Report\n"
            "      ansible.builtin.debug:\n"
            '        msg: "Deployed {{ app_name | default(\'myapp\') }}"\n'
        )

        subprocess.run(["git", "init"], cwd=work_dir, capture_output=True, check=True)
        subprocess.run(["git", "add", "."], cwd=work_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=work_dir,
            capture_output=True,
            check=True,
            env={**__import__("os").environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
        )

        bare_dir = tmp_path / "repo.git"
        subprocess.run(
            ["git", "clone", "--bare", str(work_dir), str(bare_dir)],
            capture_output=True,
            check=True,
        )
        return bare_dir

    def test_clone_and_run_playbook(self, local_git_repo, tmp_path):
        """Real git clone → path validation → ansible-runner execution."""
        import subprocess
        from unittest.mock import patch, MagicMock
        from ansible_runner_service.worker import _execute_git_playbook

        mock_provider = MagicMock()
        mock_provider.get_credential.return_value = "unused"
        mock_provider.type = "azure"

        # Patch clone_repo to do a real unauthenticated git clone
        def real_clone(repo_url, branch, target_dir, provider):
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", branch,
                 "--single-branch", str(local_git_repo), target_dir],
                check=True, capture_output=True, text=True,
            )

        source_config = {
            "type": "playbook",
            "repo": "https://fake.example.com/org/repo",
            "branch": "main",
            "path": "deploy/app.yml",
        }

        with patch("ansible_runner_service.worker.load_providers"):
            with patch("ansible_runner_service.worker.validate_repo_url", return_value=mock_provider):
                with patch("ansible_runner_service.worker.clone_repo", side_effect=real_clone):
                    result = _execute_git_playbook(source_config, {"app_name": "testapp"}, "localhost,")

        assert result.rc == 0
        assert "Deployed testapp" in result.stdout

    def test_clone_with_symlink_escape_blocked(self, tmp_path):
        """Symlink escape in cloned repo is caught before execution."""
        import subprocess
        from unittest.mock import patch, MagicMock
        from ansible_runner_service.worker import _execute_git_playbook

        mock_provider = MagicMock()
        mock_provider.get_credential.return_value = "unused"
        mock_provider.type = "azure"

        # Create escape target
        secret_dir = tmp_path / "secret"
        secret_dir.mkdir()
        (secret_dir / "evil.yml").write_text("---\n- name: Evil\n  hosts: localhost\n  tasks: []\n")

        def clone_with_symlink(repo_url, branch, target_dir, provider):
            import os
            os.makedirs(target_dir)
            os.symlink(str(secret_dir), os.path.join(target_dir, "escape"))

        source_config = {
            "type": "playbook",
            "repo": "https://fake.example.com/org/repo",
            "branch": "main",
            "path": "escape/evil.yml",
        }

        with patch("ansible_runner_service.worker.load_providers"):
            with patch("ansible_runner_service.worker.validate_repo_url", return_value=mock_provider):
                with patch("ansible_runner_service.worker.clone_repo", side_effect=clone_with_symlink):
                    with pytest.raises(RuntimeError, match="outside.*repo"):
                        _execute_git_playbook(source_config, {}, "localhost,")


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
