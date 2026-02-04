# src/ansible_runner_service/git_service.py
import os
import re
import stat
import subprocess
import tempfile
from glob import glob
from urllib.parse import urlparse, urlunparse

import yaml

from ansible_runner_service.git_config import GitProvider


def _build_username_url(repo_url: str, provider: GitProvider) -> str:
    """Build Git URL with username only (no credential).

    The credential is passed separately via GIT_ASKPASS to avoid
    exposing it in command-line arguments (visible via ps aux).

    Azure DevOps: https://pat@dev.azure.com/org/project/_git/repo
    GitLab: https://oauth2@gitlab.company.com/group/repo.git
    """
    parsed = urlparse(repo_url)

    if provider.type == "azure":
        username = "pat"
    elif provider.type == "gitlab":
        username = "oauth2"
    else:
        raise ValueError(f"Unknown provider type: {provider.type}")

    netloc = f"{username}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"

    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def _create_askpass_script(tmpdir: str) -> str:
    """Create a GIT_ASKPASS script that reads credential from env.

    The script itself contains no secrets — it outputs the value of
    the _GIT_CREDENTIAL environment variable when git prompts for a password.
    """
    script_path = os.path.join(tmpdir, "askpass.sh")
    with open(script_path, "w") as f:
        f.write('#!/bin/sh\nprintf \'%s\\n\' "$_GIT_CREDENTIAL"\n')
    os.chmod(script_path, stat.S_IRWXU)
    return script_path


def _subprocess_env(askpass_path: str, credential: str) -> dict:
    """Build subprocess environment with GIT_ASKPASS credential passing."""
    return {
        **os.environ,
        "GIT_ASKPASS": askpass_path,
        "GIT_TERMINAL_PROMPT": "0",
        "_GIT_CREDENTIAL": credential,
    }


def clone_repo(
    repo_url: str,
    branch: str,
    target_dir: str,
    provider: GitProvider,
) -> None:
    """Clone a Git repo with provider-specific authentication.

    Uses --depth 1 --single-branch for minimal clone.
    Credential is passed via GIT_ASKPASS, never in command-line arguments.
    """
    credential = provider.get_credential()
    clone_url = _build_username_url(repo_url, provider)

    cmd = [
        "git", "clone",
        "--depth", "1",
        "--branch", branch,
        "--single-branch",
        clone_url,
        target_dir,
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        askpass_path = _create_askpass_script(tmpdir)
        env = _subprocess_env(askpass_path, credential)

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
        except subprocess.CalledProcessError as e:
            safe_msg = e.stderr.replace(credential, "***") if e.stderr else "Unknown error"
            raise RuntimeError(f"Git clone failed: {safe_msg}") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("Git clone timed out after 120 seconds") from None


def _parse_primary_collection(stdout: str) -> tuple[str, str] | None:
    """Extract the primary (first installed) collection from ansible-galaxy output.

    ansible-galaxy prints lines like:
        Installing 'mycompany.infra:1.0.0' to '/path/to/...'
    The first such line is the primary collection; subsequent ones are dependencies.

    Returns (namespace, name) or None if the output couldn't be parsed.
    """
    match = re.search(r"Installing '(\w+)\.(\w+):", stdout)
    if match:
        return match.group(1), match.group(2)
    return None


def install_collection(
    repo_url: str,
    branch: str,
    collections_dir: str,
    provider: GitProvider,
) -> tuple[str, str] | None:
    """Install an Ansible collection from a Git repo using ansible-galaxy.

    Credential is passed via GIT_ASKPASS, never in command-line arguments.

    Returns (namespace, name) of the primary installed collection parsed from
    ansible-galaxy output, or None if the output couldn't be parsed.
    """
    credential = provider.get_credential()
    clone_url = _build_username_url(repo_url, provider)

    # ansible-galaxy expects: git+https://url,branch
    source = f"git+{clone_url},{branch}"

    cmd = [
        "ansible-galaxy", "collection", "install",
        source,
        "-p", collections_dir,
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        askpass_path = _create_askpass_script(tmpdir)
        env = _subprocess_env(askpass_path, credential)

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
        except subprocess.CalledProcessError as e:
            safe_msg = e.stderr.replace(credential, "***") if e.stderr else "Unknown error"
            raise RuntimeError(f"Collection install failed: {safe_msg}") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("Collection install timed out after 120 seconds") from None

    return _parse_primary_collection(result.stdout)


def resolve_fqcn(
    role: str,
    collections_dir: str,
    collection_info: tuple[str, str] | None = None,
) -> str:
    """Resolve role name to fully qualified collection name.

    If role contains dots (e.g., 'mycompany.infra.nginx'), return as-is.
    Otherwise, use collection_info (namespace, name) from ansible-galaxy output
    if available, or fall back to reading galaxy.yml from the installed collection.

    Args:
        role: Role name (short or fully qualified).
        collections_dir: Path where collections are installed.
        collection_info: Optional (namespace, name) tuple identifying the
            primary collection, as parsed from ansible-galaxy install output.
    """
    if role.count(".") >= 2:
        return role

    # Fast path: use collection_info from ansible-galaxy output
    if collection_info is not None:
        namespace, name = collection_info
        return f"{namespace}.{name}.{role}"

    # Fallback: find galaxy.yml in installed collections
    pattern = f"{collections_dir}/ansible_collections/*/*/galaxy.yml"
    galaxy_files = glob(pattern)

    if not galaxy_files:
        raise RuntimeError(
            f"No galaxy.yml found in {collections_dir}. "
            "Ensure the repo is a valid Ansible collection."
        )

    if len(galaxy_files) > 1:
        raise RuntimeError(
            f"Multiple collections found in {collections_dir} and no "
            "collection_info provided. Pass the role as a fully qualified "
            "name (namespace.collection.role) to avoid ambiguity."
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
