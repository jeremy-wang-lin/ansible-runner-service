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
    playbooks_dir: Path,
) -> RunResult:
    """Run an Ansible playbook synchronously and return results."""
    playbook_path = playbooks_dir / playbook

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ansible_runner.run(
            private_data_dir=tmpdir,
            playbook=str(playbook_path),
            inventory=inventory,
            extravars=extra_vars,
            quiet=False,
        )

        stdout = runner.stdout.read() if runner.stdout else ""

        return RunResult(
            status=runner.status,
            rc=runner.rc,
            stdout=stdout,
            stats=runner.stats or {},
        )
