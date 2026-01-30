# tests/test_runner.py
from pathlib import Path
from unittest.mock import patch, MagicMock

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


class TestRunPlaybookAbsolutePath:
    @patch("ansible_runner_service.runner.ansible_runner.run")
    def test_run_with_absolute_playbook_path(self, mock_run):
        """When playbook is absolute path, use it directly without playbooks_dir."""
        mock_runner = MagicMock()
        mock_runner.status = "successful"
        mock_runner.rc = 0
        mock_runner.stdout = MagicMock()
        mock_runner.stdout.read.return_value = "ok"
        mock_runner.stats = {}
        mock_run.return_value = mock_runner

        result = run_playbook(
            playbook="/tmp/job-xxx/repo/deploy.yml",
            extra_vars={},
            inventory="localhost,",
        )

        assert result.status == "successful"
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["playbook"] == "/tmp/job-xxx/repo/deploy.yml"

    @patch("ansible_runner_service.runner.ansible_runner.run")
    def test_run_with_envvars(self, mock_run):
        """Support passing environment variables to ansible-runner."""
        mock_runner = MagicMock()
        mock_runner.status = "successful"
        mock_runner.rc = 0
        mock_runner.stdout = MagicMock()
        mock_runner.stdout.read.return_value = "ok"
        mock_runner.stats = {}
        mock_run.return_value = mock_runner

        run_playbook(
            playbook="/tmp/playbook.yml",
            extra_vars={},
            inventory="localhost,",
            envvars={"ANSIBLE_COLLECTIONS_PATH": "/tmp/collections"},
        )

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["envvars"]["ANSIBLE_COLLECTIONS_PATH"] == "/tmp/collections"
