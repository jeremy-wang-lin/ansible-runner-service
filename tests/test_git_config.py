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
