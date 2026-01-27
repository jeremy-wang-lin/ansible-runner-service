# tests/test_db_integration.py
import pytest
from datetime import datetime, timezone
from redis import Redis

pytestmark = pytest.mark.integration


class TestDatabaseIntegration:
    """Integration tests requiring MariaDB."""

    @pytest.fixture
    def db_session(self):
        """Create a test database session."""
        from ansible_runner_service.database import get_engine, get_session

        # Use test database
        engine = get_engine("mysql+pymysql://root:devpassword@localhost:3306/ansible_runner_test")
        Session = get_session(engine)
        session = Session()

        # Create tables
        from ansible_runner_service.models import Base
        Base.metadata.create_all(engine)

        yield session

        # Cleanup
        session.rollback()
        Base.metadata.drop_all(engine)
        session.close()

    def test_create_and_get_job(self, db_session):
        from ansible_runner_service.repository import JobRepository

        repo = JobRepository(db_session)

        # Create
        job = repo.create(
            job_id="test-integration-123",
            playbook="hello.yml",
            extra_vars={"name": "Test"},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )

        assert job.id == "test-integration-123"
        assert job.status == "pending"

        # Get
        retrieved = repo.get("test-integration-123")
        assert retrieved is not None
        assert retrieved.playbook == "hello.yml"

    def test_update_status(self, db_session):
        from ansible_runner_service.repository import JobRepository

        repo = JobRepository(db_session)

        job = repo.create(
            job_id="test-update-123",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )

        result = repo.update_status(
            "test-update-123",
            "successful",
            finished_at=datetime.now(timezone.utc),
            result_rc=0,
            result_stdout="OK",
            result_stats={"localhost": {"ok": 1}},
        )

        assert result is True
        updated = repo.get("test-update-123")
        assert updated.status == "successful"
        assert updated.result_rc == 0

    def test_list_jobs_with_filter(self, db_session):
        from ansible_runner_service.repository import JobRepository

        repo = JobRepository(db_session)

        # Create jobs with different statuses
        for i, status in enumerate(["pending", "successful", "failed"]):
            repo.create(
                job_id=f"test-list-{i}",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
                created_at=datetime.now(timezone.utc),
            )
            if status != "pending":
                repo.update_status(f"test-list-{i}", status)

        # Filter by status
        failed_jobs, total = repo.list_jobs(status="failed")
        assert total == 1
        assert failed_jobs[0].id == "test-list-2"


class TestQueueIntegration:
    """Integration tests for job queue requiring Redis."""

    @pytest.fixture
    def redis_client(self):
        """Create a Redis client for testing."""
        client = Redis(host="localhost", port=6379, db=15)  # Use DB 15 for tests
        yield client
        # Cleanup: flush test database
        client.flushdb()
        client.close()

    def test_enqueue_dequeue_preserves_job_arguments(self, redis_client):
        """Verify job arguments survive the enqueue/dequeue cycle.

        This catches the bug where rq's reserved 'job_id' kwarg was
        intercepting our job_id parameter.
        """
        from rq import Queue
        from ansible_runner_service.queue import enqueue_job

        # Enqueue a job
        enqueue_job(
            job_id="test-queue-123",
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
            redis=redis_client,
        )

        # Fetch job from queue and verify arguments
        queue = Queue(connection=redis_client)
        job_ids = queue.job_ids
        assert len(job_ids) == 1

        from rq.job import Job
        job = Job.fetch(job_ids[0], connection=redis_client)

        assert job is not None
        assert job.kwargs["job_id"] == "test-queue-123"
        assert job.kwargs["playbook"] == "hello.yml"
        assert job.kwargs["extra_vars"] == {"name": "World"}
        assert job.kwargs["inventory"] == "localhost,"

    def test_enqueue_creates_job_with_correct_function(self, redis_client):
        """Verify enqueued job references the correct worker function."""
        from rq import Queue
        from rq.job import Job
        from ansible_runner_service.queue import enqueue_job

        enqueue_job(
            job_id="test-func-123",
            playbook="test.yml",
            extra_vars={},
            inventory="localhost,",
            redis=redis_client,
        )

        queue = Queue(connection=redis_client)
        job_ids = queue.job_ids
        assert len(job_ids) == 1

        job = Job.fetch(job_ids[0], connection=redis_client)

        assert job is not None
        assert job.func_name == "ansible_runner_service.worker.execute_job"
