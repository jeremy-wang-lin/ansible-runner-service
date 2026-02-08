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
        req = JobRequest(source={"type": "local", "target": "playbook", "path": "hello.yml"})
        assert req.source.path == "hello.yml"
        assert req.extra_vars == {}
        assert req.inventory == "localhost,"

    def test_full_request(self):
        req = JobRequest(
            source={"type": "local", "target": "playbook", "path": "hello.yml"},
            extra_vars={"name": "World"},
            inventory="myhost,",
        )
        assert req.extra_vars == {"name": "World"}
        assert req.inventory == "myhost,"

    def test_empty_path_rejected(self):
        # Empty path in local playbook source should be rejected
        with pytest.raises(ValidationError):
            JobRequest(source={"type": "local", "target": "playbook", "path": ""})


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
            type="git",
            target="playbook",
            repo="https://dev.azure.com/xxxit/project/_git/repo",
            path="deploy/app.yml",
        )
        assert source.type == "git"
        assert source.target == "playbook"
        assert source.branch == "main"  # default

    def test_with_branch(self):
        source = GitPlaybookSource(
            type="git",
            target="playbook",
            repo="https://dev.azure.com/xxxit/project/_git/repo",
            branch="v2.0.0",
            path="deploy/app.yml",
        )
        assert source.branch == "v2.0.0"

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            GitPlaybookSource(
                type="git",
                target="playbook",
                repo="https://dev.azure.com/xxxit/project/_git/repo",
                path="../../../etc/passwd",
            )

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError):
            GitPlaybookSource(
                type="git",
                target="playbook",
                repo="https://dev.azure.com/xxxit/project/_git/repo",
                path="/etc/passwd",
            )


class TestGitRoleSource:
    def test_minimal_source(self):
        source = GitRoleSource(
            type="git",
            target="role",
            repo="https://gitlab.company.com/platform-team/collection.git",
            role="nginx",
        )
        assert source.type == "git"
        assert source.target == "role"
        assert source.branch == "main"
        assert source.role_vars == {}

    def test_with_role_vars(self):
        source = GitRoleSource(
            type="git",
            target="role",
            repo="https://gitlab.company.com/platform-team/collection.git",
            role="nginx",
            role_vars={"nginx_port": 8080},
        )
        assert source.role_vars == {"nginx_port": 8080}

    def test_fqcn_role(self):
        source = GitRoleSource(
            type="git",
            target="role",
            repo="https://gitlab.company.com/platform-team/collection.git",
            role="mycompany.infra.nginx",
        )
        assert source.role == "mycompany.infra.nginx"

    def test_empty_role_rejected(self):
        with pytest.raises(ValidationError):
            GitRoleSource(
                type="git",
                target="role",
                repo="https://gitlab.company.com/platform-team/collection.git",
                role="",
            )


class TestUnifiedSourceTypes:
    def test_local_playbook_source(self):
        """Local playbook source works."""
        request = JobRequest(
            source={"type": "local", "target": "playbook", "path": "hello.yml"}
        )
        assert request.source.type == "local"
        assert request.source.target == "playbook"
        assert request.source.path == "hello.yml"

    def test_git_playbook_source(self):
        request = JobRequest(
            source={
                "type": "git",
                "target": "playbook",
                "repo": "https://dev.azure.com/xxxit/p/_git/r",
                "path": "deploy.yml",
            },
        )
        assert request.source is not None
        assert request.source.type == "git"
        assert request.source.target == "playbook"

    def test_git_role_source(self):
        request = JobRequest(
            source={
                "type": "git",
                "target": "role",
                "repo": "https://gitlab.company.com/team/col.git",
                "role": "nginx",
            },
        )
        assert request.source is not None
        assert request.source.type == "git"
        assert request.source.target == "role"

    def test_local_role_source(self):
        request = JobRequest(
            source={
                "type": "local",
                "target": "role",
                "collection": "mycompany.infra",
                "role": "nginx",
            },
        )
        assert request.source is not None
        assert request.source.type == "local"
        assert request.source.target == "role"

    def test_source_required(self):
        """Source is required."""
        with pytest.raises(ValidationError):
            JobRequest()


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


class TestJobRequestInventoryTypes:
    def test_string_inventory_still_works(self):
        req = JobRequest(
            source={"type": "local", "target": "playbook", "path": "hello.yml"},
            inventory="myhost,",
        )
        assert req.inventory == "myhost,"

    def test_inline_inventory(self):
        req = JobRequest(
            source={"type": "local", "target": "playbook", "path": "hello.yml"},
            inventory={
                "type": "inline",
                "data": {"webservers": {"hosts": {"10.0.1.10": None}}},
            },
        )
        assert isinstance(req.inventory, InlineInventory)
        assert req.inventory.data["webservers"]["hosts"]["10.0.1.10"] is None

    def test_git_inventory(self):
        req = JobRequest(
            source={"type": "local", "target": "playbook", "path": "hello.yml"},
            inventory={
                "type": "git",
                "repo": "https://dev.azure.com/org/project/_git/inv",
                "path": "prod/hosts.yml",
            },
        )
        assert isinstance(req.inventory, GitInventory)
        assert req.inventory.branch == "main"

    def test_invalid_inventory_type_rejected(self):
        with pytest.raises(ValidationError):
            JobRequest(
                source={"type": "local", "target": "playbook", "path": "hello.yml"},
                inventory={"type": "unknown", "data": {}},
            )

    def test_default_inventory_unchanged(self):
        req = JobRequest(source={"type": "local", "target": "playbook", "path": "hello.yml"})
        assert req.inventory == "localhost,"

    def test_options_default(self):
        req = JobRequest(source={"type": "local", "target": "playbook", "path": "hello.yml"})
        assert req.options.check is False
        assert req.options.verbosity == 0

    def test_options_provided(self):
        req = JobRequest(
            source={"type": "local", "target": "playbook", "path": "hello.yml"},
            options={"check": True, "tags": ["deploy"]},
        )
        assert req.options.check is True
        assert req.options.tags == ["deploy"]


class TestLocalPlaybookSource:
    def test_minimal_source(self):
        from ansible_runner_service.schemas import LocalPlaybookSource
        source = LocalPlaybookSource(
            type="local",
            target="playbook",
            path="hello.yml",
        )
        assert source.type == "local"
        assert source.target == "playbook"
        assert source.path == "hello.yml"

    def test_path_traversal_rejected(self):
        from ansible_runner_service.schemas import LocalPlaybookSource
        with pytest.raises(ValueError):
            LocalPlaybookSource(
                type="local",
                target="playbook",
                path="../../../etc/passwd",
            )

    def test_absolute_path_rejected(self):
        from ansible_runner_service.schemas import LocalPlaybookSource
        with pytest.raises(ValueError):
            LocalPlaybookSource(
                type="local",
                target="playbook",
                path="/etc/passwd",
            )


class TestLocalRoleSource:
    def test_minimal_source(self):
        from ansible_runner_service.schemas import LocalRoleSource
        source = LocalRoleSource(
            type="local",
            target="role",
            collection="mycompany.infra",
            role="nginx",
        )
        assert source.type == "local"
        assert source.target == "role"
        assert source.collection == "mycompany.infra"
        assert source.role == "nginx"
        assert source.role_vars == {}

    def test_with_role_vars(self):
        from ansible_runner_service.schemas import LocalRoleSource
        source = LocalRoleSource(
            type="local",
            target="role",
            collection="mycompany.infra",
            role="nginx",
            role_vars={"port": 8080},
        )
        assert source.role_vars == {"port": 8080}

    def test_empty_collection_rejected(self):
        from ansible_runner_service.schemas import LocalRoleSource
        with pytest.raises(ValidationError):
            LocalRoleSource(
                type="local",
                target="role",
                collection="",
                role="nginx",
            )

    def test_empty_role_rejected(self):
        from ansible_runner_service.schemas import LocalRoleSource
        with pytest.raises(ValidationError):
            LocalRoleSource(
                type="local",
                target="role",
                collection="mycompany.infra",
                role="",
            )


class TestUnifiedSourceDiscriminator:
    def test_local_playbook_discriminated(self):
        from ansible_runner_service.schemas import JobRequest
        req = JobRequest(
            source={"type": "local", "target": "playbook", "path": "hello.yml"},
        )
        assert req.source.type == "local"
        assert req.source.target == "playbook"

    def test_local_role_discriminated(self):
        from ansible_runner_service.schemas import JobRequest
        req = JobRequest(
            source={"type": "local", "target": "role", "collection": "mycompany.infra", "role": "nginx"},
        )
        assert req.source.type == "local"
        assert req.source.target == "role"

    def test_git_playbook_discriminated(self):
        from ansible_runner_service.schemas import JobRequest
        req = JobRequest(
            source={"type": "git", "target": "playbook", "repo": "https://dev.azure.com/org/p/_git/r", "path": "deploy.yml"},
        )
        assert req.source.type == "git"
        assert req.source.target == "playbook"

    def test_git_role_discriminated(self):
        from ansible_runner_service.schemas import JobRequest
        req = JobRequest(
            source={"type": "git", "target": "role", "repo": "https://gitlab.com/org/col.git", "role": "nginx"},
        )
        assert req.source.type == "git"
        assert req.source.target == "role"


class TestJobRequestUnifiedSource:
    def test_source_required(self):
        """Source is now required (no playbook field)."""
        with pytest.raises(ValidationError):
            JobRequest()

    def test_playbook_field_removed(self):
        """Playbook field no longer exists."""
        with pytest.raises(ValidationError):
            JobRequest(playbook="hello.yml")


class TestSourceConfigTypedDicts:
    def test_local_playbook_config(self):
        from ansible_runner_service.schemas import LocalPlaybookSourceConfig
        config: LocalPlaybookSourceConfig = {
            "type": "local",
            "target": "playbook",
            "path": "hello.yml",
        }
        assert config["type"] == "local"
        assert config["target"] == "playbook"

    def test_local_role_config(self):
        from ansible_runner_service.schemas import LocalRoleSourceConfig
        config: LocalRoleSourceConfig = {
            "type": "local",
            "target": "role",
            "collection": "mycompany.infra",
            "role": "nginx",
            "role_vars": {},
        }
        assert config["collection"] == "mycompany.infra"

    def test_git_playbook_config(self):
        from ansible_runner_service.schemas import GitPlaybookSourceConfig
        config: GitPlaybookSourceConfig = {
            "type": "git",
            "target": "playbook",
            "repo": "https://dev.azure.com/org/p/_git/r",
            "branch": "main",
            "path": "deploy.yml",
        }
        assert config["type"] == "git"
        assert config["target"] == "playbook"

    def test_git_role_config(self):
        from ansible_runner_service.schemas import GitRoleSourceConfig
        config: GitRoleSourceConfig = {
            "type": "git",
            "target": "role",
            "repo": "https://gitlab.com/org/col.git",
            "branch": "main",
            "role": "nginx",
            "role_vars": {"port": 80},
        }
        assert config["role"] == "nginx"
