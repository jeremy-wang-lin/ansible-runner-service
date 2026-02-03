# tests/test_git_service.py
import os
import subprocess
import pytest
from unittest.mock import patch, MagicMock

from ansible_runner_service.git_config import GitProvider
from ansible_runner_service.git_service import (
    _build_username_url,
    _parse_primary_collection,
    clone_repo,
    install_collection,
    resolve_fqcn,
    generate_role_wrapper_playbook,
)


class TestBuildUsernameUrl:
    def test_azure_url_has_username_no_credential(self):
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit"],
            credential_env="AZURE_PAT",
        )
        url = _build_username_url(
            "https://dev.azure.com/xxxit/project/_git/repo",
            provider,
        )
        assert url == "https://pat@dev.azure.com/xxxit/project/_git/repo"
        assert "my-pat-token" not in url

    def test_gitlab_url_has_username_no_credential(self):
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["platform-team"],
            credential_env="GITLAB_TOKEN",
        )
        url = _build_username_url(
            "https://gitlab.company.com/platform-team/repo.git",
            provider,
        )
        assert url == "https://oauth2@gitlab.company.com/platform-team/repo.git"
        assert "glpat" not in url


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

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_clone_credential_not_in_args(self, mock_run):
        """Credential must not appear in command-line arguments (visible via ps aux)."""
        mock_run.return_value = MagicMock(returncode=0)
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit"],
            credential_env="AZURE_PAT",
        )

        with patch.dict(os.environ, {"AZURE_PAT": "super-secret-pat"}):
            clone_repo(
                repo_url="https://dev.azure.com/xxxit/project/_git/repo",
                branch="main",
                target_dir="/tmp/test-dir",
                provider=provider,
            )

        args = mock_run.call_args[0][0]
        args_str = " ".join(args)
        assert "super-secret-pat" not in args_str

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_clone_passes_credential_via_env(self, mock_run):
        """Credential must be passed via GIT_ASKPASS + env var, not command line."""
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

        call_kwargs = mock_run.call_args[1]
        env = call_kwargs["env"]
        assert "GIT_ASKPASS" in env
        assert env["_GIT_CREDENTIAL"] == "my-token"
        assert env["GIT_TERMINAL_PROMPT"] == "0"

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
    def test_clone_sanitizes_credentials_in_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            128, "git", stderr="fatal: https://secret-token@dev.azure.com not found"
        )
        provider = GitProvider(
            type="azure",
            host="dev.azure.com",
            orgs=["xxxit"],
            credential_env="AZURE_PAT",
        )

        with patch.dict(os.environ, {"AZURE_PAT": "secret-token"}):
            with pytest.raises(RuntimeError) as exc_info:
                clone_repo(
                    repo_url="https://dev.azure.com/xxxit/project/_git/repo",
                    branch="main",
                    target_dir="/tmp/test-dir",
                    provider=provider,
                )
            assert "secret-token" not in str(exc_info.value)
            assert "***" in str(exc_info.value)

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_clone_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired("git", 120)
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["infra"],
            credential_env="GITLAB_TOKEN",
        )

        with patch.dict(os.environ, {"GITLAB_TOKEN": "token"}):
            with pytest.raises(RuntimeError, match="timed out"):
                clone_repo(
                    repo_url="https://gitlab.company.com/infra/repo.git",
                    branch="main",
                    target_dir="/tmp/test-dir",
                    provider=provider,
                )


class TestParsePrimaryCollection:
    def test_parses_standard_output(self):
        stdout = (
            "Starting galaxy collection install process\n"
            "Process install dependency map\n"
            "Starting collection install process\n"
            "Installing 'mycompany.infra:1.0.0' to '/tmp/collections/ansible_collections/mycompany/infra'\n"
            "mycompany.infra (1.0.0) was installed successfully\n"
        )
        assert _parse_primary_collection(stdout) == ("mycompany", "infra")

    def test_parses_first_collection_with_dependencies(self):
        stdout = (
            "Installing 'mycompany.infra:1.0.0' to '/tmp/collections/...'\n"
            "Installing 'ansible.utils:3.0.0' to '/tmp/collections/...'\n"
            "mycompany.infra (1.0.0) was installed successfully\n"
            "ansible.utils (3.0.0) was installed successfully\n"
        )
        assert _parse_primary_collection(stdout) == ("mycompany", "infra")

    def test_returns_none_for_unparseable_output(self):
        assert _parse_primary_collection("") is None
        assert _parse_primary_collection("some random output") is None

    def test_parses_wildcard_version(self):
        stdout = "Installing 'mycompany.infra:*' to '/tmp/collections/...'\n"
        assert _parse_primary_collection(stdout) == ("mycompany", "infra")


class TestInstallCollection:
    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_install_calls_ansible_galaxy(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
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
        # Should contain git+ URL with branch
        source_arg = args[3]
        assert source_arg.startswith("git+")
        assert ",v2.0.0" in source_arg
        # Credential must NOT be in command-line args
        assert "token" not in source_arg
        # -p flag for install path
        assert "-p" in args
        assert "/tmp/collections" in args

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_install_returns_parsed_collection_info(self, mock_run):
        """install_collection should return (namespace, name) from stdout."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Installing 'mycompany.infra:1.0.0' to '/tmp/collections/...'\n"
                "mycompany.infra (1.0.0) was installed successfully\n"
            ),
        )
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["platform-team"],
            credential_env="GITLAB_TOKEN",
        )

        with patch.dict(os.environ, {"GITLAB_TOKEN": "token"}):
            result = install_collection(
                repo_url="https://gitlab.company.com/platform-team/col.git",
                branch="main",
                collections_dir="/tmp/collections",
                provider=provider,
            )

        assert result == ("mycompany", "infra")

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_install_returns_none_when_stdout_unparseable(self, mock_run):
        """If ansible-galaxy output can't be parsed, return None."""
        mock_run.return_value = MagicMock(returncode=0, stdout="unexpected output\n")
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["platform-team"],
            credential_env="GITLAB_TOKEN",
        )

        with patch.dict(os.environ, {"GITLAB_TOKEN": "token"}):
            result = install_collection(
                repo_url="https://gitlab.company.com/platform-team/col.git",
                branch="main",
                collections_dir="/tmp/collections",
                provider=provider,
            )

        assert result is None

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_install_passes_credential_via_env(self, mock_run):
        """Credential must be passed via GIT_ASKPASS, not command line."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["platform-team"],
            credential_env="GITLAB_TOKEN",
        )

        with patch.dict(os.environ, {"GITLAB_TOKEN": "secret-token"}):
            install_collection(
                repo_url="https://gitlab.company.com/platform-team/col.git",
                branch="main",
                collections_dir="/tmp/collections",
                provider=provider,
            )

        call_kwargs = mock_run.call_args[1]
        env = call_kwargs["env"]
        assert "GIT_ASKPASS" in env
        assert env["_GIT_CREDENTIAL"] == "secret-token"

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

    @patch("ansible_runner_service.git_service.subprocess.run")
    def test_install_sanitizes_credentials(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "ansible-galaxy", stderr="ERROR: secret-token auth failed"
        )
        provider = GitProvider(
            type="gitlab",
            host="gitlab.company.com",
            orgs=["team"],
            credential_env="GITLAB_TOKEN",
        )

        with patch.dict(os.environ, {"GITLAB_TOKEN": "secret-token"}):
            with pytest.raises(RuntimeError) as exc_info:
                install_collection(
                    repo_url="https://gitlab.company.com/team/col.git",
                    branch="main",
                    collections_dir="/tmp/collections",
                    provider=provider,
                )
            assert "secret-token" not in str(exc_info.value)


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

    def test_single_dot_not_treated_as_fqcn(self, tmp_path):
        """Role with single dot (e.g., 'nginx.conf_setup') is NOT a FQCN."""
        col_dir = tmp_path / "ansible_collections" / "mycompany" / "infra"
        col_dir.mkdir(parents=True)
        galaxy_yml = col_dir / "galaxy.yml"
        galaxy_yml.write_text("namespace: mycompany\nname: infra\nversion: 1.0.0\n")

        result = resolve_fqcn("nginx.conf_setup", str(tmp_path))
        assert result == "mycompany.infra.nginx.conf_setup"

    def test_short_name_no_galaxy_yml_raises(self, tmp_path):
        """If no galaxy.yml found, raise error."""
        with pytest.raises(RuntimeError, match="No galaxy.yml found"):
            resolve_fqcn("nginx", str(tmp_path))

    def test_collection_info_used_when_provided(self):
        """collection_info should be used directly, skipping galaxy.yml lookup."""
        result = resolve_fqcn("nginx", "/nonexistent", collection_info=("mycompany", "infra"))
        assert result == "mycompany.infra.nginx"

    def test_collection_info_ignored_for_fqcn(self):
        """If role is already a FQCN, collection_info is irrelevant."""
        result = resolve_fqcn(
            "mycompany.infra.nginx", "/tmp", collection_info=("other", "col"),
        )
        assert result == "mycompany.infra.nginx"

    def test_multiple_collections_without_info_raises(self, tmp_path):
        """Multiple galaxy.yml without collection_info should raise."""
        # Primary collection
        col1 = tmp_path / "ansible_collections" / "mycompany" / "infra"
        col1.mkdir(parents=True)
        (col1 / "galaxy.yml").write_text("namespace: mycompany\nname: infra\n")
        # Dependency collection
        col2 = tmp_path / "ansible_collections" / "ansible" / "utils"
        col2.mkdir(parents=True)
        (col2 / "galaxy.yml").write_text("namespace: ansible\nname: utils\n")

        with pytest.raises(RuntimeError, match="Multiple collections found"):
            resolve_fqcn("nginx", str(tmp_path))

    def test_multiple_collections_with_info_resolves(self, tmp_path):
        """collection_info disambiguates when multiple collections exist."""
        # Primary collection
        col1 = tmp_path / "ansible_collections" / "mycompany" / "infra"
        col1.mkdir(parents=True)
        (col1 / "galaxy.yml").write_text("namespace: mycompany\nname: infra\n")
        # Dependency collection
        col2 = tmp_path / "ansible_collections" / "ansible" / "utils"
        col2.mkdir(parents=True)
        (col2 / "galaxy.yml").write_text("namespace: ansible\nname: utils\n")

        result = resolve_fqcn("nginx", str(tmp_path), collection_info=("mycompany", "infra"))
        assert result == "mycompany.infra.nginx"


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
