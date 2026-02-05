# tests/test_worker.py
import os
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from ansible_runner_service.worker import execute_job
from ansible_runner_service.job_store import JobStatus, JobResult
from ansible_runner_service.runner import RunResult


@pytest.fixture
def mock_redis():
    return MagicMock()


@pytest.fixture
def mock_session():
    """Mock database session."""
    session = MagicMock()
    return session


class TestExecuteJob:
    @patch("ansible_runner_service.worker.JobStore")
    @patch("ansible_runner_service.worker.JobRepository")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_successful_execution(
        self,
        mock_get_playbooks_dir,
        mock_run_playbook,
        mock_get_redis,
        mock_get_engine,
        mock_get_session,
        mock_job_repo_class,
        mock_job_store_class,
    ):
        mock_store = MagicMock()
        mock_job_store_class.return_value = mock_store
        mock_get_playbooks_dir.return_value = "/playbooks"
        mock_run_playbook.return_value = RunResult(
            status="successful",
            rc=0,
            stdout="Hello, World!",
            stats={"localhost": {"ok": 1}},
        )

        execute_job(
            job_id="test-123",
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
        )

        # Verify status updated to running
        calls = mock_store.update_status.call_args_list
        assert calls[0].args[1] == JobStatus.RUNNING

        # Verify status updated to successful with result
        assert calls[1].args[1] == JobStatus.SUCCESSFUL
        assert calls[1].kwargs["result"].rc == 0

    @patch("ansible_runner_service.worker.JobStore")
    @patch("ansible_runner_service.worker.JobRepository")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_failed_execution(
        self,
        mock_get_playbooks_dir,
        mock_run_playbook,
        mock_get_redis,
        mock_get_engine,
        mock_get_session,
        mock_job_repo_class,
        mock_job_store_class,
    ):
        mock_store = MagicMock()
        mock_job_store_class.return_value = mock_store
        mock_get_playbooks_dir.return_value = "/playbooks"
        mock_run_playbook.side_effect = Exception("Playbook error")

        execute_job(
            job_id="test-123",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )

        # Verify status updated to failed with error
        calls = mock_store.update_status.call_args_list
        assert calls[1].args[1] == JobStatus.FAILED
        assert "Playbook error" in calls[1].kwargs["error"]


class TestExecuteJobWithDB:
    def test_writes_to_db_on_status_changes(self, mock_redis):
        from unittest.mock import MagicMock, patch
        from ansible_runner_service.worker import execute_job

        mock_result = MagicMock()
        mock_result.rc = 0
        mock_result.stdout = "PLAY [Hello]..."
        mock_result.stats = {"localhost": {"ok": 1}}

        mock_repo = MagicMock()
        mock_session = MagicMock()
        mock_session_factory = MagicMock(return_value=mock_session)

        with patch("ansible_runner_service.worker.run_playbook", return_value=mock_result):
            with patch("ansible_runner_service.worker.get_engine_singleton"):
                with patch("ansible_runner_service.worker.get_session", return_value=mock_session_factory):
                    with patch("ansible_runner_service.worker.JobRepository", return_value=mock_repo) as mock_repo_class:
                        with patch("ansible_runner_service.worker.get_redis", return_value=mock_redis):
                            execute_job(
                                job_id="test-123",
                                playbook="hello.yml",
                                extra_vars={},
                                inventory="localhost,",
                            )

        # Verify repository.update_status was called twice (running, then successful)
        assert mock_repo.update_status.call_count == 2

        # Verify first call was for RUNNING status
        first_call = mock_repo.update_status.call_args_list[0]
        assert first_call.args[0] == "test-123"
        assert first_call.args[1] == "running"

        # Verify second call was for SUCCESSFUL status
        second_call = mock_repo.update_status.call_args_list[1]
        assert second_call.args[0] == "test-123"
        assert second_call.args[1] == "successful"

        # Verify session was closed
        mock_session.close.assert_called_once()


class TestPathTraversalProtection:
    @patch("ansible_runner_service.worker.validate_repo_url")
    @patch("ansible_runner_service.worker.load_providers")
    @patch("ansible_runner_service.worker.clone_repo")
    def test_symlink_escape_blocked(
        self,
        mock_clone,
        mock_load_providers,
        mock_validate,
        tmp_path,
    ):
        """Symlink inside cloned repo must not escape repo_dir."""
        mock_validate.return_value = MagicMock()

        # Simulate clone: create repo_dir with a symlink that escapes
        def fake_clone(repo_url, branch, target_dir, provider):
            os.makedirs(target_dir)
            # Create a symlink pointing outside the repo
            escape_target = tmp_path / "secret"
            escape_target.mkdir()
            (escape_target / "playbook.yml").write_text("---")
            os.symlink(str(escape_target), os.path.join(target_dir, "escape"))

        mock_clone.side_effect = fake_clone

        source_config = {
            "type": "playbook",
            "repo": "https://dev.azure.com/xxxit/p/_git/r",
            "branch": "main",
            "path": "escape/playbook.yml",
        }

        from ansible_runner_service.worker import _execute_git_playbook
        with pytest.raises(RuntimeError, match="outside.*repo"):
            _execute_git_playbook(source_config, {}, "localhost,")

    @patch("ansible_runner_service.worker.validate_repo_url")
    @patch("ansible_runner_service.worker.load_providers")
    @patch("ansible_runner_service.worker.clone_repo")
    def test_dotdot_traversal_blocked(
        self,
        mock_clone,
        mock_load_providers,
        mock_validate,
        tmp_path,
    ):
        """Path with '..' segments escaping repo_dir must be rejected."""
        mock_validate.return_value = MagicMock()

        def fake_clone(repo_url, branch, target_dir, provider):
            os.makedirs(os.path.join(target_dir, "deploy"))
            # Create a file outside repo_dir that the traversal would reach
            (tmp_path / "etc_shadow").write_text("---")

        mock_clone.side_effect = fake_clone

        source_config = {
            "type": "playbook",
            "repo": "https://dev.azure.com/xxxit/p/_git/r",
            "branch": "main",
            "path": "deploy/../../etc_shadow",
        }

        from ansible_runner_service.worker import _execute_git_playbook
        with pytest.raises(RuntimeError, match="outside.*repo"):
            _execute_git_playbook(source_config, {}, "localhost,")


class TestExecuteJobWithGitSource:
    @patch("ansible_runner_service.worker.JobStore")
    @patch("ansible_runner_service.worker.JobRepository")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker._execute_git_playbook")
    def test_git_playbook_source(
        self,
        mock_exec_git_playbook,
        mock_get_redis,
        mock_get_engine,
        mock_get_session,
        mock_job_repo_class,
        mock_job_store_class,
    ):
        mock_store = MagicMock()
        mock_job_store_class.return_value = mock_store
        mock_exec_git_playbook.return_value = RunResult(
            status="successful", rc=0, stdout="ok", stats={},
        )

        source_config = {
            "type": "playbook",
            "repo": "https://dev.azure.com/xxxit/p/_git/r",
            "branch": "main",
            "path": "deploy/app.yml",
        }

        execute_job(
            job_id="test-git-123",
            playbook="deploy/app.yml",
            extra_vars={},
            inventory="localhost,",
            source_config=source_config,
        )

        mock_exec_git_playbook.assert_called_once_with(source_config, {}, "localhost,", None)

        calls = mock_store.update_status.call_args_list
        assert calls[0].args[1] == JobStatus.RUNNING
        assert calls[1].args[1] == JobStatus.SUCCESSFUL

    @patch("ansible_runner_service.worker.JobStore")
    @patch("ansible_runner_service.worker.JobRepository")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker._execute_git_role")
    def test_git_role_source(
        self,
        mock_exec_git_role,
        mock_get_redis,
        mock_get_engine,
        mock_get_session,
        mock_job_repo_class,
        mock_job_store_class,
    ):
        mock_store = MagicMock()
        mock_job_store_class.return_value = mock_store
        mock_exec_git_role.return_value = RunResult(
            status="successful", rc=0, stdout="ok", stats={},
        )

        source_config = {
            "type": "role",
            "repo": "https://gitlab.company.com/platform-team/col.git",
            "branch": "v2.0.0",
            "role": "nginx",
            "role_vars": {"nginx_port": 8080},
        }

        execute_job(
            job_id="test-role-123",
            playbook="mycompany.infra.nginx",
            extra_vars={},
            inventory="localhost,",
            source_config=source_config,
        )

        mock_exec_git_role.assert_called_once_with(source_config, {}, "localhost,", None)
        calls = mock_store.update_status.call_args_list
        assert calls[1].args[1] == JobStatus.SUCCESSFUL

    @patch("ansible_runner_service.worker.JobStore")
    @patch("ansible_runner_service.worker.JobRepository")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker._execute_local")
    def test_local_source_unchanged(
        self,
        mock_exec_local,
        mock_get_redis,
        mock_get_engine,
        mock_get_session,
        mock_job_repo_class,
        mock_job_store_class,
    ):
        """Legacy local source still works when source_config is None."""
        mock_store = MagicMock()
        mock_job_store_class.return_value = mock_store
        mock_exec_local.return_value = RunResult(
            status="successful", rc=0, stdout="ok", stats={},
        )

        execute_job(
            job_id="test-local-123",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )

        mock_exec_local.assert_called_once_with("hello.yml", {}, "localhost,", None)


class TestInlineInventory:
    @patch("ansible_runner_service.worker.JobStore")
    @patch("ansible_runner_service.worker.JobRepository")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_inline_inventory_writes_yaml(
        self,
        mock_get_playbooks_dir,
        mock_run_playbook,
        mock_get_redis,
        mock_get_engine,
        mock_get_session,
        mock_job_repo_class,
        mock_job_store_class,
    ):
        """Inline inventory dict is written as YAML file."""
        mock_store = MagicMock()
        mock_job_store_class.return_value = mock_store
        mock_get_playbooks_dir.return_value = "/playbooks"
        mock_run_playbook.return_value = RunResult(
            status="successful", rc=0, stdout="ok", stats={},
        )

        inventory = {
            "type": "inline",
            "data": {"webservers": {"hosts": {"10.0.1.10": None}}},
        }

        execute_job(
            job_id="inv-test",
            playbook="test.yml",
            extra_vars={},
            inventory=inventory,
        )

        call_kwargs = mock_run_playbook.call_args
        inv_arg = call_kwargs[1].get("inventory")
        # inventory arg should be a file path string, not the dict
        assert isinstance(inv_arg, str)
        assert inv_arg != str(inventory)  # Not just str(dict)

    @patch("ansible_runner_service.worker.JobStore")
    @patch("ansible_runner_service.worker.JobRepository")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_string_inventory_passed_through(
        self,
        mock_get_playbooks_dir,
        mock_run_playbook,
        mock_get_redis,
        mock_get_engine,
        mock_get_session,
        mock_job_repo_class,
        mock_job_store_class,
    ):
        """String inventory passed directly to runner."""
        mock_store = MagicMock()
        mock_job_store_class.return_value = mock_store
        mock_get_playbooks_dir.return_value = "/playbooks"
        mock_run_playbook.return_value = RunResult(
            status="successful", rc=0, stdout="ok", stats={},
        )

        execute_job(
            job_id="str-test",
            playbook="test.yml",
            extra_vars={},
            inventory="localhost,",
        )

        # Check run_playbook was called with string inventory
        call_kwargs = mock_run_playbook.call_args[1]
        assert call_kwargs["inventory"] == "localhost,"


class TestWorkerExecutionOptions:
    @patch("ansible_runner_service.worker.JobStore")
    @patch("ansible_runner_service.worker.JobRepository")
    @patch("ansible_runner_service.worker.get_session")
    @patch("ansible_runner_service.worker.get_engine_singleton")
    @patch("ansible_runner_service.worker.get_redis")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_options_passed_to_runner(
        self,
        mock_get_playbooks_dir,
        mock_run_playbook,
        mock_get_redis,
        mock_get_engine,
        mock_get_session,
        mock_job_repo_class,
        mock_job_store_class,
    ):
        """Options dict is forwarded to run_playbook."""
        mock_store = MagicMock()
        mock_job_store_class.return_value = mock_store
        mock_get_playbooks_dir.return_value = "/playbooks"
        mock_run_playbook.return_value = RunResult(
            status="successful", rc=0, stdout="ok", stats={},
        )

        options = {"check": True, "tags": ["deploy"]}

        execute_job(
            job_id="opt-test",
            playbook="test.yml",
            extra_vars={},
            inventory="localhost,",
            options=options,
        )

        call_kwargs = mock_run_playbook.call_args[1]
        assert call_kwargs["options"] == options
