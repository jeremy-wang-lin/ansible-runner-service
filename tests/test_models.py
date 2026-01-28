# tests/test_models.py
from datetime import datetime, timezone


class TestJobModel:
    def test_model_attributes(self):
        from ansible_runner_service.models import JobModel

        job = JobModel(
            id="test-123",
            status="pending",
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )

        assert job.id == "test-123"
        assert job.status == "pending"
        assert job.playbook == "hello.yml"
        assert job.extra_vars == {"name": "World"}
        assert job.inventory == "localhost,"
        assert job.started_at is None
        assert job.finished_at is None
        assert job.result_rc is None
        assert job.result_stdout is None
        assert job.result_stats is None
        assert job.error is None

    def test_model_tablename(self):
        from ansible_runner_service.models import JobModel

        assert JobModel.__tablename__ == "jobs"
