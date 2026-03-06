# Health Endpoint Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add health check endpoints (`/health/live`, `/health/ready`, `/health/details`) for Kubernetes probes and observability.

**Architecture:** Create a new `health.py` module with dependency check functions, add three routes to `main.py`, and add a `count_jobs_since` method to `repository.py` for metrics.

**Tech Stack:** FastAPI, Redis, SQLAlchemy, importlib.metadata

---

## Task 1: Add `/health/live` endpoint

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Test: `tests/test_health.py` (create)

**Step 1: Write the failing test**

Create `tests/test_health.py`:

```python
# tests/test_health.py
import pytest
from httpx import AsyncClient, ASGITransport

from ansible_runner_service.main import app


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestHealthLive:
    async def test_health_live_returns_ok(self, client: AsyncClient):
        response = await client.get("/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py::TestHealthLive::test_health_live_returns_ok -v`
Expected: FAIL with 404 (endpoint not found)

**Step 3: Write minimal implementation**

Add to `src/ansible_runner_service/main.py` (after the existing routes):

```python
@app.get("/health/live")
async def health_live():
    """Liveness probe - returns ok if process is running."""
    return {"status": "ok"}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_health.py::TestHealthLive::test_health_live_returns_ok -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_health.py src/ansible_runner_service/main.py
git commit -m "feat: add /health/live endpoint for liveness probe"
```

---

## Task 2: Add `/health/ready` endpoint (success case)

**Files:**
- Create: `src/ansible_runner_service/health.py`
- Modify: `src/ansible_runner_service/main.py`
- Test: `tests/test_health.py`

**Step 1: Write the failing test**

Add to `tests/test_health.py`:

```python
from unittest.mock import patch, MagicMock


class TestHealthReady:
    async def test_health_ready_success(self, client: AsyncClient):
        """Returns 200 when Redis and MariaDB are reachable."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        with patch("ansible_runner_service.main.get_redis", return_value=mock_redis):
            with patch("ansible_runner_service.health.check_mariadb", return_value=(True, 5)):
                response = await client.get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py::TestHealthReady::test_health_ready_success -v`
Expected: FAIL (endpoint not found or import error)

**Step 3: Write minimal implementation**

Create `src/ansible_runner_service/health.py`:

```python
# src/ansible_runner_service/health.py
import time
from sqlalchemy import text
from sqlalchemy.orm import Session


def check_redis(redis_client) -> tuple[bool, int]:
    """Check Redis connectivity. Returns (is_ok, latency_ms)."""
    try:
        start = time.perf_counter()
        redis_client.ping()
        latency_ms = int((time.perf_counter() - start) * 1000)
        return True, latency_ms
    except Exception:
        return False, 0


def check_mariadb(session: Session) -> tuple[bool, int]:
    """Check MariaDB connectivity. Returns (is_ok, latency_ms)."""
    try:
        start = time.perf_counter()
        session.execute(text("SELECT 1"))
        latency_ms = int((time.perf_counter() - start) * 1000)
        return True, latency_ms
    except Exception:
        return False, 0
```

Add to `src/ansible_runner_service/main.py`:

```python
from ansible_runner_service.health import check_redis, check_mariadb

@app.get("/health/ready")
async def health_ready(
    redis: Redis = Depends(get_redis),
    session: Session = Depends(get_db_session),
):
    """Readiness probe - returns ok if Redis and MariaDB are reachable."""
    redis_ok, _ = check_redis(redis)
    mariadb_ok, _ = check_mariadb(session)

    if redis_ok and mariadb_ok:
        return {"status": "ok"}

    reasons = []
    if not redis_ok:
        reasons.append("redis unreachable")
    if not mariadb_ok:
        reasons.append("mariadb unreachable")

    return JSONResponse(
        status_code=503,
        content={"status": "error", "reason": ", ".join(reasons)}
    )
```

Also add `get_db_session` dependency to `main.py`:

```python
from sqlalchemy.orm import Session

def get_db_session():
    """Get a database session for health checks."""
    engine = get_engine_singleton()
    with Session(engine) as session:
        yield session
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_health.py::TestHealthReady::test_health_ready_success -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/health.py src/ansible_runner_service/main.py tests/test_health.py
git commit -m "feat: add /health/ready endpoint with Redis and MariaDB checks"
```

---

## Task 3: Add `/health/ready` failure tests

**Files:**
- Test: `tests/test_health.py`

**Step 1: Write the failing tests**

Add to `tests/test_health.py` in `TestHealthReady` class:

```python
    async def test_health_ready_redis_down(self, client: AsyncClient):
        """Returns 503 when Redis is unreachable."""
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = Exception("Connection refused")

        with patch("ansible_runner_service.main.get_redis", return_value=mock_redis):
            with patch("ansible_runner_service.health.check_mariadb", return_value=(True, 5)):
                response = await client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "error"
        assert "redis" in data["reason"]

    async def test_health_ready_mariadb_down(self, client: AsyncClient):
        """Returns 503 when MariaDB is unreachable."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        with patch("ansible_runner_service.main.get_redis", return_value=mock_redis):
            with patch("ansible_runner_service.health.check_mariadb", return_value=(False, 0)):
                response = await client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "error"
        assert "mariadb" in data["reason"]
```

**Step 2: Run tests to verify they pass**

Run: `pytest tests/test_health.py::TestHealthReady -v`
Expected: All 3 tests PASS (implementation already handles failures)

**Step 3: Commit**

```bash
git add tests/test_health.py
git commit -m "test: add failure case tests for /health/ready"
```

---

## Task 4: Add `count_jobs_since` to repository

**Files:**
- Modify: `src/ansible_runner_service/repository.py`
- Test: `tests/test_repository.py`

**Step 1: Write the failing test**

Add to `tests/test_repository.py`:

```python
class TestCountJobsSince:
    def test_count_jobs_since(self, session: Session):
        """Count jobs created since a given time."""
        repo = JobRepository(session)
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        two_hours_ago = now - timedelta(hours=2)

        # Create jobs at different times
        repo.create("job-old", "test.yml", {}, "localhost,", two_hours_ago - timedelta(minutes=30))
        repo.create("job-recent-1", "test.yml", {}, "localhost,", one_hour_ago + timedelta(minutes=10))
        repo.create("job-recent-2", "test.yml", {}, "localhost,", one_hour_ago + timedelta(minutes=20))

        count = repo.count_jobs_since(one_hour_ago)

        assert count == 2
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_repository.py::TestCountJobsSince -v`
Expected: FAIL with AttributeError (method not found)

**Step 3: Write minimal implementation**

Add to `src/ansible_runner_service/repository.py`:

```python
    def count_jobs_since(self, since: datetime) -> int:
        """Count jobs created since a given time."""
        from sqlalchemy import func
        return self.session.query(func.count(JobModel.id)).filter(
            JobModel.created_at >= since
        ).scalar() or 0
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_repository.py::TestCountJobsSince -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/repository.py tests/test_repository.py
git commit -m "feat: add count_jobs_since method to repository"
```

---

## Task 5: Add worker info and version functions to health.py

**Files:**
- Modify: `src/ansible_runner_service/health.py`
- Test: `tests/test_health.py`

**Step 1: Write the failing tests**

Add to `tests/test_health.py`:

```python
from ansible_runner_service.health import get_worker_info, get_version_info


class TestHealthHelpers:
    def test_get_worker_info(self):
        """Get worker count and queues from Redis."""
        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {b"rq:worker:worker1", b"rq:worker:worker2"}
        mock_redis.keys.return_value = [b"rq:queue:default", b"rq:queue:high"]

        info = get_worker_info(mock_redis)

        assert info["count"] == 2
        assert "default" in info["queues"]
        assert "high" in info["queues"]

    def test_get_version_info(self):
        """Get app and ansible versions."""
        info = get_version_info()

        assert "app" in info
        assert "ansible_core" in info
        assert "python" in info
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_health.py::TestHealthHelpers -v`
Expected: FAIL with ImportError (functions not found)

**Step 3: Write minimal implementation**

Add to `src/ansible_runner_service/health.py`:

```python
import platform
import subprocess
import importlib.metadata


def get_worker_info(redis_client) -> dict:
    """Get RQ worker info from Redis."""
    try:
        workers = redis_client.smembers("rq:workers")
        worker_count = len(workers) if workers else 0

        queue_keys = redis_client.keys("rq:queue:*")
        queues = [k.decode().replace("rq:queue:", "") for k in queue_keys] if queue_keys else []

        return {"count": worker_count, "queues": sorted(queues)}
    except Exception:
        return {"count": 0, "queues": []}


def get_version_info() -> dict:
    """Get version information."""
    try:
        app_version = importlib.metadata.version("ansible-runner-service")
    except importlib.metadata.PackageNotFoundError:
        app_version = "unknown"

    try:
        result = subprocess.run(
            ["ansible", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        first_line = result.stdout.split("\n")[0]
        # Parse "ansible [core 2.20.2]"
        ansible_version = first_line.split("[core ")[1].rstrip("]") if "[core " in first_line else "unknown"
    except Exception:
        ansible_version = "unknown"

    return {
        "app": app_version,
        "ansible_core": ansible_version,
        "python": platform.python_version()
    }
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_health.py::TestHealthHelpers -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/health.py tests/test_health.py
git commit -m "feat: add get_worker_info and get_version_info helpers"
```

---

## Task 6: Add `/health/details` endpoint

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Test: `tests/test_health.py`

**Step 1: Write the failing test**

Add to `tests/test_health.py`:

```python
from datetime import datetime, timezone, timedelta


class TestHealthDetails:
    async def test_health_details_structure(self, client: AsyncClient):
        """Returns full health details with correct structure."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        mock_redis.smembers.return_value = {b"rq:worker:worker1"}
        mock_redis.keys.return_value = [b"rq:queue:default"]
        mock_redis.llen.return_value = 5

        with patch("ansible_runner_service.main.get_redis", return_value=mock_redis):
            with patch("ansible_runner_service.health.check_mariadb", return_value=(True, 3)):
                with patch("ansible_runner_service.main.get_db_session"):
                    with patch("ansible_runner_service.health.get_jobs_last_hour", return_value=42):
                        response = await client.get("/health/details")

        assert response.status_code == 200
        data = response.json()

        # Check structure
        assert data["status"] == "ok"
        assert "dependencies" in data
        assert "redis" in data["dependencies"]
        assert "mariadb" in data["dependencies"]
        assert "workers" in data
        assert "metrics" in data
        assert "version" in data

        # Check dependency structure
        assert data["dependencies"]["redis"]["status"] == "ok"
        assert "latency_ms" in data["dependencies"]["redis"]

        # Check workers structure
        assert "count" in data["workers"]
        assert "queues" in data["workers"]

        # Check metrics structure
        assert "queue_depth" in data["metrics"]
        assert "jobs_last_hour" in data["metrics"]

        # Check version structure
        assert "app" in data["version"]
        assert "ansible_core" in data["version"]
        assert "python" in data["version"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py::TestHealthDetails::test_health_details_structure -v`
Expected: FAIL (endpoint not found)

**Step 3: Write minimal implementation**

Add helper function to `src/ansible_runner_service/health.py`:

```python
def get_queue_depth(redis_client) -> int:
    """Get total number of jobs in all queues."""
    try:
        queue_keys = redis_client.keys("rq:queue:*")
        total = 0
        for key in queue_keys or []:
            total += redis_client.llen(key)
        return total
    except Exception:
        return 0


def get_jobs_last_hour(session: Session) -> int:
    """Get count of jobs created in the last hour."""
    from ansible_runner_service.repository import JobRepository
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    repo = JobRepository(session)
    return repo.count_jobs_since(one_hour_ago)
```

Add to `src/ansible_runner_service/main.py`:

```python
from ansible_runner_service.health import (
    check_redis,
    check_mariadb,
    get_worker_info,
    get_version_info,
    get_queue_depth,
    get_jobs_last_hour,
)

@app.get("/health/details")
async def health_details(
    redis: Redis = Depends(get_redis),
    session: Session = Depends(get_db_session),
):
    """Full health details for debugging and observability."""
    redis_ok, redis_latency = check_redis(redis)
    mariadb_ok, mariadb_latency = check_mariadb(session)

    overall_status = "ok" if (redis_ok and mariadb_ok) else "error"

    return {
        "status": overall_status,
        "dependencies": {
            "redis": {
                "status": "ok" if redis_ok else "error",
                "latency_ms": redis_latency
            },
            "mariadb": {
                "status": "ok" if mariadb_ok else "error",
                "latency_ms": mariadb_latency
            }
        },
        "workers": get_worker_info(redis),
        "metrics": {
            "queue_depth": get_queue_depth(redis),
            "jobs_last_hour": get_jobs_last_hour(session) if mariadb_ok else 0
        },
        "version": get_version_info()
    }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_health.py::TestHealthDetails -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/health.py src/ansible_runner_service/main.py tests/test_health.py
git commit -m "feat: add /health/details endpoint with full observability"
```

---

## Task 7: Run full test suite and update documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/usage-guide.md`

**Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

**Step 2: Update README.md**

Add to Features section:
```markdown
- **Health endpoints** - `/health/live`, `/health/ready`, `/health/details` for Kubernetes probes
```

**Step 3: Update docs/usage-guide.md**

Add new section after "API Usage":
```markdown
### Health Endpoints

#### Liveness probe

```bash
curl http://localhost:8000/health/live
```

Response:
```json
{"status": "ok"}
```

#### Readiness probe

```bash
curl http://localhost:8000/health/ready
```

Response (success):
```json
{"status": "ok"}
```

Response (failure - 503):
```json
{"status": "error", "reason": "mariadb unreachable"}
```

#### Detailed health status

```bash
curl http://localhost:8000/health/details
```

Response:
```json
{
  "status": "ok",
  "dependencies": {
    "redis": {"status": "ok", "latency_ms": 2},
    "mariadb": {"status": "ok", "latency_ms": 5}
  },
  "workers": {
    "count": 3,
    "queues": ["default"]
  },
  "metrics": {
    "queue_depth": 12,
    "jobs_last_hour": 47
  },
  "version": {
    "app": "0.1.0",
    "ansible_core": "2.20.2",
    "python": "3.11.5"
  }
}
```
```

**Step 4: Commit**

```bash
git add README.md docs/usage-guide.md
git commit -m "docs: add health endpoints to README and usage guide"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | `/health/live` endpoint | main.py, test_health.py |
| 2 | `/health/ready` success case | health.py, main.py, test_health.py |
| 3 | `/health/ready` failure tests | test_health.py |
| 4 | `count_jobs_since` repository method | repository.py, test_repository.py |
| 5 | Worker info and version helpers | health.py, test_health.py |
| 6 | `/health/details` endpoint | health.py, main.py, test_health.py |
| 7 | Documentation updates | README.md, usage-guide.md |
