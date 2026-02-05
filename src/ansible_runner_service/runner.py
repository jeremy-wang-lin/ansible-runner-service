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
    options: dict | None = None,
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

        if options:
            if options.get("tags"):
                run_kwargs["tags"] = ",".join(options["tags"])
            if options.get("skip_tags"):
                run_kwargs["skip_tags"] = ",".join(options["skip_tags"])
            if options.get("limit"):
                run_kwargs["limit"] = options["limit"]
            if options.get("verbosity"):
                run_kwargs["verbosity"] = options["verbosity"]

            cmdline_parts = []
            if options.get("check"):
                cmdline_parts.append("--check")
            if options.get("diff"):
                cmdline_parts.append("--diff")
            if cmdline_parts:
                run_kwargs["cmdline"] = " ".join(cmdline_parts)

        runner = ansible_runner.run(**run_kwargs)

        stdout = runner.stdout.read() if runner.stdout else ""

        return RunResult(
            status=runner.status,
            rc=runner.rc,
            stdout=stdout,
            stats=runner.stats or {},
        )
