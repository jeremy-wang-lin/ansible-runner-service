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
from ansible_runner_service.schemas import PlaybookSourceConfig, RoleSourceConfig, SourceConfig


# Engine singleton for connection reuse
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


def _execute_git_playbook(source_config: PlaybookSourceConfig, extra_vars, inventory):
    """Clone repo and execute playbook from it.

    Note: provider validation here serves two purposes — security
    (defense-in-depth) and credential lookup.  The API and worker
    MUST share the same GIT_PROVIDERS configuration.
    """
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

        # Verify resolved path stays inside repo_dir (blocks symlink escapes)
        resolved = Path(playbook_path).resolve()
        repo_root = Path(repo_dir).resolve()
        if not resolved.is_relative_to(repo_root):
            raise RuntimeError(
                f"Playbook path resolves outside repo directory"
            )

        return run_playbook(
            playbook=playbook_path,
            extra_vars=extra_vars,
            inventory=inventory,
        )


def _execute_git_role(source_config: RoleSourceConfig, extra_vars, inventory):
    """Install collection and execute role.

    See _execute_git_playbook docstring for note on dual validation.
    """
    providers = load_providers()
    provider = validate_repo_url(source_config["repo"], providers)

    with tempfile.TemporaryDirectory() as tmpdir:
        collections_dir = os.path.join(tmpdir, "collections")
        os.makedirs(collections_dir)

        collection_info = install_collection(
            repo_url=source_config["repo"],
            branch=source_config.get("branch", "main"),
            collections_dir=collections_dir,
            provider=provider,
        )

        fqcn = resolve_fqcn(source_config["role"], collections_dir, collection_info)
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
    source_config: SourceConfig | None = None,
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
