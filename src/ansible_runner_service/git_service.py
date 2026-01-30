# src/ansible_runner_service/git_service.py
import subprocess
from glob import glob
from urllib.parse import urlparse, urlunparse

import yaml

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
