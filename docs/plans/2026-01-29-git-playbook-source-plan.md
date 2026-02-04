# Git Playbook Source Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow playbooks and roles to be fetched from Git repositories (Azure DevOps and GitLab) at runtime, with backward compatibility for local playbooks.

**Architecture:** API validates repo URLs against configured providers/orgs, passes source config through the queue to workers. Workers clone repos (playbook) or install collections via ansible-galaxy (role), then execute using ansible-runner. Credentials are stored server-side in environment variables.

**Tech Stack:** FastAPI, ansible-runner, ansible-galaxy, subprocess (git clone), PyYAML, Alembic (migration)

**Design docs:**
- `docs/plans/2026-01-29-git-playbook-source-design.md`
- `docs/plans/adr/2026-01-29-role-execution-strategy.md`

---

### Task 1: Git Provider Configuration

Load allowed Git providers from config and validate repo URLs against them.

**Files:**
- Create: `src/ansible_runner_service/git_config.py`
- Create: `tests/test_git_config.py`

**Step 1: Write failing tests**

```python
# tests/test_git_config.py
import os
import pytest
from unittest.mock import patch

from ansible_runner_service.git_config import (
    GitProvider,
    load_providers,
    validate_repo_url,
)


class TestGitProvider:
    def test_create_provider(self):
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit", "xxxplatform"],
            credential_env="AZURE_PAT",
        )
        assert provider.type == "azure"
        assert provider.host == "dev.azure.com"
        assert "xxxit" in provider.orgs

    def test_get_credential_from_env(self):
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit"],
            credential_env="AZURE_PAT",
        )
        with patch.dict(os.environ, {"AZURE_PAT": "my-token"}):
            assert provider.get_credential() == "my-token"

    def test_get_credential_missing_env(self):
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit"],
            credential_env="MISSING_VAR",
        )
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="not set"):
                provider.get_credential()


class TestLoadProviders:
    def test_load_from_env_json(self):
        config = '[{"type": "azure", "host": "dev.azure.com", "orgs": ["xxxit"], "credential_env": "AZURE_PAT"}]'
        with patch.dict(os.environ, {"GIT_PROVIDERS": config}):
            providers = load_providers()
            assert len(providers) == 1
            assert providers[0].host == "dev.azure.com"

    def test_load_empty_returns_empty_list(self):
        with patch.dict(os.environ, {}, clear=True):
            providers = load_providers()
            assert providers == []

    def test_load_multiple_providers(self):
        config = """[
            {"type": "azure", "host": "dev.azure.com", "orgs": ["xxxit"], "credential_env": "AZURE_PAT"},
            {"type": "gitlab", "host": "gitlab.company.com", "orgs": ["platform-team"], "credential_env": "GITLAB_TOKEN"}
        ]"""
        with patch.dict(os.environ, {"GIT_PROVIDERS": config}):
            providers = load_providers()
            assert len(providers) == 2
            assert providers[1].type == "gitlab"


class TestValidateRepoUrl:
    @pytest.fixture
    def providers(self):
        return [
            GitProvider(type="azure", host="dev.azure.com", orgs=["xxxit", "xxxplatform"], credential_env="AZURE_PAT"),
            GitProvider(type="gitlab", host="gitlab.company.com", orgs=["platform-team", "infra"], credential_env="GITLAB_TOKEN"),
        ]

    def test_valid_azure_url(self, providers):
        provider = validate_repo_url(
            "https://dev.azure.com/xxxit/project/_git/repo",
            providers,
        )
        assert provider.type == "azure"
        assert provider.host == "dev.azure.com"

    def test_valid_gitlab_url(self, providers):
        provider = validate_repo_url(
            "https://gitlab.company.com/platform-team/repo.git",
            providers,
        )
        assert provider.type == "gitlab"

    def test_reject_unknown_host(self, providers):
        with pytest.raises(ValueError, match="not configured"):
            validate_repo_url("https://github.com/org/repo.git", providers)

    def test_reject_unknown_org(self, providers):
        with pytest.raises(ValueError, match="not in allowed list"):
            validate_repo_url(
                "https://dev.azure.com/unknown-org/project/_git/repo",
                providers,
            )

    def test_extract_azure_org(self, providers):
        provider = validate_repo_url(
            "https://dev.azure.com/xxxplatform/myproject/_git/myrepo",
            providers,
        )
        assert provider.type == "azure"

    def test_extract_gitlab_org(self, providers):
        provider = validate_repo_url(
            "https://gitlab.company.com/infra/sub/repo.git",
            providers,
        )
        assert provider.type == "gitlab"

    def test_empty_providers_rejects_all(self):
        with pytest.raises(ValueError, match="not configured"):
            validate_repo_url("https://dev.azure.com/xxxit/p/_git/r", [])
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_config.py -v`
Expected: FAIL (module not found)

**Step 3: Write implementation**

```python
# src/ansible_runner_service/git_config.py
import json
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class GitProvider:
    type: str           # "azure" or "gitlab"
    host: str           # "dev.azure.com" or "gitlab.company.com"
    orgs: list[str]     # allowed organizations/groups
    credential_env: str  # env var name holding credential

    def get_credential(self) -> str:
        """Get credential from environment variable."""
        value = os.environ.get(self.credential_env)
        if not value:
            raise ValueError(
                f"Credential environment variable '{self.credential_env}' is not set"
            )
        return value


def load_providers() -> list[GitProvider]:
    """Load Git provider configuration from GIT_PROVIDERS env var (JSON)."""
    raw = os.environ.get("GIT_PROVIDERS", "")
    if not raw:
        return []
    data = json.loads(raw)
    return [GitProvider(**item) for item in data]


def _extract_org(url_path: str, provider_type: str) -> str:
    """Extract organization/group from URL path.

    Azure DevOps: /org/project/_git/repo -> org
    GitLab: /group/subgroup/repo.git -> group
    """
    parts = [p for p in url_path.strip("/").split("/") if p]
    if not parts:
        raise ValueError("Cannot extract organization from URL path")
    return parts[0]


def validate_repo_url(url: str, providers: list[GitProvider]) -> GitProvider:
    """Validate repo URL against allowed providers and orgs.

    Returns the matched GitProvider.
    Raises ValueError if not allowed.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    # Find matching provider
    provider = next((p for p in providers if p.host == host), None)
    if not provider:
        raise ValueError(f"Repository not allowed: host '{host}' is not configured")

    # Extract org from path
    org = _extract_org(parsed.path, provider.type)

    if org not in provider.orgs:
        raise ValueError(
            f"Repository not allowed: org '{org}' is not in allowed list for {host}"
        )

    return provider
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_git_config.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/git_config.py tests/test_git_config.py
git commit -m "feat: add Git provider configuration and URL validation"
```

---

### Task 2: Git Service - Clone Repos

Clone Git repos with provider-specific authentication.

**Files:**
- Create: `src/ansible_runner_service/git_service.py`
- Create: `tests/test_git_service.py`

**Step 1: Write failing tests**

```python
# tests/test_git_service.py
import os
import subprocess
import pytest
from unittest.mock import patch, MagicMock

from ansible_runner_service.git_config import GitProvider
from ansible_runner_service.git_service import build_auth_url, clone_repo


class TestBuildAuthUrl:
    def test_azure_pat_url(self):
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit"],
            credential_env="AZURE_PAT",
        )
        url = build_auth_url(
            "https://dev.azure.com/xxxit/project/_git/repo",
            provider,
            "my-pat-token",
        )
        assert url == "https://my-pat-token@dev.azure.com/xxxit/project/_git/repo"

    def test_gitlab_token_url(self):
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["platform-team"],
            credential_env="GITLAB_TOKEN",
        )
        url = build_auth_url(
            "https://gitlab.company.com/platform-team/repo.git",
            provider,
            "glpat-xxx",
        )
        assert url == "https://oauth2:glpat-xxx@gitlab.company.com/platform-team/repo.git"


class TestCloneRepo:
    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_clone_calls_git_with_correct_args(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit"],
            credential_env="AZURE_PAT",
        )

        with patch.dict(os.environ, {"AZURE_PAT": "my-token"}):
            clone_repo(
                repo_url="https://dev.azure.com/xxxit/project/_git/repo",
                branch="main",
                target_dir="/tmp/test-dir",
                provider=provider,
            )

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert args[1] == "clone"
        assert "--depth" in args
        assert "--branch" in args
        assert "main" in args
        assert "/tmp/test-dir" in args
        # Auth URL should contain token
        auth_url = [a for a in args if "dev.azure.com" in a][0]
        assert "my-token@" in auth_url

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_clone_raises_on_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            128, "git", stderr="fatal: repository not found"
        )
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit"],
            credential_env="AZURE_PAT",
        )

        with patch.dict(os.environ, {"AZURE_PAT": "my-token"}):
            with pytest.raises(RuntimeError, match="Git clone failed"):
                clone_repo(
                    repo_url="https://dev.azure.com/xxxit/project/_git/repo",
                    branch="main",
                    target_dir="/tmp/test-dir",
                    provider=provider,
                )

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_clone_default_branch_is_main(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["infra"],
            credential_env="GITLAB_TOKEN",
        )

        with patch.dict(os.environ, {"GITLAB_TOKEN": "token"}):
            clone_repo(
                repo_url="https://gitlab.company.com/infra/repo.git",
                branch="main",
                target_dir="/tmp/test-dir",
                provider=provider,
            )

        args = mock_run.call_args[0][0]
        branch_idx = args.index("--branch") + 1
        assert args[branch_idx] == "main"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_service.py -v`
Expected: FAIL (module not found)

**Step 3: Write implementation**

```python
# src/ansible_runner_service/git_service.py
import subprocess
from urllib.parse import urlparse, urlunparse

from ansible_runner_service.git_config import GitProvider


def build_auth_url(repo_url: str, provider: GitProvider, credential: str) -> str:
    """Build authenticated Git URL.

    Azure DevOps: https://{PAT}@dev.azure.com/org/project/_git/repo
    GitLab: https://oauth2:{TOKEN}@gitlab.company.com/group/repo.git
    """
    parsed = urlparse(repo_url)

    if provider.type == "azure":
        netloc = f"{credential}@{parsed.hostname}"
    elif provider.type == "gitlab":
        netloc = f"oauth2:{credential}@{parsed.hostname}"
    else:
        raise ValueError(f"Unknown provider type: {provider.type}")

    if parsed.port:
        netloc += f":{parsed.port}"

    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def clone_repo(
    repo_url: str,
    branch: str,
    target_dir: str,
    provider: GitProvider,
) -> None:
    """Clone a Git repo with provider-specific authentication.

    Uses --depth 1 --single-branch for minimal clone.
    """
    credential = provider.get_credential()
    auth_url = build_auth_url(repo_url, provider, credential)

    cmd = [
        "git", "clone",
        "--depth", "1",
        "--branch", branch,
        "--single-branch",
        auth_url,
        target_dir,
    ]

    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        # Sanitize error message to remove credentials
        safe_msg = e.stderr.replace(credential, "***") if e.stderr else "Unknown error"
        raise RuntimeError(f"Git clone failed: {safe_msg}") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Git clone timed out after 120 seconds") from None
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_git_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/git_service.py tests/test_git_service.py
git commit -m "feat: add Git clone service with provider-specific auth"
```

---

### Task 3: Role Execution - Collection Install and FQCN Resolution

Install Ansible collections from Git and resolve FQCNs.

**Files:**
- Modify: `src/ansible_runner_service/git_service.py`
- Modify: `tests/test_git_service.py`

**Step 1: Write failing tests**

Add to `tests/test_git_service.py`:

```python
from ansible_runner_service.git_service import (
    build_auth_url,
    clone_repo,
    install_collection,
    resolve_fqcn,
    generate_role_wrapper_playbook,
)


class TestInstallCollection:
    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_install_calls_ansible_galaxy(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["platform-team"],
            credential_env="GITLAB_TOKEN",
        )

        with patch.dict(os.environ, {"GITLAB_TOKEN": "token"}):
            install_collection(
                repo_url="https://gitlab.company.com/platform-team/collection.git",
                branch="v2.0.0",
                collections_dir="/tmp/collections",
                provider=provider,
            )

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "ansible-galaxy"
        assert args[1] == "collection"
        assert args[2] == "install"
        # Should contain git+ URL with auth
        source_arg = args[3]
        assert source_arg.startswith("git+")
        assert "v2.0.0" in source_arg
        # -p flag for install path
        assert "-p" in args
        assert "/tmp/collections" in args

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_install_raises_on_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "ansible-galaxy", stderr="ERROR: Failed to install"
        )
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["platform-team"],
            credential_env="GITLAB_TOKEN",
        )

        with patch.dict(os.environ, {"GITLAB_TOKEN": "token"}):
            with pytest.raises(RuntimeError, match="Collection install failed"):
                install_collection(
                    repo_url="https://gitlab.company.com/platform-team/col.git",
                    branch="main",
                    collections_dir="/tmp/collections",
                    provider=provider,
                )


class TestResolveFqcn:
    def test_fqcn_passed_through(self):
        """If role contains dots, treat as FQCN."""
        assert resolve_fqcn("mycompany.infra.nginx", "/tmp") == "mycompany.infra.nginx"

    def test_short_name_resolved_from_galaxy_yml(self, tmp_path):
        """Short name should be resolved by reading galaxy.yml."""
        # Create mock installed collection structure
        col_dir = tmp_path / "ansible_collections" / "mycompany" / "infra"
        col_dir.mkdir(parents=True)
        galaxy_yml = col_dir / "galaxy.yml"
        galaxy_yml.write_text("namespace: mycompany\nname: infra\nversion: 1.0.0\n")

        result = resolve_fqcn("nginx", str(tmp_path))
        assert result == "mycompany.infra.nginx"

    def test_short_name_no_galaxy_yml_raises(self, tmp_path):
        """If no galaxy.yml found, raise error."""
        with pytest.raises(RuntimeError, match="No galaxy.yml found"):
            resolve_fqcn("nginx", str(tmp_path))


class TestGenerateRoleWrapperPlaybook:
    def test_generate_wrapper(self):
        content = generate_role_wrapper_playbook(
            fqcn="mycompany.infra.nginx",
            role_vars={"nginx_port": 8080},
        )
        assert "mycompany.infra.nginx" in content
        assert "nginx_port" in content
        assert "8080" in content
        assert "hosts: all" in content

    def test_generate_wrapper_no_vars(self):
        content = generate_role_wrapper_playbook(
            fqcn="mycompany.infra.nginx",
            role_vars={},
        )
        assert "mycompany.infra.nginx" in content
        assert "vars:" not in content

    def test_generate_wrapper_is_valid_yaml(self):
        import yaml
        content = generate_role_wrapper_playbook(
            fqcn="mycompany.infra.nginx",
            role_vars={"port": 80, "ssl": True},
        )
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, list)
        assert parsed[0]["hosts"] == "all"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_service.py -v -k "TestInstallCollection or TestResolveFqcn or TestGenerateRoleWrapper"`
Expected: FAIL (functions not found)

**Step 3: Write implementation**

Add to `src/ansible_runner_service/git_service.py`:

```python
import subprocess
import yaml
from glob import glob
from urllib.parse import urlparse, urlunparse

from ansible_runner_service.git_config import GitProvider


# ... existing code (build_auth_url, clone_repo) ...


def install_collection(
    repo_url: str,
    branch: str,
    collections_dir: str,
    provider: GitProvider,
) -> None:
    """Install an Ansible collection from a Git repo using ansible-galaxy."""
    credential = provider.get_credential()
    auth_url = build_auth_url(repo_url, provider, credential)

    # ansible-galaxy expects: git+https://url,branch
    source = f"git+{auth_url},{branch}"

    cmd = [
        "ansible-galaxy", "collection", "install",
        source,
        "-p", collections_dir,
    ]

    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        safe_msg = e.stderr.replace(credential, "***") if e.stderr else "Unknown error"
        raise RuntimeError(f"Collection install failed: {safe_msg}") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Collection install timed out after 120 seconds") from None


def resolve_fqcn(role: str, collections_dir: str) -> str:
    """Resolve role name to fully qualified collection name.

    If role contains dots (e.g., 'mycompany.infra.nginx'), return as-is.
    Otherwise, read galaxy.yml from the installed collection to derive FQCN.
    """
    if "." in role:
        return role

    # Find galaxy.yml in installed collections
    pattern = f"{collections_dir}/ansible_collections/*/*/galaxy.yml"
    galaxy_files = glob(pattern)

    if not galaxy_files:
        raise RuntimeError(
            f"No galaxy.yml found in {collections_dir}. "
            "Ensure the repo is a valid Ansible collection."
        )

    with open(galaxy_files[0]) as f:
        galaxy = yaml.safe_load(f)

    namespace = galaxy["namespace"]
    collection = galaxy["name"]
    return f"{namespace}.{collection}.{role}"


def generate_role_wrapper_playbook(
    fqcn: str,
    role_vars: dict,
) -> str:
    """Generate a wrapper playbook that runs a role by FQCN."""
    role_entry: dict = {"role": fqcn}
    if role_vars:
        role_entry["vars"] = role_vars

    playbook = [
        {
            "name": f"Run role {fqcn}",
            "hosts": "all",
            "gather_facts": True,
            "roles": [role_entry],
        }
    ]

    return yaml.dump(playbook, default_flow_style=False)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_git_service.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/git_service.py tests/test_git_service.py
git commit -m "feat: add collection install, FQCN resolution, and wrapper playbook generation"
```

**Note:** Add `pyyaml` to dependencies in `pyproject.toml` if not already present. Check first:
```bash
grep pyyaml pyproject.toml
```
It's already a transitive dependency of ansible-runner, but add it explicitly if missing.

---

### Task 4: Request Schemas - Git Source Models

Update Pydantic schemas with backward-compatible source support.

**Files:**
- Modify: `src/ansible_runner_service/schemas.py`
- Modify: `tests/test_schemas.py`

**Step 1: Write failing tests**

Add to `tests/test_schemas.py`:

```python
from ansible_runner_service.schemas import (
    # ... existing imports ...
    GitPlaybookSource,
    GitRoleSource,
)


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
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schemas.py -v -k "GitPlaybook or GitRole or Backward"`
Expected: FAIL

**Step 3: Write implementation**

Update `src/ansible_runner_service/schemas.py`:

```python
# src/ansible_runner_service/schemas.py
from typing import Any, Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class GitPlaybookSource(BaseModel):
    type: Literal["playbook"]
    repo: str
    branch: str = "main"
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("Path traversal not allowed")
        return v


class GitRoleSource(BaseModel):
    type: Literal["role"]
    repo: str
    branch: str = "main"
    role: str
    role_vars: dict[str, Any] = Field(default_factory=dict)


GitSource = Annotated[
    Union[GitPlaybookSource, GitRoleSource],
    Field(discriminator="type"),
]


class JobRequest(BaseModel):
    playbook: str | None = Field(default=None, min_length=1)
    source: GitSource | None = None
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str = "localhost,"

    @model_validator(mode="after")
    def validate_playbook_or_source(self):
        if self.playbook and self.source:
            raise ValueError("Provide either 'playbook' or 'source', not both")
        if not self.playbook and not self.source:
            raise ValueError("Must provide either 'playbook' or 'source'")
        return self


# ... rest of existing schemas unchanged ...
class JobResponse(BaseModel):
    """Sync response - full result."""
    status: str
    rc: int
    stdout: str
    stats: dict[str, Any]


class JobSubmitResponse(BaseModel):
    """Async response - job reference."""
    job_id: str
    status: str
    created_at: str


class JobResultSchema(BaseModel):
    """Job execution result."""
    rc: int
    stdout: str
    stats: dict[str, Any]


class JobDetail(BaseModel):
    """Full job details for GET /jobs/{id}."""
    job_id: str
    status: str
    playbook: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: JobResultSchema | None = None
    error: str | None = None


class JobSummary(BaseModel):
    """Job summary for list endpoint."""
    job_id: str
    status: str
    playbook: str
    created_at: str
    finished_at: str | None = None


class JobListResponse(BaseModel):
    """Response for GET /jobs list endpoint."""
    jobs: list[JobSummary]
    total: int
    limit: int
    offset: int
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS (all tests including existing ones)

Note: Some existing tests may need updates since `playbook` is now optional. Fix `test_minimal_request` to provide `playbook="hello.yml"` explicitly if needed (it already does via `Field(...)`). The key change is `playbook` going from required to optional.

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "feat: add Git source schemas with backward compatibility"
```

---

### Task 5: Database Migration - Add Source Columns

Add source_type, source_repo, source_branch columns to jobs table.

**Files:**
- Modify: `src/ansible_runner_service/models.py`
- Create: Alembic migration
- Modify: `src/ansible_runner_service/repository.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_repository.py`

**Step 1: Write failing tests**

Add to `tests/test_models.py`:

```python
class TestJobModelSourceFields:
    def test_source_fields_exist(self):
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
```

Add to `tests/test_repository.py`:

```python
def test_create_job_with_source_fields(self):
    job = self.repo.create(
        job_id="test-source-123",
        playbook="deploy/app.yml",
        extra_vars={},
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
        source_type="playbook",
        source_repo="https://dev.azure.com/xxxit/p/_git/r",
        source_branch="v2.0.0",
    )
    assert job.source_type == "playbook"
    assert job.source_repo == "https://dev.azure.com/xxxit/p/_git/r"
    assert job.source_branch == "v2.0.0"

def test_create_job_default_source_type(self):
    job = self.repo.create(
        job_id="test-local-123",
        playbook="hello.yml",
        extra_vars={},
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
    )
    assert job.source_type == "local"
    assert job.source_repo is None
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models.py tests/test_repository.py -v -k "source"`
Expected: FAIL

**Step 3: Write implementation**

Update `src/ansible_runner_service/models.py`:

```python
# Add to JobModel class:
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    source_repo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Update `src/ansible_runner_service/repository.py` - modify `create` method signature:

```python
def create(
    self,
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str,
    created_at: datetime,
    source_type: str = "local",
    source_repo: str | None = None,
    source_branch: str | None = None,
) -> JobModel:
    """Create a new job record."""
    job = JobModel(
        id=job_id,
        status="pending",
        playbook=playbook,
        extra_vars=extra_vars,
        inventory=inventory,
        created_at=created_at,
        source_type=source_type,
        source_repo=source_repo,
        source_branch=source_branch,
    )
    self.session.add(job)
    self.session.commit()
    return job
```

Create Alembic migration:

```bash
cd /Users/jeremy.lin/work/claude_code/ansible-runner-service/.worktrees/git-playbook-source
source .venv/bin/activate
alembic revision --autogenerate -m "add source columns to jobs table"
```

If autogenerate doesn't work, create manually:

```python
# alembic/versions/xxxx_add_source_columns.py
"""add source columns to jobs table"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('jobs', sa.Column('source_type', sa.String(20), nullable=False, server_default='local'))
    op.add_column('jobs', sa.Column('source_repo', sa.String(512), nullable=True))
    op.add_column('jobs', sa.Column('source_branch', sa.String(255), nullable=True))

def downgrade():
    op.drop_column('jobs', 'source_branch')
    op.drop_column('jobs', 'source_repo')
    op.drop_column('jobs', 'source_type')
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py tests/test_repository.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/models.py src/ansible_runner_service/repository.py tests/test_models.py tests/test_repository.py alembic/
git commit -m "feat: add source_type, source_repo, source_branch columns to jobs"
```

---

### Task 6: Job Store Updates - Source Metadata

Pass source metadata through JobStore and Redis.

**Files:**
- Modify: `src/ansible_runner_service/job_store.py`
- Modify: `tests/test_job_store.py`

**Step 1: Write failing tests**

Add to `tests/test_job_store.py`:

```python
class TestJobStoreSourceFields:
    def test_create_job_with_source(self, mock_redis):
        store = JobStore(mock_redis)
        job = store.create_job(
            playbook="deploy/app.yml",
            extra_vars={},
            inventory="localhost,",
            source_type="playbook",
            source_repo="https://dev.azure.com/xxxit/p/_git/r",
            source_branch="main",
        )
        assert job.source_type == "playbook"
        assert job.source_repo == "https://dev.azure.com/xxxit/p/_git/r"
        assert job.source_branch == "main"

    def test_create_job_default_local(self, mock_redis):
        store = JobStore(mock_redis)
        job = store.create_job(
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )
        assert job.source_type == "local"
        assert job.source_repo is None
        assert job.source_branch is None

    def test_create_job_with_source_writes_to_db(self, mock_redis, mock_repo):
        store = JobStore(mock_redis, repository=mock_repo)
        store.create_job(
            playbook="deploy/app.yml",
            extra_vars={},
            inventory="localhost,",
            source_type="playbook",
            source_repo="https://dev.azure.com/xxxit/p/_git/r",
            source_branch="main",
        )
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        assert call_kwargs["source_type"] == "playbook"
        assert call_kwargs["source_repo"] == "https://dev.azure.com/xxxit/p/_git/r"
        assert call_kwargs["source_branch"] == "main"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_job_store.py -v -k "SourceFields"`
Expected: FAIL

**Step 3: Write implementation**

Update `src/ansible_runner_service/job_store.py`:

Add `source_type`, `source_repo`, `source_branch` fields to the `Job` dataclass:

```python
@dataclass
class Job:
    job_id: str
    status: JobStatus
    playbook: str
    extra_vars: dict[str, Any]
    inventory: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: JobResult | None = None
    error: str | None = None
    source_type: str = "local"
    source_repo: str | None = None
    source_branch: str | None = None
```

Update `create_job` to accept and pass source fields:

```python
def create_job(
    self,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str,
    source_type: str = "local",
    source_repo: str | None = None,
    source_branch: str | None = None,
) -> Job:
    job = Job(
        job_id=str(uuid.uuid4()),
        status=JobStatus.PENDING,
        playbook=playbook,
        extra_vars=extra_vars,
        inventory=inventory,
        created_at=datetime.now(timezone.utc),
        source_type=source_type,
        source_repo=source_repo,
        source_branch=source_branch,
    )
    self._save_job(job)

    if self.repository:
        try:
            self.repository.create(
                job_id=job.job_id,
                playbook=playbook,
                extra_vars=extra_vars,
                inventory=inventory,
                created_at=job.created_at,
                source_type=source_type,
                source_repo=source_repo,
                source_branch=source_branch,
            )
        except Exception:
            self.redis.delete(self._job_key(job.job_id))
            raise

    return job
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_job_store.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/job_store.py tests/test_job_store.py
git commit -m "feat: pass source metadata through JobStore to Redis and DB"
```

---

### Task 7: Queue Updates - Pass Source Config

Pass source configuration through the rq queue to workers.

**Files:**
- Modify: `src/ansible_runner_service/queue.py`
- Modify: `tests/test_queue.py`

**Step 1: Write failing tests**

Add to `tests/test_queue.py`:

```python
class TestEnqueueJobWithSource:
    def test_enqueue_with_source_config(self):
        mock_queue = MagicMock()

        with patch("ansible_runner_service.queue.Queue", return_value=mock_queue):
            enqueue_job(
                job_id="test-git-123",
                playbook="deploy/app.yml",
                extra_vars={},
                inventory="localhost,",
                source_config={
                    "type": "playbook",
                    "repo": "https://dev.azure.com/xxxit/p/_git/r",
                    "branch": "main",
                    "path": "deploy/app.yml",
                },
            )

        call_args = mock_queue.enqueue.call_args
        job_kwargs = call_args.kwargs["kwargs"]
        assert job_kwargs["source_config"]["type"] == "playbook"
        assert job_kwargs["source_config"]["repo"] == "https://dev.azure.com/xxxit/p/_git/r"

    def test_enqueue_without_source_config(self):
        mock_queue = MagicMock()

        with patch("ansible_runner_service.queue.Queue", return_value=mock_queue):
            enqueue_job(
                job_id="test-local-123",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
            )

        call_args = mock_queue.enqueue.call_args
        job_kwargs = call_args.kwargs["kwargs"]
        assert job_kwargs["source_config"] is None
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queue.py -v`
Expected: FAIL

**Step 3: Write implementation**

Update `src/ansible_runner_service/queue.py`:

```python
def enqueue_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str,
    source_config: dict[str, Any] | None = None,
    redis: Redis | None = None,
) -> None:
    """Enqueue a job for async execution."""
    if redis is None:
        redis = Redis()
    queue = Queue(connection=redis)
    queue.enqueue(
        "ansible_runner_service.worker.execute_job",
        kwargs={
            "job_id": job_id,
            "playbook": playbook,
            "extra_vars": extra_vars,
            "inventory": inventory,
            "source_config": source_config,
        },
    )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queue.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/queue.py tests/test_queue.py
git commit -m "feat: pass source_config through job queue to worker"
```

---

### Task 8: Worker Updates - Handle Git Sources

Update worker to clone repos, install collections, and execute from temp directories.

**Files:**
- Modify: `src/ansible_runner_service/worker.py`
- Modify: `src/ansible_runner_service/runner.py`
- Modify: `tests/test_worker.py`
- Modify: `tests/test_runner.py`

**Step 1: Update runner.py to support absolute playbook paths and envvars**

Add to `tests/test_runner.py`:

```python
class TestRunPlaybookAbsolutePath:
    @patch("ansible_runner_service.runner.ansible_runner.run")
    def test_run_with_absolute_playbook_path(self, mock_run):
        """When playbook is absolute path, use it directly without playbooks_dir."""
        mock_runner = MagicMock()
        mock_runner.status = "successful"
        mock_runner.rc = 0
        mock_runner.stdout = MagicMock()
        mock_runner.stdout.read.return_value = "ok"
        mock_runner.stats = {}
        mock_run.return_value = mock_runner

        result = run_playbook(
            playbook="/tmp/job-xxx/repo/deploy.yml",
            extra_vars={},
            inventory="localhost,",
        )

        assert result.status == "successful"
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["playbook"] == "/tmp/job-xxx/repo/deploy.yml"

    @patch("ansible_runner_service.runner.ansible_runner.run")
    def test_run_with_envvars(self, mock_run):
        """Support passing environment variables to ansible-runner."""
        mock_runner = MagicMock()
        mock_runner.status = "successful"
        mock_runner.rc = 0
        mock_runner.stdout = MagicMock()
        mock_runner.stdout.read.return_value = "ok"
        mock_runner.stats = {}
        mock_run.return_value = mock_runner

        run_playbook(
            playbook="/tmp/playbook.yml",
            extra_vars={},
            inventory="localhost,",
            envvars={"ANSIBLE_COLLECTIONS_PATH": "/tmp/collections"},
        )

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["envvars"]["ANSIBLE_COLLECTIONS_PATH"] == "/tmp/collections"
```

Update `src/ansible_runner_service/runner.py`:

```python
def run_playbook(
    playbook: str,
    extra_vars: dict,
    inventory: str,
    playbooks_dir: Path | None = None,
    envvars: dict | None = None,
) -> RunResult:
    """Run an Ansible playbook synchronously and return results."""
    if playbooks_dir:
        playbook_path = str(playbooks_dir / playbook)
    else:
        playbook_path = playbook

    with tempfile.TemporaryDirectory() as tmpdir:
        run_kwargs = dict(
            private_data_dir=tmpdir,
            playbook=playbook_path,
            inventory=inventory,
            extravars=extra_vars,
            quiet=False,
        )
        if envvars:
            run_kwargs["envvars"] = envvars

        runner = ansible_runner.run(**run_kwargs)

        stdout = runner.stdout.read() if runner.stdout else ""

        return RunResult(
            status=runner.status,
            rc=runner.rc,
            stdout=stdout,
            stats=runner.stats or {},
        )
```

**Step 2: Write failing worker tests**

Add to `tests/test_worker.py`:

```python
class TestExecuteJobWithGitSource:
    @patch("ansible_runner_service.worker.clone_repo")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker.load_providers")
    @patch("ansible_runner_service.worker.validate_repo_url")
    def test_git_playbook_source(
        self, mock_validate, mock_load_providers,
        mock_redis, mock_session, mock_engine, mock_run, mock_clone,
    ):
        from ansible_runner_service.worker import execute_job
        from ansible_runner_service.git_config import GitProvider

        mock_redis.return_value = MagicMock()
        mock_session_inst = MagicMock()
        mock_session.return_value = MagicMock(return_value=mock_session_inst)
        mock_engine.return_value = MagicMock()

        mock_provider = GitProvider(
            type="azure", host="dev.azure.com",
            orgs=["xxxit"], credential_env="AZURE_PAT",
        )
        mock_validate.return_value = mock_provider
        mock_load_providers.return_value = [mock_provider]

        mock_run.return_value = MagicMock(
            status="successful", rc=0, stdout="ok", stats={},
        )

        execute_job(
            job_id="test-123",
            playbook="deploy/app.yml",
            extra_vars={},
            inventory="localhost,",
            source_config={
                "type": "playbook",
                "repo": "https://dev.azure.com/xxxit/p/_git/r",
                "branch": "main",
                "path": "deploy/app.yml",
            },
        )

        # Verify clone was called
        mock_clone.assert_called_once()
        clone_kwargs = mock_clone.call_args[1]
        assert clone_kwargs["repo_url"] == "https://dev.azure.com/xxxit/p/_git/r"
        assert clone_kwargs["branch"] == "main"

        # Verify run_playbook was called with path in cloned dir
        mock_run.assert_called_once()

    @patch("ansible_runner_service.worker.install_collection")
    @patch("ansible_runner_service.worker.resolve_fqcn")
    @patch("ansible_runner_service.worker.generate_role_wrapper_playbook")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker.load_providers")
    @patch("ansible_runner_service.worker.validate_repo_url")
    def test_git_role_source(
        self, mock_validate, mock_load_providers,
        mock_redis, mock_session, mock_engine,
        mock_run, mock_gen_wrapper, mock_resolve, mock_install,
    ):
        from ansible_runner_service.worker import execute_job
        from ansible_runner_service.git_config import GitProvider

        mock_redis.return_value = MagicMock()
        mock_session_inst = MagicMock()
        mock_session.return_value = MagicMock(return_value=mock_session_inst)
        mock_engine.return_value = MagicMock()

        mock_provider = GitProvider(
            type="gitlab", host="gitlab.company.com",
            orgs=["platform-team"], credential_env="GITLAB_TOKEN",
        )
        mock_validate.return_value = mock_provider
        mock_load_providers.return_value = [mock_provider]
        mock_resolve.return_value = "mycompany.infra.nginx"
        mock_gen_wrapper.return_value = "---\n- hosts: all\n  roles:\n    - mycompany.infra.nginx\n"

        mock_run.return_value = MagicMock(
            status="successful", rc=0, stdout="ok", stats={},
        )

        execute_job(
            job_id="test-role-123",
            playbook="mycompany.infra.nginx",
            extra_vars={},
            inventory="localhost,",
            source_config={
                "type": "role",
                "repo": "https://gitlab.company.com/platform-team/col.git",
                "branch": "v2.0.0",
                "role": "nginx",
                "role_vars": {"nginx_port": 8080},
            },
        )

        # Verify collection was installed
        mock_install.assert_called_once()
        # Verify FQCN resolved
        mock_resolve.assert_called_once()
        # Verify wrapper generated
        mock_gen_wrapper.assert_called_once_with(
            fqcn="mycompany.infra.nginx",
            role_vars={"nginx_port": 8080},
        )
        # Verify playbook was run
        mock_run.assert_called_once()

    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_redis")
    def test_local_source_unchanged(
        self, mock_redis, mock_session, mock_engine, mock_run,
    ):
        """Legacy local source still works when source_config is None."""
        from ansible_runner_service.worker import execute_job

        mock_redis.return_value = MagicMock()
        mock_session_inst = MagicMock()
        mock_session.return_value = MagicMock(return_value=mock_session_inst)
        mock_engine.return_value = MagicMock()

        mock_run.return_value = MagicMock(
            status="successful", rc=0, stdout="ok", stats={},
        )

        execute_job(
            job_id="test-local-123",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )

        # No git operations, just regular run
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert "playbooks_dir" in call_kwargs
```

**Step 3: Write implementation**

Update `src/ansible_runner_service/worker.py`:

```python
# src/ansible_runner_service/worker.py
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from redis import Redis

from ansible_runner_service.job_store import JobStore, JobStatus, JobResult
from ansible_runner_service.runner import run_playbook
from ansible_runner_service.repository import JobRepository
from ansible_runner_service.database import get_engine, get_session
from ansible_runner_service.git_config import load_providers, validate_repo_url
from ansible_runner_service.git_service import (
    clone_repo,
    install_collection,
    resolve_fqcn,
    generate_role_wrapper_playbook,
)


_engine = None


def get_engine_singleton():
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def get_redis() -> Redis:
    return Redis()


def get_playbooks_dir() -> Path:
    return Path(__file__).parent.parent.parent / "playbooks"


def _execute_local(playbook, extra_vars, inventory):
    """Execute a local playbook."""
    return run_playbook(
        playbook=playbook,
        extra_vars=extra_vars,
        inventory=inventory,
        playbooks_dir=get_playbooks_dir(),
    )


def _execute_git_playbook(source_config, extra_vars, inventory):
    """Clone repo and execute playbook from it."""
    providers = load_providers()
    provider = validate_repo_url(source_config["repo"], providers)

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "repo")
        clone_repo(
            repo_url=source_config["repo"],
            branch=source_config.get("branch", "main"),
            target_dir=repo_dir,
            provider=provider,
        )

        playbook_path = os.path.join(repo_dir, source_config["path"])

        return run_playbook(
            playbook=playbook_path,
            extra_vars=extra_vars,
            inventory=inventory,
        )


def _execute_git_role(source_config, extra_vars, inventory):
    """Install collection and execute role."""
    providers = load_providers()
    provider = validate_repo_url(source_config["repo"], providers)

    with tempfile.TemporaryDirectory() as tmpdir:
        collections_dir = os.path.join(tmpdir, "collections")
        os.makedirs(collections_dir)

        install_collection(
            repo_url=source_config["repo"],
            branch=source_config.get("branch", "main"),
            collections_dir=collections_dir,
            provider=provider,
        )

        fqcn = resolve_fqcn(source_config["role"], collections_dir)
        role_vars = source_config.get("role_vars", {})

        wrapper_content = generate_role_wrapper_playbook(fqcn=fqcn, role_vars=role_vars)
        wrapper_path = os.path.join(tmpdir, "wrapper_playbook.yml")
        with open(wrapper_path, "w") as f:
            f.write(wrapper_content)

        return run_playbook(
            playbook=wrapper_path,
            extra_vars=extra_vars,
            inventory=inventory,
            envvars={"ANSIBLE_COLLECTIONS_PATH": collections_dir},
        )


def execute_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str,
    source_config: dict[str, Any] | None = None,
) -> None:
    """Execute a job - called by rq worker."""
    engine = get_engine_singleton()
    Session = get_session(engine)
    session = Session()

    try:
        repository = JobRepository(session)
        store = JobStore(get_redis(), repository=repository)

        # Mark as running
        store.update_status(
            job_id,
            JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        try:
            if source_config is None:
                result = _execute_local(playbook, extra_vars, inventory)
            elif source_config["type"] == "playbook":
                result = _execute_git_playbook(source_config, extra_vars, inventory)
            elif source_config["type"] == "role":
                result = _execute_git_role(source_config, extra_vars, inventory)
            else:
                raise ValueError(f"Unknown source type: {source_config['type']}")

            job_result = JobResult(
                rc=result.rc,
                stdout=result.stdout,
                stats=result.stats,
            )

            status = JobStatus.SUCCESSFUL if result.rc == 0 else JobStatus.FAILED
            store.update_status(
                job_id,
                status,
                finished_at=datetime.now(timezone.utc),
                result=job_result,
            )

        except Exception as e:
            store.update_status(
                job_id,
                JobStatus.FAILED,
                finished_at=datetime.now(timezone.utc),
                error=str(e),
            )
    finally:
        session.close()
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker.py tests/test_runner.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/worker.py src/ansible_runner_service/runner.py tests/test_worker.py tests/test_runner.py
git commit -m "feat: worker handles Git playbook and role sources"
```

---

### Task 9: API Endpoint Updates - Handle New Request Format

Update the API to validate and route Git source requests.

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Modify: `tests/test_api.py`

**Step 1: Write failing tests**

Add to `tests/test_api.py`:

```python
class TestSubmitGitPlaybookSource:
    @pytest.fixture
    def client(self, ...):
        # Same fixture pattern as existing tests, with git providers configured
        ...

    async def test_submit_git_playbook(self, client):
        """Submit job with Git playbook source."""
        with patch("ansible_runner_service.main.load_providers") as mock_providers, \
             patch("ansible_runner_service.main.validate_repo_url") as mock_validate:
            mock_providers.return_value = [
                GitProvider(type="azure", host="dev.azure.com", orgs=["xxxit"], credential_env="AZURE_PAT"),
            ]
            mock_validate.return_value = mock_providers.return_value[0]

            response = await client.post(
                "/api/v1/jobs",
                json={
                    "source": {
                        "type": "playbook",
                        "repo": "https://dev.azure.com/xxxit/p/_git/r",
                        "path": "deploy/app.yml",
                    },
                    "inventory": "localhost,",
                },
            )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data

    async def test_submit_git_playbook_rejected_org(self, client):
        """Reject repo from disallowed organization."""
        with patch("ansible_runner_service.main.load_providers") as mock_providers, \
             patch("ansible_runner_service.main.validate_repo_url") as mock_validate:
            mock_providers.return_value = []
            mock_validate.side_effect = ValueError("host 'github.com' is not configured")

            response = await client.post(
                "/api/v1/jobs",
                json={
                    "source": {
                        "type": "playbook",
                        "repo": "https://github.com/evil/repo.git",
                        "path": "deploy.yml",
                    },
                },
            )
        assert response.status_code == 400

    async def test_submit_git_role(self, client):
        """Submit job with Git role source."""
        with patch("ansible_runner_service.main.load_providers") as mock_providers, \
             patch("ansible_runner_service.main.validate_repo_url") as mock_validate:
            mock_providers.return_value = [
                GitProvider(type="gitlab", host="gitlab.company.com", orgs=["team"], credential_env="GL_TOKEN"),
            ]
            mock_validate.return_value = mock_providers.return_value[0]

            response = await client.post(
                "/api/v1/jobs",
                json={
                    "source": {
                        "type": "role",
                        "repo": "https://gitlab.company.com/team/col.git",
                        "role": "nginx",
                        "role_vars": {"port": 80},
                    },
                },
            )
        assert response.status_code == 202

    async def test_legacy_local_playbook_still_works(self, client):
        """Existing format still accepted."""
        response = await client.post(
            "/api/v1/jobs?sync=true",
            json={"playbook": "hello.yml"},
        )
        assert response.status_code == 200
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v -k "GitPlaybook or GitRole or legacy"`
Expected: FAIL

**Step 3: Write implementation**

Update `src/ansible_runner_service/main.py` - modify `submit_job`:

```python
from ansible_runner_service.git_config import load_providers, validate_repo_url
from ansible_runner_service.schemas import GitPlaybookSource, GitRoleSource

@app.post(
    "/api/v1/jobs",
    response_model=Union[JobSubmitResponse, JobResponse],
    status_code=202,
)
def submit_job(
    request: JobRequest,
    sync: bool = Query(default=False, description="Run synchronously"),
    playbooks_dir: Path = Depends(get_playbooks_dir),
    job_store: JobStore = Depends(get_job_store),
    redis: Redis = Depends(get_redis),
) -> Union[JobSubmitResponse, JobResponse]:
    """Submit a playbook job for execution."""

    # Route based on source type
    if request.source:
        return _handle_git_source(request, sync, job_store, redis)
    else:
        return _handle_local_source(request, sync, playbooks_dir, job_store, redis)


def _handle_local_source(request, sync, playbooks_dir, job_store, redis):
    """Handle legacy local playbook source."""
    # Block path traversal attempts
    if ".." in request.playbook or request.playbook.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid playbook name")

    playbook_path = playbooks_dir / request.playbook

    if not playbook_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Playbook not found: {request.playbook}"
        )

    if sync:
        result = run_playbook(
            playbook=request.playbook,
            extra_vars=request.extra_vars,
            inventory=request.inventory,
            playbooks_dir=playbooks_dir,
        )
        return JSONResponse(
            status_code=200,
            content=JobResponse(
                status=result.status,
                rc=result.rc,
                stdout=result.stdout,
                stats=result.stats,
            ).model_dump(),
        )

    job = job_store.create_job(
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
    )

    enqueue_job(
        job_id=job.job_id,
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
        redis=redis,
    )

    return JSONResponse(
        status_code=202,
        content=JobSubmitResponse(
            job_id=job.job_id,
            status=job.status.value,
            created_at=job.created_at.isoformat(),
        ).model_dump(),
    )


def _handle_git_source(request, sync, job_store, redis):
    """Handle Git playbook/role source."""
    source = request.source

    # Validate repo URL against allowed providers
    providers = load_providers()
    try:
        validate_repo_url(source.repo, providers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Determine playbook name for job record
    if isinstance(source, GitPlaybookSource):
        playbook = source.path
        source_config = {
            "type": "playbook",
            "repo": source.repo,
            "branch": source.branch,
            "path": source.path,
        }
    elif isinstance(source, GitRoleSource):
        playbook = source.role
        source_config = {
            "type": "role",
            "repo": source.repo,
            "branch": source.branch,
            "role": source.role,
            "role_vars": source.role_vars,
        }
    else:
        raise HTTPException(status_code=400, detail="Unknown source type")

    if sync:
        raise HTTPException(
            status_code=400,
            detail="Sync mode not supported for Git sources. Use async mode.",
        )

    job = job_store.create_job(
        playbook=playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
        source_type=source.type,
        source_repo=source.repo,
        source_branch=source.branch,
    )

    enqueue_job(
        job_id=job.job_id,
        playbook=playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
        source_config=source_config,
        redis=redis,
    )

    return JSONResponse(
        status_code=202,
        content=JobSubmitResponse(
            job_id=job.job_id,
            status=job.status.value,
            created_at=job.created_at.isoformat(),
        ).model_dump(),
    )
```

**Step 4: Run ALL tests to verify nothing is broken**

Run: `pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_db_integration.py --ignore=tests/test_queue_integration.py`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "feat: API accepts Git playbook and role sources with backward compatibility"
```

---

### Task 10: Add PyYAML Dependency and Example Config

Ensure PyYAML is in dependencies and add example config file.

**Files:**
- Modify: `pyproject.toml`
- Create: `config/git_providers.example.yaml`

**Step 1: Check if pyyaml is already a dependency**

```bash
grep -i pyyaml pyproject.toml
```

If not present, add it:

```toml
dependencies = [
    # ... existing ...
    "pyyaml>=6.0.0",
]
```

**Step 2: Create example config**

```yaml
# config/git_providers.example.yaml
# Example Git provider configuration.
#
# To use, set the GIT_PROVIDERS environment variable to a JSON string:
#
#   export GIT_PROVIDERS='[
#     {"type": "azure", "host": "dev.azure.com", "orgs": ["xxxit", "xxxplatform"], "credential_env": "AZURE_PAT"},
#     {"type": "gitlab", "host": "gitlab.company.com", "orgs": ["platform-team", "infra"], "credential_env": "GITLAB_TOKEN"}
#   ]'
#
# Then set the credential environment variables:
#   export AZURE_PAT="your-azure-pat-token"
#   export GITLAB_TOKEN="your-gitlab-access-token"
#
providers:
  - type: azure
    host: dev.azure.com
    orgs:
      - xxxit
      - xxxplatform
    credential_env: AZURE_PAT

  - type: gitlab
    host: gitlab.company.com
    orgs:
      - platform-team
      - infra
    credential_env: GITLAB_TOKEN
```

**Step 3: Run all tests to verify nothing is broken**

Run: `pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_db_integration.py --ignore=tests/test_queue_integration.py`
Expected: PASS

**Step 4: Commit**

```bash
git add pyproject.toml config/
git commit -m "chore: add pyyaml dependency and example Git providers config"
```

---

### Task 11: Update Usage Guide

Document the new Git source feature.

**Files:**
- Modify: `docs/usage-guide.md`

Add a new section "Git Playbook Sources" after "With Extra Variables":

```markdown
### Git Playbook Source

Execute a playbook from a Git repository:

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "playbook",
      "repo": "https://dev.azure.com/xxxit/project/_git/ansible-playbooks",
      "branch": "main",
      "path": "deploy/app.yml"
    },
    "extra_vars": {"env": "prod"},
    "inventory": "localhost,"
  }'
```

### Git Role Source

Execute an Ansible role from a collection in a Git repository:

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "role",
      "repo": "https://gitlab.company.com/platform-team/ansible-collection.git",
      "branch": "v2.0.0",
      "role": "nginx",
      "role_vars": {"nginx_port": 8080}
    },
    "inventory": "webservers,"
  }'
```

### Configuring Git Providers

Git sources require provider configuration. Set the `GIT_PROVIDERS` environment variable:

```bash
export GIT_PROVIDERS='[
  {"type": "azure", "host": "dev.azure.com", "orgs": ["xxxit"], "credential_env": "AZURE_PAT"},
  {"type": "gitlab", "host": "gitlab.company.com", "orgs": ["platform-team"], "credential_env": "GITLAB_TOKEN"}
]'
export AZURE_PAT="your-azure-pat-token"
export GITLAB_TOKEN="your-gitlab-access-token"
```

See `config/git_providers.example.yaml` for a full example.
```

**Step 1: Commit**

```bash
git add docs/usage-guide.md
git commit -m "docs: add Git playbook source usage guide"
```

---

### Task 12: Final Verification

Run all tests and verify everything works together.

**Step 1: Run all unit tests**

```bash
pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_db_integration.py --ignore=tests/test_queue_integration.py
```

Expected: ALL PASS

**Step 2: Run integration tests (if Redis + MariaDB available)**

```bash
pytest tests/test_integration.py tests/test_db_integration.py tests/test_queue_integration.py -v -m integration
```

**Step 3: Final commit if any fixes needed**

```bash
git log --oneline -10
```

Review all commits for the feature.
