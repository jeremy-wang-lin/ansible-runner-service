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


class TestJobModelSourceFields:
    def test_source_fields_exist(self):
        from ansible_runner_service.models import JobModel

        job = JobModel(
            id="test-123",
            status="pending",
            playbook="hello.yml",
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
            source_type="playbook",
            source_repo="https://dev.azure.com/xxxit/p/_git/r",
            source_branch="main",
        )
        assert job.source_type == "playbook"
        assert job.source_repo == "https://dev.azure.com/xxxit/p/_git/r"
        assert job.source_branch == "main"

    def test_source_fields_default(self):
        from ansible_runner_service.models import JobModel

        job = JobModel(
            id="test-456",
            status="pending",
            playbook="hello.yml",
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )
        assert job.source_type == "local"
        assert job.source_repo is None
        assert job.source_branch is None


def test_job_model_has_source_target():
    from ansible_runner_service.models import JobModel

    job = JobModel(
        id="test-123",
        status="pending",
        playbook="hello.yml",
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
        source_type="local",
        source_target="playbook",
    )
    assert job.source_target == "playbook"


def test_job_model_source_target_defaults():
    from ansible_runner_service.models import JobModel

    job = JobModel(
        id="test-123",
        status="pending",
        playbook="hello.yml",
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
    )
    assert job.source_type == "local"
    assert job.source_target == "playbook"
