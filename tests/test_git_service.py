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
