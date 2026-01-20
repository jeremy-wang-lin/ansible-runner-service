# Async Job Queue Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Redis + rq for async job execution with sync fallback option.

**Architecture:** FastAPI submits jobs to Redis queue, rq worker processes them, job state stored in Redis. Sync mode bypasses queue and executes directly.

**Tech Stack:** FastAPI, rq, redis-py, Docker Compose

---

## Task 1: Add Dependencies and Docker Compose

**Files:**
- Modify: `pyproject.toml`
- Create: `docker-compose.yml`

**Step 1: Update pyproject.toml dependencies**

Add to dependencies list:
```toml
    "rq>=1.16.0",
    "redis>=5.0.0",
```

**Step 2: Create docker-compose.yml**

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3
```

**Step 3: Install updated dependencies**

Run: `pip install -e ".[dev]"`

**Step 4: Verify Redis starts**

Run: `docker-compose up -d && docker-compose ps`
Expected: Redis container running and healthy

**Step 5: Verify redis-py works**

Run: `python3 -c "import redis; r = redis.Redis(); r.ping(); print('OK')"`
Expected: `OK`

**Step 6: Commit**

```bash
git add pyproject.toml docker-compose.yml
git commit -m "feat: add Redis and rq dependencies with docker-compose"
```

---

## Task 2: Job Store (Redis-backed)

**Files:**
- Create: `src/ansible_runner_service/job_store.py`
- Create: `tests/test_job_store.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_job_store.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write implementation**

```python
# src/ansible_runner_service/job_store.py
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from redis import Redis


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESSFUL = "successful"
    FAILED = "failed"


@dataclass
class JobResult:
    rc: int
    stdout: str
    stats: dict[str, Any]


@dataclass
class Job:
    job_id: str
    status: JobStatus
    playbook: str
    extra_vars: dict[str, Any]
    inventory: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: JobResult | None = None
    error: str | None = None


class JobStore:
    def __init__(self, redis: Redis, ttl: int = 86400):
        self.redis = redis
        self.ttl = ttl  # 24 hours default

    def _job_key(self, job_id: str) -> str:
        return f"job:{job_id}"

    def create_job(
        self,
        playbook: str,
        extra_vars: dict[str, Any],
        inventory: str,
    ) -> Job:
        job = Job(
            job_id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            playbook=playbook,
            extra_vars=extra_vars,
            inventory=inventory,
            created_at=datetime.now(timezone.utc),
        )
        self._save_job(job)
        return job

    def get_job(self, job_id: str) -> Job | None:
        data = self.redis.hgetall(self._job_key(job_id))
        if not data:
            return None
        return self._deserialize_job(data)

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        result: JobResult | None = None,
        error: str | None = None,
    ) -> None:
        updates = {"status": status.value}
        if started_at:
            updates["started_at"] = started_at.isoformat()
        if finished_at:
            updates["finished_at"] = finished_at.isoformat()
        if result:
            updates["result"] = json.dumps(asdict(result))
        if error:
            updates["error"] = error
        self.redis.hset(self._job_key(job_id), mapping=updates)

    def _save_job(self, job: Job) -> None:
        data = {
            "job_id": job.job_id,
            "status": job.status.value,
            "playbook": job.playbook,
            "extra_vars": json.dumps(job.extra_vars),
            "inventory": job.inventory,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else "",
            "finished_at": job.finished_at.isoformat() if job.finished_at else "",
            "result": json.dumps(asdict(job.result)) if job.result else "",
            "error": job.error or "",
        }
        self.redis.hset(self._job_key(job.job_id), mapping=data)
        self.redis.expire(self._job_key(job.job_id), self.ttl)

    def _deserialize_job(self, data: dict[bytes, bytes]) -> Job:
        def get_str(key: str) -> str:
            return data.get(key.encode(), b"").decode()

        result_str = get_str("result")
        result = None
        if result_str:
            result_dict = json.loads(result_str)
            result = JobResult(**result_dict)

        started_str = get_str("started_at")
        finished_str = get_str("finished_at")

        return Job(
            job_id=get_str("job_id"),
            status=JobStatus(get_str("status")),
            playbook=get_str("playbook"),
            extra_vars=json.loads(get_str("extra_vars")),
            inventory=get_str("inventory"),
            created_at=datetime.fromisoformat(get_str("created_at")),
            started_at=datetime.fromisoformat(started_str) if started_str else None,
            finished_at=datetime.fromisoformat(finished_str) if finished_str else None,
            result=result,
            error=get_str("error") or None,
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_job_store.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/job_store.py tests/test_job_store.py
git commit -m "feat: add Redis-backed job store with Job dataclass"
```

---

## Task 3: Queue Module (rq integration)

**Files:**
- Create: `src/ansible_runner_service/queue.py`
- Create: `tests/test_queue.py`

**Step 1: Write the failing test**

```python
# tests/test_queue.py
import pytest
from unittest.mock import MagicMock, patch

from ansible_runner_service.queue import enqueue_job


class TestEnqueueJob:
    def test_enqueue_job(self):
        mock_queue = MagicMock()

        with patch("ansible_runner_service.queue.Queue", return_value=mock_queue):
            enqueue_job(
                job_id="test-123",
                playbook="hello.yml",
                extra_vars={"name": "World"},
                inventory="localhost,",
            )

        mock_queue.enqueue.assert_called_once()
        call_args = mock_queue.enqueue.call_args
        assert call_args.kwargs["job_id"] == "test-123"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_queue.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write implementation**

```python
# src/ansible_runner_service/queue.py
from typing import Any

from redis import Redis
from rq import Queue


def get_queue(redis: Redis) -> Queue:
    return Queue(connection=redis)


def enqueue_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str,
    redis: Redis | None = None,
) -> None:
    """Enqueue a job for async execution."""
    if redis is None:
        redis = Redis()
    queue = Queue(connection=redis)
    queue.enqueue(
        "ansible_runner_service.worker.execute_job",
        job_id=job_id,
        playbook=playbook,
        extra_vars=extra_vars,
        inventory=inventory,
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_queue.py -v`
Expected: PASS (1 test)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/queue.py tests/test_queue.py
git commit -m "feat: add rq queue module for job enqueueing"
```

---

## Task 4: Worker Module

**Files:**
- Create: `src/ansible_runner_service/worker.py`
- Create: `tests/test_worker.py`

**Step 1: Write the failing test**

```python
# tests/test_worker.py
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from ansible_runner_service.worker import execute_job
from ansible_runner_service.job_store import JobStatus, JobResult
from ansible_runner_service.runner import RunResult


class TestExecuteJob:
    @patch("ansible_runner_service.worker.get_job_store")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_successful_execution(
        self, mock_get_playbooks_dir, mock_run_playbook, mock_get_job_store
    ):
        mock_store = MagicMock()
        mock_get_job_store.return_value = mock_store
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

    @patch("ansible_runner_service.worker.get_job_store")
    @patch("ansible_runner_service.worker.run_playbook")
    @patch("ansible_runner_service.worker.get_playbooks_dir")
    def test_failed_execution(
        self, mock_get_playbooks_dir, mock_run_playbook, mock_get_job_store
    ):
        mock_store = MagicMock()
        mock_get_job_store.return_value = mock_store
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
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write implementation**

```python
# src/ansible_runner_service/worker.py
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from redis import Redis

from ansible_runner_service.job_store import JobStore, JobStatus, JobResult
from ansible_runner_service.runner import run_playbook


def get_redis() -> Redis:
    return Redis()


def get_job_store() -> JobStore:
    return JobStore(get_redis())


def get_playbooks_dir() -> Path:
    return Path(__file__).parent.parent.parent / "playbooks"


def execute_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str,
) -> None:
    """Execute a job - called by rq worker."""
    store = get_job_store()
    playbooks_dir = get_playbooks_dir()

    # Mark as running
    store.update_status(
        job_id,
        JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )

    try:
        result = run_playbook(
            playbook=playbook,
            extra_vars=extra_vars,
            inventory=inventory,
            playbooks_dir=playbooks_dir,
        )

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
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_worker.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/worker.py tests/test_worker.py
git commit -m "feat: add worker module for async job execution"
```

---

## Task 5: Update Schemas

**Files:**
- Modify: `src/ansible_runner_service/schemas.py`
- Modify: `tests/test_schemas.py`

**Step 1: Write the failing test**

Add to `tests/test_schemas.py`:

```python
from ansible_runner_service.schemas import JobSubmitResponse, JobDetail, JobResultSchema


class TestJobSubmitResponse:
    def test_create_response(self):
        resp = JobSubmitResponse(
            job_id="abc123",
            status="pending",
            created_at="2026-01-21T10:00:00Z",
        )
        assert resp.job_id == "abc123"
        assert resp.status == "pending"


class TestJobDetail:
    def test_create_detail(self):
        detail = JobDetail(
            job_id="abc123",
            status="successful",
            playbook="hello.yml",
            created_at="2026-01-21T10:00:00Z",
            started_at="2026-01-21T10:00:01Z",
            finished_at="2026-01-21T10:00:05Z",
            result=JobResultSchema(
                rc=0,
                stdout="Hello!",
                stats={"localhost": {"ok": 1}},
            ),
        )
        assert detail.job_id == "abc123"
        assert detail.result.rc == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_schemas.py::TestJobSubmitResponse -v`
Expected: FAIL with `ImportError`

**Step 3: Update schemas.py**

```python
# src/ansible_runner_service/schemas.py
from typing import Any

from pydantic import BaseModel, Field


class JobRequest(BaseModel):
    playbook: str = Field(..., min_length=1)
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str = "localhost,"


class JobResponse(BaseModel):
    """Sync response - full result."""
    status: str
    rc: int
    stdout: str
    stats: dict[str, Any]


class JobSubmitResponse(BaseModel):
    """Async response - job reference."""
    job_id: str
    status: str
    created_at: str


class JobResultSchema(BaseModel):
    """Job execution result."""
    rc: int
    stdout: str
    stats: dict[str, Any]


class JobDetail(BaseModel):
    """Full job details for GET /jobs/{id}."""
    job_id: str
    status: str
    playbook: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: JobResultSchema | None = None
    error: str | None = None
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "feat: add JobSubmitResponse and JobDetail schemas"
```

---

## Task 6: Update API Endpoints

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Modify: `tests/test_api.py`

**Step 1: Write failing tests for new behavior**

Add to `tests/test_api.py`:

```python
class TestAsyncJobs:
    async def test_submit_async_job(self, client: AsyncClient):
        """Default behavior - async submission."""
        with patch("ansible_runner_service.main.enqueue_job"):
            with patch("ansible_runner_service.main.get_job_store") as mock_store:
                mock_store.return_value.create_job.return_value = MagicMock(
                    job_id="test-123",
                    status=MagicMock(value="pending"),
                    created_at=datetime(2026, 1, 21, 10, 0, 0, tzinfo=timezone.utc),
                )

                response = await client.post(
                    "/api/v1/jobs",
                    json={"playbook": "hello.yml"},
                )

        assert response.status_code == 202
        data = response.json()
        assert data["job_id"] == "test-123"
        assert data["status"] == "pending"

    async def test_submit_sync_job(self, client: AsyncClient):
        """Sync mode with ?sync=true."""
        response = await client.post(
            "/api/v1/jobs?sync=true",
            json={"playbook": "hello.yml"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert "Hello, World!" in data["stdout"]


class TestGetJob:
    async def test_get_job(self, client: AsyncClient):
        with patch("ansible_runner_service.main.get_job_store") as mock_store:
            mock_store.return_value.get_job.return_value = MagicMock(
                job_id="test-123",
                status=MagicMock(value="successful"),
                playbook="hello.yml",
                created_at=datetime(2026, 1, 21, 10, 0, 0, tzinfo=timezone.utc),
                started_at=datetime(2026, 1, 21, 10, 0, 1, tzinfo=timezone.utc),
                finished_at=datetime(2026, 1, 21, 10, 0, 5, tzinfo=timezone.utc),
                result=MagicMock(rc=0, stdout="Hello!", stats={}),
                error=None,
            )

            response = await client.get("/api/v1/jobs/test-123")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "test-123"
        assert data["status"] == "successful"

    async def test_get_job_not_found(self, client: AsyncClient):
        with patch("ansible_runner_service.main.get_job_store") as mock_store:
            mock_store.return_value.get_job.return_value = None

            response = await client.get("/api/v1/jobs/nonexistent")

        assert response.status_code == 404
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py::TestAsyncJobs -v`
Expected: FAIL

**Step 3: Update main.py**

```python
# src/ansible_runner_service/main.py
from pathlib import Path
from typing import Union

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from redis import Redis

from ansible_runner_service.job_store import JobStore
from ansible_runner_service.queue import enqueue_job
from ansible_runner_service.runner import run_playbook
from ansible_runner_service.schemas import (
    JobRequest,
    JobResponse,
    JobSubmitResponse,
    JobDetail,
    JobResultSchema,
)

app = FastAPI(title="Ansible Runner Service")

PLAYBOOKS_DIR = Path(__file__).parent.parent.parent / "playbooks"


def get_playbooks_dir() -> Path:
    return PLAYBOOKS_DIR


def get_redis() -> Redis:
    return Redis()


def get_job_store() -> JobStore:
    return JobStore(get_redis())


@app.post(
    "/api/v1/jobs",
    response_model=Union[JobSubmitResponse, JobResponse],
    status_code=202,
)
def submit_job(
    request: JobRequest,
    sync: bool = Query(default=False, description="Run synchronously"),
    playbooks_dir: Path = Depends(get_playbooks_dir),
    job_store: JobStore = Depends(get_job_store),
    redis: Redis = Depends(get_redis),
) -> Union[JobSubmitResponse, JobResponse]:
    """Submit a playbook job for execution."""
    # Block path traversal attempts
    if ".." in request.playbook or request.playbook.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid playbook name")

    playbook_path = playbooks_dir / request.playbook

    if not playbook_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Playbook not found: {request.playbook}"
        )

    if sync:
        # Synchronous execution
        result = run_playbook(
            playbook=request.playbook,
            extra_vars=request.extra_vars,
            inventory=request.inventory,
            playbooks_dir=playbooks_dir,
        )
        return JSONResponse(
            status_code=200,
            content=JobResponse(
                status=result.status,
                rc=result.rc,
                stdout=result.stdout,
                stats=result.stats,
            ).model_dump(),
        )

    # Async execution (default)
    job = job_store.create_job(
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
    )

    enqueue_job(
        job_id=job.job_id,
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
        redis=redis,
    )

    return JSONResponse(
        status_code=202,
        content=JobSubmitResponse(
            job_id=job.job_id,
            status=job.status.value,
            created_at=job.created_at.isoformat(),
        ).model_dump(),
    )


@app.get("/api/v1/jobs/{job_id}", response_model=JobDetail)
def get_job(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
) -> JobDetail:
    """Get job status and details."""
    job = job_store.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result = None
    if job.result:
        result = JobResultSchema(
            rc=job.result.rc,
            stdout=job.result.stdout,
            stats=job.result.stats,
        )

    return JobDetail(
        job_id=job.job_id,
        status=job.status.value,
        playbook=job.playbook,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        result=result,
        error=job.error,
    )
```

**Step 4: Run all tests**

Run: `pytest tests/test_api.py -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "feat: add async job submission and GET /jobs/{id} endpoint"
```

---

## Task 7: Integration Test with Real Redis

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration test**

```python
# tests/test_integration.py
"""Integration tests requiring Redis.

Run with: pytest tests/test_integration.py -v -m integration
Requires: docker-compose up -d
"""
import pytest
import time
from pathlib import Path

from redis import Redis
from httpx import AsyncClient, ASGITransport

from ansible_runner_service.main import app, get_playbooks_dir, get_redis, get_job_store
from ansible_runner_service.job_store import JobStore


pytestmark = pytest.mark.integration


@pytest.fixture
def redis():
    """Real Redis connection."""
    r = Redis()
    r.flushdb()  # Clean slate
    yield r
    r.flushdb()


@pytest.fixture
def job_store(redis):
    return JobStore(redis)


@pytest.fixture
def playbooks_dir(tmp_path: Path):
    playbook = tmp_path / "hello.yml"
    playbook.write_text("""
---
- name: Hello
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Greet
      ansible.builtin.debug:
        msg: "Hello, {{ name | default('World') }}!"
""")
    return tmp_path


@pytest.fixture
def client(playbooks_dir: Path, redis: Redis, job_store: JobStore):
    app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_job_store] = lambda: job_store
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


class TestAsyncFlow:
    async def test_submit_and_poll(self, client: AsyncClient, job_store: JobStore):
        """Submit job async, poll until complete."""
        # Submit
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "hello.yml"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        # Simulate worker execution (in real test, worker would run separately)
        from ansible_runner_service.worker import execute_job
        execute_job(
            job_id=job_id,
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
        )

        # Poll
        response = await client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert "Hello, World!" in data["result"]["stdout"]

    async def test_sync_mode(self, client: AsyncClient):
        """Sync mode bypasses queue."""
        response = await client.post(
            "/api/v1/jobs?sync=true",
            json={"playbook": "hello.yml"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert "Hello, World!" in data["stdout"]
```

**Step 2: Add pytest marker to pyproject.toml**

Add to `[tool.pytest.ini_options]`:
```toml
markers = [
    "integration: marks tests as integration tests (require Redis)",
]
```

**Step 3: Run integration tests**

Run: `docker-compose up -d && pytest tests/test_integration.py -v -m integration`
Expected: All integration tests pass

**Step 4: Commit**

```bash
git add tests/test_integration.py pyproject.toml
git commit -m "feat: add integration tests for async job flow"
```

---

## Task 8: Manual Verification

**Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass

**Step 2: Start services**

```bash
# Terminal 1
docker-compose up -d

# Terminal 2
source .venv/bin/activate
uvicorn ansible_runner_service.main:app --reload

# Terminal 3
source .venv/bin/activate
rq worker --url redis://localhost:6379
```

**Step 3: Test async flow**

```bash
# Submit async job
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"playbook": "hello.yml"}'

# Poll for result (use job_id from response)
curl http://localhost:8000/api/v1/jobs/{job_id}
```

**Step 4: Test sync flow**

```bash
curl -X POST "http://localhost:8000/api/v1/jobs?sync=true" \
  -H "Content-Type: application/json" \
  -d '{"playbook": "hello.yml"}'
```

**Step 5: Verify OpenAPI docs**

Visit: http://localhost:8000/docs
Expected: Both endpoints documented with sync parameter

---

## Summary

After completing all tasks:

- `docker-compose.yml` - Redis container
- `src/ansible_runner_service/job_store.py` - Redis-backed job state
- `src/ansible_runner_service/queue.py` - rq job enqueueing
- `src/ansible_runner_service/worker.py` - Worker execution
- Updated `main.py` - Async/sync endpoints
- Updated `schemas.py` - New response models
- `tests/test_integration.py` - Full flow tests

Total new/modified files: 10
Total commits: 8
