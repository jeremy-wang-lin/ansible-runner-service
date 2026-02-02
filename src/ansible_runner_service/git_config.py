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

    if parsed.scheme != "https":
        raise ValueError("Only HTTPS repository URLs are allowed")

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
