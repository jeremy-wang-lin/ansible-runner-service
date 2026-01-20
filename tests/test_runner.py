# tests/test_runner.py
from pathlib import Path

from ansible_runner_service.runner import run_playbook, RunResult


class TestRunPlaybook:
    def test_successful_run(self, tmp_path: Path):
        # Create a minimal playbook
        playbook = tmp_path / "test.yml"
        playbook.write_text("""
---
- name: Test
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Debug
      ansible.builtin.debug:
        msg: "Hello!"
""")
        result = run_playbook(
            playbook="test.yml",
            extra_vars={},
            inventory="localhost,",
            playbooks_dir=tmp_path,
        )

        assert isinstance(result, RunResult)
        assert result.status == "successful"
        assert result.rc == 0
        assert "Hello!" in result.stdout

    def test_with_extra_vars(self, tmp_path: Path):
        playbook = tmp_path / "greet.yml"
        playbook.write_text("""
---
- name: Greet
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Say name
      ansible.builtin.debug:
        msg: "Hi {{ name }}!"
""")
        result = run_playbook(
            playbook="greet.yml",
            extra_vars={"name": "Claude"},
            inventory="localhost,",
            playbooks_dir=tmp_path,
        )

        assert result.status == "successful"
        assert "Hi Claude!" in result.stdout
