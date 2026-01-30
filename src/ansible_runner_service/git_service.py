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
