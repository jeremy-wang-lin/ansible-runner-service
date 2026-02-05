# tests/test_schemas.py
import pytest
from pydantic import ValidationError

from ansible_runner_service.schemas import (
    JobRequest,
    JobResponse,
    JobSubmitResponse,
    JobDetail,
    JobResultSchema,
    GitPlaybookSource,
    GitRoleSource,
    InlineInventory,
    GitInventory,
    ExecutionOptions,
)


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


class TestGitPlaybookSource:
    def test_minimal_source(self):
        source = GitPlaybookSource(
            type="playbook",
            repo="https://dev.azure.com/xxxit/project/_git/repo",
            path="deploy/app.yml",
        )
        assert source.type == "playbook"
        assert source.branch == "main"  # default

    def test_with_branch(self):
        source = GitPlaybookSource(
            type="playbook",
            repo="https://dev.azure.com/xxxit/project/_git/repo",
            branch="v2.0.0",
            path="deploy/app.yml",
        )
        assert source.branch == "v2.0.0"

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            GitPlaybookSource(
                type="playbook",
                repo="https://dev.azure.com/xxxit/project/_git/repo",
                path="../../../etc/passwd",
            )

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError):
            GitPlaybookSource(
                type="playbook",
                repo="https://dev.azure.com/xxxit/project/_git/repo",
                path="/etc/passwd",
            )


class TestGitRoleSource:
    def test_minimal_source(self):
        source = GitRoleSource(
            type="role",
            repo="https://gitlab.company.com/platform-team/collection.git",
            role="nginx",
        )
        assert source.type == "role"
        assert source.branch == "main"
        assert source.role_vars == {}

    def test_with_role_vars(self):
        source = GitRoleSource(
            type="role",
            repo="https://gitlab.company.com/platform-team/collection.git",
            role="nginx",
            role_vars={"nginx_port": 8080},
        )
        assert source.role_vars == {"nginx_port": 8080}

    def test_fqcn_role(self):
        source = GitRoleSource(
            type="role",
            repo="https://gitlab.company.com/platform-team/collection.git",
            role="mycompany.infra.nginx",
        )
        assert source.role == "mycompany.infra.nginx"


class TestJobRequestBackwardCompatibility:
    def test_legacy_local_playbook(self):
        """Existing format still works."""
        request = JobRequest(playbook="hello.yml")
        assert request.playbook == "hello.yml"
        assert request.source is None

    def test_git_playbook_source(self):
        request = JobRequest(
            source={
                "type": "playbook",
                "repo": "https://dev.azure.com/xxxit/p/_git/r",
                "path": "deploy.yml",
            },
        )
        assert request.source is not None
        assert request.source.type == "playbook"
        assert request.playbook is None

    def test_git_role_source(self):
        request = JobRequest(
            source={
                "type": "role",
                "repo": "https://gitlab.company.com/team/col.git",
                "role": "nginx",
            },
        )
        assert request.source is not None
        assert request.source.type == "role"

    def test_must_provide_playbook_or_source(self):
        """Either playbook or source required."""
        with pytest.raises(ValueError, match="playbook.*source"):
            JobRequest()

    def test_cannot_provide_both(self):
        """Cannot provide both playbook and source."""
        with pytest.raises(ValueError, match="playbook.*source"):
            JobRequest(
                playbook="hello.yml",
                source={
                    "type": "playbook",
                    "repo": "https://dev.azure.com/xxxit/p/_git/r",
                    "path": "deploy.yml",
                },
            )


class TestInlineInventory:
    def test_valid_inline(self):
        inv = InlineInventory(
            type="inline",
            data={
                "webservers": {
                    "hosts": {"10.0.1.10": {"http_port": "8080"}, "10.0.1.11": None}
                }
            },
        )
        assert inv.type == "inline"
        assert "webservers" in inv.data

    def test_inline_requires_data(self):
        with pytest.raises(ValidationError):
            InlineInventory(type="inline")


class TestGitInventory:
    def test_valid_git_inventory(self):
        inv = GitInventory(
            type="git",
            repo="https://dev.azure.com/org/project/_git/inventory",
            path="production/hosts.yml",
        )
        assert inv.type == "git"
        assert inv.branch == "main"

    def test_git_inventory_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            GitInventory(
                type="git",
                repo="https://dev.azure.com/org/project/_git/inventory",
                path="../../../etc/passwd",
            )

    def test_git_inventory_absolute_path_rejected(self):
        with pytest.raises(ValueError):
            GitInventory(
                type="git",
                repo="https://dev.azure.com/org/project/_git/inventory",
                path="/etc/hosts",
            )


class TestExecutionOptions:
    def test_defaults(self):
        opts = ExecutionOptions()
        assert opts.check is False
        assert opts.diff is False
        assert opts.tags == []
        assert opts.skip_tags == []
        assert opts.limit is None
        assert opts.verbosity == 0
        assert opts.vault_password_file is None

    def test_all_options(self):
        opts = ExecutionOptions(
            check=True,
            diff=True,
            tags=["deploy", "config"],
            skip_tags=["debug"],
            limit="webservers",
            verbosity=3,
        )
        assert opts.check is True
        assert opts.tags == ["deploy", "config"]
        assert opts.verbosity == 3

    def test_verbosity_range(self):
        with pytest.raises(ValidationError):
            ExecutionOptions(verbosity=5)

    def test_verbosity_negative_rejected(self):
        with pytest.raises(ValidationError):
            ExecutionOptions(verbosity=-1)
