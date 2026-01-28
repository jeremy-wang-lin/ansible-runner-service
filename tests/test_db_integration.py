# tests/test_db_integration.py
import pytest
from datetime import datetime, timezone

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
