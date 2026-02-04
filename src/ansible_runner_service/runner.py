# src/ansible_runner_service/runner.py
import tempfile
from dataclasses import dataclass
from pathlib import Path

import ansible_runner


@dataclass
class RunResult:
    status: str
    rc: int
    stdout: str
    stats: dict


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
