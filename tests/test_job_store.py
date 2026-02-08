# tests/test_job_store.py
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from ansible_runner_service.job_store import JobStore, Job, JobStatus


@pytest.fixture
def mock_redis():
    return MagicMock()


@pytest.fixture
def job_store(mock_redis):
    return JobStore(mock_redis)


class TestJobStore:
    def test_create_job(self, job_store, mock_redis):
        job = job_store.create_job(
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
        )

        assert job.job_id is not None
        assert job.status == JobStatus.PENDING
        assert job.playbook == "hello.yml"
        assert job.extra_vars == {"name": "World"}
        assert job.created_at is not None
        mock_redis.hset.assert_called()

    def test_get_job(self, job_store, mock_redis):
        mock_redis.hgetall.return_value = {
            b"job_id": b"test-123",
            b"status": b"pending",
            b"playbook": b"hello.yml",
            b"extra_vars": b'{"name": "World"}',
            b"inventory": b"localhost,",
            b"created_at": b"2026-01-21T10:00:00+00:00",
            b"started_at": b"",
            b"finished_at": b"",
            b"result": b"",
            b"error": b"",
        }

        job = job_store.get_job("test-123")

        assert job is not None
        assert job.job_id == "test-123"
        assert job.status == JobStatus.PENDING
        mock_redis.hgetall.assert_called_with("job:test-123")

    def test_get_job_not_found(self, job_store, mock_redis):
        mock_redis.hgetall.return_value = {}

        job = job_store.get_job("nonexistent")

        assert job is None

    def test_update_job_status(self, job_store, mock_redis):
        job_store.update_status("test-123", JobStatus.RUNNING)

        mock_redis.hset.assert_called()


class TestJobStoreWithDB:
    def test_create_job_writes_to_db(self):
        from ansible_runner_service.job_store import JobStore

        mock_redis = MagicMock()
        mock_repo = MagicMock()

        store = JobStore(mock_redis, repository=mock_repo)
        job = store.create_job(
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
        )

        # Verify DB write
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        assert call_kwargs["playbook"] == "hello.yml"
        assert call_kwargs["extra_vars"] == {"name": "World"}
        assert call_kwargs["inventory"] == "localhost,"

    def test_update_status_writes_to_db(self):
        from ansible_runner_service.job_store import JobStore, JobStatus
        from datetime import datetime, timezone

        mock_redis = MagicMock()
        mock_repo = MagicMock()

        store = JobStore(mock_redis, repository=mock_repo)
        now = datetime.now(timezone.utc)

        store.update_status(
            "test-123",
            JobStatus.RUNNING,
            started_at=now,
        )

        # Verify DB update
        mock_repo.update_status.assert_called_once_with(
            "test-123",
            "running",
            started_at=now,
            finished_at=None,
            result_rc=None,
            result_stdout=None,
            result_stats=None,
            error=None,
        )

    def test_create_job_works_without_repo(self):
        """Backwards compatibility: works without repository."""
        from ansible_runner_service.job_store import JobStore

        mock_redis = MagicMock()
        store = JobStore(mock_redis)  # No repository

        job = store.create_job(
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )

        assert job.playbook == "hello.yml"

    def test_create_job_rollbacks_redis_on_db_failure(self):
        """Strict consistency: Redis key deleted if DB write fails."""
        from ansible_runner_service.job_store import JobStore

        mock_redis = MagicMock()
        mock_repo = MagicMock()
        mock_repo.create.side_effect = Exception("DB connection failed")

        store = JobStore(mock_redis, repository=mock_repo)

        with pytest.raises(Exception, match="DB connection failed"):
            store.create_job(
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
            )

        # Verify Redis key was deleted for rollback
        mock_redis.delete.assert_called_once()

    def test_update_status_no_redis_update_on_db_failure(self):
        """Strict consistency: Redis not updated if DB write fails."""
        from ansible_runner_service.job_store import JobStore, JobStatus

        mock_redis = MagicMock()
        mock_repo = MagicMock()
        mock_repo.update_status.side_effect = Exception("DB connection failed")

        store = JobStore(mock_redis, repository=mock_repo)

        with pytest.raises(Exception, match="DB connection failed"):
            store.update_status("test-123", JobStatus.RUNNING)

        # Verify Redis hset was NOT called (DB failed first)
        mock_redis.hset.assert_not_called()


class TestJobStoreSourceFields:
    @pytest.fixture
    def mock_redis(self):
        return MagicMock()

    @pytest.fixture
    def mock_repo(self):
        return MagicMock()

    def test_create_job_with_source(self, mock_redis):
        store = JobStore(mock_redis)
        job = store.create_job(
            playbook="deploy/app.yml",
            extra_vars={},
            inventory="localhost,",
            source_type="playbook",
            source_repo="https://dev.azure.com/xxxit/p/_git/r",
            source_branch="main",
        )
        assert job.source_type == "playbook"
        assert job.source_repo == "https://dev.azure.com/xxxit/p/_git/r"
        assert job.source_branch == "main"

    def test_create_job_default_local(self, mock_redis):
        store = JobStore(mock_redis)
        job = store.create_job(
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )
        assert job.source_type == "local"
        assert job.source_repo is None
        assert job.source_branch is None

    def test_create_job_with_source_writes_to_db(self, mock_redis, mock_repo):
        store = JobStore(mock_redis, repository=mock_repo)
        store.create_job(
            playbook="deploy/app.yml",
            extra_vars={},
            inventory="localhost,",
            source_type="playbook",
            source_repo="https://dev.azure.com/xxxit/p/_git/r",
            source_branch="main",
        )
        mock_repo.create.assert_called_once()
        call_kwargs = mock_repo.create.call_args[1]
        assert call_kwargs["source_type"] == "playbook"
        assert call_kwargs["source_repo"] == "https://dev.azure.com/xxxit/p/_git/r"
        assert call_kwargs["source_branch"] == "main"

    def test_source_fields_in_redis(self, mock_redis):
        store = JobStore(mock_redis)
        store.create_job(
            playbook="deploy/app.yml",
            extra_vars={},
            inventory="localhost,",
            source_type="playbook",
            source_repo="https://dev.azure.com/xxxit/p/_git/r",
            source_branch="main",
        )
        # Verify Redis hset was called with source fields
        call_args = mock_redis.hset.call_args
        data = call_args.kwargs.get("mapping") or call_args[1].get("mapping")
        assert data["source_type"] == "playbook"
        assert data["source_repo"] == "https://dev.azure.com/xxxit/p/_git/r"
        assert data["source_branch"] == "main"

    def test_deserialize_job_with_source(self, mock_redis):
        store = JobStore(mock_redis)
        mock_redis.hgetall.return_value = {
            b"job_id": b"test-123",
            b"status": b"pending",
            b"playbook": b"deploy/app.yml",
            b"extra_vars": b'{}',
            b"inventory": b"localhost,",
            b"created_at": b"2026-01-29T10:00:00+00:00",
            b"started_at": b"",
            b"finished_at": b"",
            b"result": b"",
            b"error": b"",
            b"source_type": b"playbook",
            b"source_repo": b"https://dev.azure.com/xxxit/p/_git/r",
            b"source_branch": b"main",
        }
        job = store.get_job("test-123")
        assert job.source_type == "playbook"
        assert job.source_repo == "https://dev.azure.com/xxxit/p/_git/r"
        assert job.source_branch == "main"


class _FakeRedis:
    """Minimal in-memory Redis mock supporting hset/hgetall/expire/delete."""

    def __init__(self):
        self._data: dict[str, dict[bytes, bytes]] = {}

    def hset(self, name: str, key=None, value=None, mapping=None):
        if name not in self._data:
            self._data[name] = {}
        if mapping:
            for k, v in mapping.items():
                self._data[name][k.encode() if isinstance(k, str) else k] = (
                    v.encode() if isinstance(v, str) else v
                )
        if key is not None and value is not None:
            k = key.encode() if isinstance(key, str) else key
            v = value.encode() if isinstance(value, str) else value
            self._data[name][k] = v

    def hgetall(self, name: str) -> dict[bytes, bytes]:
        return dict(self._data.get(name, {}))

    def expire(self, name: str, time: int):
        pass

    def delete(self, *names):
        for name in names:
            self._data.pop(name, None)


class TestJobStoreInventoryAndOptions:
    @pytest.fixture
    def redis(self):
        return _FakeRedis()

    def test_create_job_with_inline_inventory(self, redis):
        store = JobStore(redis)
        inventory = {"type": "inline", "data": {"all": {"hosts": {"host1": None}}}}
        job = store.create_job(
            playbook="test.yml", extra_vars={}, inventory=inventory
        )
        assert job.inventory == inventory

        retrieved = store.get_job(job.job_id)
        assert retrieved.inventory == inventory

    def test_create_job_with_string_inventory(self, redis):
        store = JobStore(redis)
        job = store.create_job(
            playbook="test.yml", extra_vars={}, inventory="localhost,"
        )
        assert job.inventory == "localhost,"

        retrieved = store.get_job(job.job_id)
        assert retrieved.inventory == "localhost,"

    def test_create_job_with_options(self, redis):
        store = JobStore(redis)
        options = {"check": True, "tags": ["deploy"], "verbosity": 2}
        job = store.create_job(
            playbook="test.yml", extra_vars={}, inventory="localhost,",
            options=options,
        )
        assert job.options == options

        retrieved = store.get_job(job.job_id)
        assert retrieved.options == options

    def test_create_job_without_options(self, redis):
        store = JobStore(redis)
        job = store.create_job(
            playbook="test.yml", extra_vars={}, inventory="localhost,"
        )
        assert job.options is None

        retrieved = store.get_job(job.job_id)
        assert retrieved.options is None
