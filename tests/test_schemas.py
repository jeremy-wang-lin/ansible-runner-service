# tests/test_schemas.py
import pytest
from pydantic import ValidationError

from ansible_runner_service.schemas import JobRequest, JobResponse, JobSubmitResponse, JobDetail, JobResultSchema


class TestJobRequest:
    def test_minimal_request(self):
        req = JobRequest(playbook="hello.yml")
        assert req.playbook == "hello.yml"
        assert req.extra_vars == {}
        assert req.inventory == "localhost,"

    def test_full_request(self):
        req = JobRequest(
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="myhost,",
        )
        assert req.extra_vars == {"name": "World"}
        assert req.inventory == "myhost,"

    def test_empty_playbook_rejected(self):
        with pytest.raises(ValidationError):
            JobRequest(playbook="")


class TestJobResponse:
    def test_response_creation(self):
        resp = JobResponse(
            status="successful",
            rc=0,
            stdout="PLAY [Hello]...",
            stats={"localhost": {"ok": 1, "changed": 0, "failures": 0}},
        )
        assert resp.status == "successful"
        assert resp.rc == 0


class TestJobSubmitResponse:
    def test_create_response(self):
        resp = JobSubmitResponse(
            job_id="abc123",
            status="pending",
            created_at="2026-01-21T10:00:00Z",
        )
        assert resp.job_id == "abc123"
        assert resp.status == "pending"


class TestJobDetail:
    def test_create_detail(self):
        detail = JobDetail(
            job_id="abc123",
            status="successful",
            playbook="hello.yml",
            created_at="2026-01-21T10:00:00Z",
            started_at="2026-01-21T10:00:01Z",
            finished_at="2026-01-21T10:00:05Z",
            result=JobResultSchema(
                rc=0,
                stdout="Hello!",
                stats={"localhost": {"ok": 1}},
            ),
        )
        assert detail.job_id == "abc123"
        assert detail.result.rc == 0


class TestJobSummary:
    def test_create_summary(self):
        from ansible_runner_service.schemas import JobSummary

        summary = JobSummary(
            job_id="test-123",
            status="successful",
            playbook="hello.yml",
            created_at="2026-01-24T10:00:00Z",
            finished_at="2026-01-24T10:00:05Z",
        )

        assert summary.job_id == "test-123"
        assert summary.status == "successful"
        assert summary.playbook == "hello.yml"
        assert summary.finished_at == "2026-01-24T10:00:05Z"


class TestJobListResponse:
    def test_create_response(self):
        from ansible_runner_service.schemas import JobListResponse, JobSummary

        response = JobListResponse(
            jobs=[
                JobSummary(
                    job_id="test-123",
                    status="successful",
                    playbook="hello.yml",
                    created_at="2026-01-24T10:00:00Z",
                    finished_at="2026-01-24T10:00:05Z",
                )
            ],
            total=42,
            limit=20,
            offset=0,
        )

        assert len(response.jobs) == 1
        assert response.total == 42
        assert response.limit == 20
        assert response.offset == 0
