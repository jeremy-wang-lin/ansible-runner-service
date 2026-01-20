# tests/test_schemas.py
import pytest
from pydantic import ValidationError

from ansible_runner_service.schemas import JobRequest, JobResponse


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
