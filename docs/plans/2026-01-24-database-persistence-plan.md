# Database Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add MariaDB for durable job history while keeping Redis for fast live state (write-through pattern).

**Architecture:** SQLAlchemy ORM with Alembic migrations. Worker writes to both Redis and MariaDB. GET /jobs/{id} tries Redis first, falls back to DB. GET /jobs (list) always queries DB.

**Tech Stack:** SQLAlchemy 2.0, Alembic, mysqlclient, MariaDB 11

**Design Reference:** `docs/plans/2026-01-24-database-persistence-design.md`

---

## Task 1: Add Dependencies and Docker Setup

**Files:**
- Modify: `pyproject.toml:6-12`
- Modify: `docker-compose.yml`

**Step 1: Update pyproject.toml**

Add database dependencies:

```toml
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn>=0.27.0",
    "ansible-runner>=2.3.0",
    "rq>=1.16.0",
    "redis>=5.0.0",
    "sqlalchemy>=2.0.0",
    "alembic>=1.13.0",
    "mysqlclient>=2.2.0",
]
```

**Step 2: Update docker-compose.yml**

Add MariaDB service:

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

  mariadb:
    image: mariadb:11
    ports:
      - "3306:3306"
    environment:
      MYSQL_ROOT_PASSWORD: devpassword
      MYSQL_DATABASE: ansible_runner
    volumes:
      - mariadb_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mariadb-admin", "ping", "-h", "localhost"]
      interval: 5s
      timeout: 3s
      retries: 3

volumes:
  mariadb_data:
```

**Step 3: Install dependencies and start containers**

Run: `pip install -e ".[dev]"`
Run: `docker-compose up -d`
Run: `docker-compose ps`
Expected: Both redis and mariadb healthy

**Step 4: Commit**

```bash
git add pyproject.toml docker-compose.yml
git commit -m "feat: add MariaDB to docker-compose and SQLAlchemy deps"
```

---

## Task 2: Create Database Engine and Session Management

**Files:**
- Create: `src/ansible_runner_service/database.py`
- Create: `tests/test_database.py`

**Step 1: Write the failing test**

```python
# tests/test_database.py
import pytest
from unittest.mock import patch, MagicMock


class TestGetEngine:
    def test_creates_engine_with_url(self):
        from ansible_runner_service.database import get_engine

        with patch("ansible_runner_service.database.create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            engine = get_engine("mysql://user:pass@localhost/db")

            mock_create.assert_called_once_with(
                "mysql://user:pass@localhost/db",
                pool_pre_ping=True,
            )
            assert engine == mock_engine


class TestGetSession:
    def test_creates_session(self):
        from ansible_runner_service.database import get_session, get_engine

        with patch("ansible_runner_service.database.create_engine"):
            engine = get_engine("mysql://user:pass@localhost/db")
            session = get_session(engine)

            # Session should be a sessionmaker instance
            assert callable(session)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_database.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write minimal implementation**

```python
# src/ansible_runner_service/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session


def get_database_url() -> str:
    """Get database URL from environment."""
    return os.getenv(
        "DATABASE_URL",
        "mysql://root:devpassword@localhost:3306/ansible_runner"
    )


def get_engine(url: str | None = None) -> Engine:
    """Create SQLAlchemy engine."""
    db_url = url or get_database_url()
    return create_engine(db_url, pool_pre_ping=True)


def get_session(engine: Engine) -> sessionmaker[Session]:
    """Create session factory."""
    return sessionmaker(bind=engine, expire_on_commit=False)
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_database.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/database.py tests/test_database.py
git commit -m "feat: add database engine and session management"
```

---

## Task 3: Create JobModel ORM Class

**Files:**
- Create: `src/ansible_runner_service/models.py`
- Create: `tests/test_models.py`

**Step 1: Write the failing test**

```python
# tests/test_models.py
from datetime import datetime, timezone


class TestJobModel:
    def test_model_attributes(self):
        from ansible_runner_service.models import JobModel

        job = JobModel(
            id="test-123",
            status="pending",
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )

        assert job.id == "test-123"
        assert job.status == "pending"
        assert job.playbook == "hello.yml"
        assert job.extra_vars == {"name": "World"}
        assert job.inventory == "localhost,"
        assert job.started_at is None
        assert job.finished_at is None
        assert job.result_rc is None
        assert job.result_stdout is None
        assert job.result_stats is None
        assert job.error is None

    def test_model_tablename(self):
        from ansible_runner_service.models import JobModel

        assert JobModel.__tablename__ == "jobs"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write minimal implementation**

```python
# src/ansible_runner_service/models.py
from datetime import datetime
from typing import Any

from sqlalchemy import String, Integer, Text, DateTime, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class JobModel(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    playbook: Mapped[str] = mapped_column(String(255), nullable=False)
    extra_vars: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    inventory: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_rc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/models.py tests/test_models.py
git commit -m "feat: add JobModel ORM class"
```

---

## Task 4: Set Up Alembic Migrations

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/` (directory)
- Create: `alembic/script.py.mako`

**Step 1: Initialize Alembic**

Run: `.venv/bin/alembic init alembic`
Expected: Creates alembic/ directory and alembic.ini

**Step 2: Configure alembic.ini**

Edit `alembic.ini` line 63 (sqlalchemy.url):
```ini
sqlalchemy.url = mysql://root:devpassword@localhost:3306/ansible_runner
```

**Step 3: Configure alembic/env.py**

Replace target_metadata in `alembic/env.py`:

```python
# After "from alembic import context" add:
from ansible_runner_service.models import Base

# Change target_metadata line:
target_metadata = Base.metadata
```

**Step 4: Create initial migration**

Run: `.venv/bin/alembic revision --autogenerate -m "create jobs table"`
Expected: Creates migration file in alembic/versions/

**Step 5: Verify migration file contains correct schema**

Check migration file includes:
- `CREATE TABLE jobs`
- All columns from JobModel
- Indexes on status and created_at

**Step 6: Run migration**

Run: `.venv/bin/alembic upgrade head`
Expected: "Running upgrade  -> xxxx, create jobs table"

**Step 7: Verify table exists**

Run: `docker exec -it $(docker ps -qf "ancestor=mariadb:11") mariadb -uroot -pdevpassword -e "DESCRIBE ansible_runner.jobs;"`
Expected: Shows all columns

**Step 8: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: add Alembic migrations with jobs table"
```

---

## Task 5: Create Job Repository (DB Layer)

**Files:**
- Create: `src/ansible_runner_service/repository.py`
- Create: `tests/test_repository.py`

**Step 1: Write the failing test**

```python
# tests/test_repository.py
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


class TestJobRepository:
    def test_create_job(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        repo = JobRepository(mock_session)

        job = repo.create(
            job_id="test-123",
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="localhost,",
            created_at=datetime(2026, 1, 24, 10, 0, 0, tzinfo=timezone.utc),
        )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

        added_job = mock_session.add.call_args[0][0]
        assert added_job.id == "test-123"
        assert added_job.status == "pending"
        assert added_job.playbook == "hello.yml"

    def test_get_job_found(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        mock_job = JobModel(
            id="test-123",
            status="pending",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )
        mock_session.get.return_value = mock_job

        repo = JobRepository(mock_session)
        job = repo.get("test-123")

        assert job == mock_job
        mock_session.get.assert_called_once_with(JobModel, "test-123")

    def test_get_job_not_found(self):
        from ansible_runner_service.repository import JobRepository

        mock_session = MagicMock()
        mock_session.get.return_value = None

        repo = JobRepository(mock_session)
        job = repo.get("nonexistent")

        assert job is None

    def test_update_status(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        mock_job = JobModel(
            id="test-123",
            status="pending",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )
        mock_session.get.return_value = mock_job

        repo = JobRepository(mock_session)
        now = datetime.now(timezone.utc)
        repo.update_status("test-123", "running", started_at=now)

        assert mock_job.status == "running"
        assert mock_job.started_at == now
        mock_session.commit.assert_called_once()

    def test_list_jobs(self):
        from ansible_runner_service.repository import JobRepository
        from ansible_runner_service.models import JobModel

        mock_session = MagicMock()
        mock_jobs = [
            JobModel(
                id="job-1",
                status="successful",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
                created_at=datetime.now(timezone.utc),
            ),
            JobModel(
                id="job-2",
                status="failed",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
                created_at=datetime.now(timezone.utc),
            ),
        ]

        # Mock the query chain
        mock_query = MagicMock()
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = mock_jobs
        mock_session.query.return_value = mock_query

        # Mock count
        mock_count_query = MagicMock()
        mock_count_query.scalar.return_value = 2
        mock_session.query.return_value.count = MagicMock(return_value=mock_count_query)

        repo = JobRepository(mock_session)
        jobs, total = repo.list_jobs(limit=20, offset=0)

        assert len(jobs) == 2
        assert total == 2

    def test_list_jobs_with_status_filter(self):
        from ansible_runner_service.repository import JobRepository

        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        mock_session.query.return_value = mock_query

        mock_count_query = MagicMock()
        mock_count_query.scalar.return_value = 0
        mock_query.count = MagicMock(return_value=mock_count_query)

        repo = JobRepository(mock_session)
        repo.list_jobs(status="failed", limit=20, offset=0)

        # Verify filter was called (status filter applied)
        mock_query.filter.assert_called()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_repository.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write minimal implementation**

```python
# src/ansible_runner_service/repository.py
from datetime import datetime
from typing import Any

from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from ansible_runner_service.models import JobModel


class JobRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        job_id: str,
        playbook: str,
        extra_vars: dict[str, Any],
        inventory: str,
        created_at: datetime,
    ) -> JobModel:
        """Create a new job record."""
        job = JobModel(
            id=job_id,
            status="pending",
            playbook=playbook,
            extra_vars=extra_vars,
            inventory=inventory,
            created_at=created_at,
        )
        self.session.add(job)
        self.session.commit()
        return job

    def get(self, job_id: str) -> JobModel | None:
        """Get a job by ID."""
        return self.session.get(JobModel, job_id)

    def update_status(
        self,
        job_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        result_rc: int | None = None,
        result_stdout: str | None = None,
        result_stats: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Update job status and related fields."""
        job = self.get(job_id)
        if job is None:
            return

        job.status = status
        if started_at is not None:
            job.started_at = started_at
        if finished_at is not None:
            job.finished_at = finished_at
        if result_rc is not None:
            job.result_rc = result_rc
        if result_stdout is not None:
            job.result_stdout = result_stdout
        if result_stats is not None:
            job.result_stats = result_stats
        if error is not None:
            job.error = error

        self.session.commit()

    def list_jobs(
        self,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JobModel], int]:
        """List jobs with optional filtering and pagination."""
        query = self.session.query(JobModel)

        if status:
            query = query.filter(JobModel.status == status)

        # Get total count before pagination
        total = query.count()

        # Apply ordering and pagination
        jobs = (
            query
            .order_by(desc(JobModel.created_at))
            .offset(offset)
            .limit(limit)
            .all()
        )

        return jobs, total
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_repository.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/repository.py tests/test_repository.py
git commit -m "feat: add JobRepository for database operations"
```

---

## Task 6: Add DB Writes to JobStore (Write-Through)

**Files:**
- Modify: `src/ansible_runner_service/job_store.py`
- Modify: `tests/test_job_store.py`

**Step 1: Write the failing test**

Add to `tests/test_job_store.py`:

```python
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
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_job_store.py::TestJobStoreWithDB -v`
Expected: FAIL (repository parameter not supported)

**Step 3: Update job_store.py to accept repository**

Modify `JobStore.__init__` to accept optional repository:

```python
class JobStore:
    def __init__(
        self,
        redis: Redis,
        ttl: int = 86400,
        repository: "JobRepository | None" = None,
    ):
        self.redis = redis
        self.ttl = ttl
        self.repository = repository
```

Modify `create_job` to write to DB:

```python
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

    # Write-through to DB
    if self.repository:
        self.repository.create(
            job_id=job.job_id,
            playbook=playbook,
            extra_vars=extra_vars,
            inventory=inventory,
            created_at=job.created_at,
        )

    return job
```

Modify `update_status` to write to DB:

```python
def update_status(
    self,
    job_id: str,
    status: JobStatus,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    result: JobResult | None = None,
    error: str | None = None,
) -> None:
    # Existing Redis update
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

    # Write-through to DB
    if self.repository:
        self.repository.update_status(
            job_id,
            status.value,
            started_at=started_at,
            finished_at=finished_at,
            result_rc=result.rc if result else None,
            result_stdout=result.stdout if result else None,
            result_stats=result.stats if result else None,
            error=error,
        )
```

Add import at top of job_store.py:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ansible_runner_service.repository import JobRepository
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_job_store.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/job_store.py tests/test_job_store.py
git commit -m "feat: add write-through to database in JobStore"
```

---

## Task 7: Add Schemas for Job List Endpoint

**Files:**
- Modify: `src/ansible_runner_service/schemas.py`
- Modify: `tests/test_schemas.py`

**Step 1: Write the failing test**

Add to `tests/test_schemas.py`:

```python
class TestJobSummary:
    def test_create_summary(self):
        from ansible_runner_service.schemas import JobSummary

        summary = JobSummary(
            job_id="test-123",
            status="successful",
            playbook="hello.yml",
            created_at="2026-01-24T10:00:00Z",
            finished_at="2026-01-24T10:00:05Z",
        )

        assert summary.job_id == "test-123"
        assert summary.status == "successful"
        assert summary.playbook == "hello.yml"
        assert summary.finished_at == "2026-01-24T10:00:05Z"


class TestJobListResponse:
    def test_create_response(self):
        from ansible_runner_service.schemas import JobListResponse, JobSummary

        response = JobListResponse(
            jobs=[
                JobSummary(
                    job_id="test-123",
                    status="successful",
                    playbook="hello.yml",
                    created_at="2026-01-24T10:00:00Z",
                    finished_at="2026-01-24T10:00:05Z",
                )
            ],
            total=42,
            limit=20,
            offset=0,
        )

        assert len(response.jobs) == 1
        assert response.total == 42
        assert response.limit == 20
        assert response.offset == 0
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_schemas.py::TestJobSummary -v`
Expected: FAIL with ImportError

**Step 3: Add new schemas**

Add to `src/ansible_runner_service/schemas.py`:

```python
class JobSummary(BaseModel):
    """Job summary for list endpoint."""
    job_id: str
    status: str
    playbook: str
    created_at: str
    finished_at: str | None = None


class JobListResponse(BaseModel):
    """Response for GET /jobs list endpoint."""
    jobs: list[JobSummary]
    total: int
    limit: int
    offset: int
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_schemas.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "feat: add JobSummary and JobListResponse schemas"
```

---

## Task 8: Add GET /jobs List Endpoint

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Modify: `tests/test_api.py`

**Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
class TestListJobs:
    def test_list_jobs_empty(self, client, mock_job_store, mock_redis):
        from unittest.mock import MagicMock, patch

        mock_repo = MagicMock()
        mock_repo.list_jobs.return_value = ([], 0)

        with patch("ansible_runner_service.main.get_repository", return_value=mock_repo):
            response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert data["jobs"] == []
        assert data["total"] == 0
        assert data["limit"] == 20
        assert data["offset"] == 0

    def test_list_jobs_with_results(self, client, mock_job_store, mock_redis):
        from unittest.mock import MagicMock, patch
        from ansible_runner_service.models import JobModel
        from datetime import datetime, timezone

        mock_job = JobModel(
            id="test-123",
            status="successful",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime(2026, 1, 24, 10, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 24, 10, 0, 5, tzinfo=timezone.utc),
        )
        mock_repo = MagicMock()
        mock_repo.list_jobs.return_value = ([mock_job], 1)

        with patch("ansible_runner_service.main.get_repository", return_value=mock_repo):
            response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["job_id"] == "test-123"
        assert data["jobs"][0]["status"] == "successful"
        assert data["total"] == 1

    def test_list_jobs_with_status_filter(self, client, mock_job_store, mock_redis):
        from unittest.mock import MagicMock, patch

        mock_repo = MagicMock()
        mock_repo.list_jobs.return_value = ([], 0)

        with patch("ansible_runner_service.main.get_repository", return_value=mock_repo):
            response = client.get("/api/v1/jobs?status=failed")

        assert response.status_code == 200
        mock_repo.list_jobs.assert_called_once_with(
            status="failed",
            limit=20,
            offset=0,
        )

    def test_list_jobs_with_pagination(self, client, mock_job_store, mock_redis):
        from unittest.mock import MagicMock, patch

        mock_repo = MagicMock()
        mock_repo.list_jobs.return_value = ([], 0)

        with patch("ansible_runner_service.main.get_repository", return_value=mock_repo):
            response = client.get("/api/v1/jobs?limit=10&offset=20")

        assert response.status_code == 200
        mock_repo.list_jobs.assert_called_once_with(
            status=None,
            limit=10,
            offset=20,
        )

    def test_list_jobs_limit_max_100(self, client, mock_job_store, mock_redis):
        from unittest.mock import MagicMock, patch

        mock_repo = MagicMock()
        mock_repo.list_jobs.return_value = ([], 0)

        with patch("ansible_runner_service.main.get_repository", return_value=mock_repo):
            response = client.get("/api/v1/jobs?limit=200")

        assert response.status_code == 200
        # Should cap at 100
        mock_repo.list_jobs.assert_called_once_with(
            status=None,
            limit=100,
            offset=0,
        )
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py::TestListJobs -v`
Expected: FAIL (endpoint doesn't exist)

**Step 3: Add endpoint and repository dependency**

Add imports to main.py:
```python
from ansible_runner_service.repository import JobRepository
from ansible_runner_service.database import get_engine, get_session
from ansible_runner_service.schemas import JobSummary, JobListResponse
```

Add repository dependency:
```python
def get_repository() -> JobRepository:
    engine = get_engine()
    Session = get_session(engine)
    return JobRepository(Session())
```

Add endpoint:
```python
@app.get("/api/v1/jobs", response_model=JobListResponse)
def list_jobs(
    status: str | None = Query(default=None, description="Filter by status"),
    limit: int = Query(default=20, ge=1, le=100, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    repository: JobRepository = Depends(get_repository),
) -> JobListResponse:
    """List jobs with optional filtering and pagination."""
    # Cap limit at 100
    limit = min(limit, 100)

    jobs, total = repository.list_jobs(
        status=status,
        limit=limit,
        offset=offset,
    )

    job_summaries = [
        JobSummary(
            job_id=job.id,
            status=job.status,
            playbook=job.playbook,
            created_at=job.created_at.isoformat(),
            finished_at=job.finished_at.isoformat() if job.finished_at else None,
        )
        for job in jobs
    ]

    return JobListResponse(
        jobs=job_summaries,
        total=total,
        limit=limit,
        offset=offset,
    )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api.py::TestListJobs -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "feat: add GET /jobs list endpoint with filtering and pagination"
```

---

## Task 9: Update GET /jobs/{id} with DB Fallback

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Modify: `tests/test_api.py`

**Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
class TestGetJobWithDBFallback:
    def test_get_job_from_redis(self, client, mock_redis):
        """Job found in Redis, no DB lookup needed."""
        from unittest.mock import MagicMock, patch
        from ansible_runner_service.job_store import Job, JobStatus
        from datetime import datetime, timezone

        mock_job = Job(
            job_id="test-123",
            status=JobStatus.SUCCESSFUL,
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )

        mock_store = MagicMock()
        mock_store.get_job.return_value = mock_job

        with patch("ansible_runner_service.main.get_job_store", return_value=mock_store):
            response = client.get("/api/v1/jobs/test-123")

        assert response.status_code == 200
        # DB should NOT be queried when Redis has the job
        # (We'd verify this with a mock if needed)

    def test_get_job_fallback_to_db(self, client, mock_redis):
        """Job not in Redis, found in DB."""
        from unittest.mock import MagicMock, patch
        from ansible_runner_service.models import JobModel
        from datetime import datetime, timezone

        mock_store = MagicMock()
        mock_store.get_job.return_value = None  # Not in Redis

        mock_db_job = JobModel(
            id="test-123",
            status="successful",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime(2026, 1, 24, 10, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 24, 10, 0, 5, tzinfo=timezone.utc),
            result_rc=0,
            result_stdout="PLAY [Hello]...",
            result_stats={"localhost": {"ok": 1}},
        )

        mock_repo = MagicMock()
        mock_repo.get.return_value = mock_db_job

        with patch("ansible_runner_service.main.get_job_store", return_value=mock_store):
            with patch("ansible_runner_service.main.get_repository", return_value=mock_repo):
                response = client.get("/api/v1/jobs/test-123")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "test-123"
        assert data["status"] == "successful"

    def test_get_job_not_in_redis_or_db(self, client, mock_redis):
        """Job not found anywhere."""
        from unittest.mock import MagicMock, patch

        mock_store = MagicMock()
        mock_store.get_job.return_value = None

        mock_repo = MagicMock()
        mock_repo.get.return_value = None

        with patch("ansible_runner_service.main.get_job_store", return_value=mock_store):
            with patch("ansible_runner_service.main.get_repository", return_value=mock_repo):
                response = client.get("/api/v1/jobs/test-123")

        assert response.status_code == 404
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py::TestGetJobWithDBFallback -v`
Expected: FAIL (no DB fallback)

**Step 3: Update get_job endpoint**

Modify `get_job` in main.py:

```python
@app.get("/api/v1/jobs/{job_id}", response_model=JobDetail)
def get_job(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
    repository: JobRepository = Depends(get_repository),
) -> JobDetail:
    """Get job status and details."""
    # Try Redis first (fast for active jobs)
    job = job_store.get_job(job_id)

    if job is not None:
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

    # Fallback to DB (for completed jobs after TTL)
    db_job = repository.get(job_id)

    if db_job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result = None
    if db_job.result_rc is not None:
        result = JobResultSchema(
            rc=db_job.result_rc,
            stdout=db_job.result_stdout or "",
            stats=db_job.result_stats or {},
        )

    return JobDetail(
        job_id=db_job.id,
        status=db_job.status,
        playbook=db_job.playbook,
        created_at=db_job.created_at.isoformat(),
        started_at=db_job.started_at.isoformat() if db_job.started_at else None,
        finished_at=db_job.finished_at.isoformat() if db_job.finished_at else None,
        result=result,
        error=db_job.error,
    )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api.py::TestGetJobWithDBFallback -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "feat: add DB fallback to GET /jobs/{id} endpoint"
```

---

## Task 10: Wire Up Repository in Worker

**Files:**
- Modify: `src/ansible_runner_service/worker.py`
- Modify: `tests/test_worker.py`

**Step 1: Write the failing test**

Add to `tests/test_worker.py`:

```python
class TestExecuteJobWithDB:
    def test_writes_to_db(self, mock_redis):
        from unittest.mock import MagicMock, patch
        from ansible_runner_service.worker import execute_job

        mock_store = MagicMock()
        mock_repo = MagicMock()

        mock_result = MagicMock()
        mock_result.rc = 0
        mock_result.stdout = "PLAY [Hello]..."
        mock_result.stats = {"localhost": {"ok": 1}}

        with patch("ansible_runner_service.worker.get_job_store") as mock_get_store:
            with patch("ansible_runner_service.worker.get_repository", return_value=mock_repo):
                with patch("ansible_runner_service.worker.run_playbook", return_value=mock_result):
                    # Configure store to use repo
                    mock_store_instance = MagicMock()
                    mock_get_store.return_value = mock_store_instance

                    execute_job(
                        job_id="test-123",
                        playbook="hello.yml",
                        extra_vars={},
                        inventory="localhost,",
                    )

        # Verify update_status was called (which writes to both Redis and DB)
        assert mock_store_instance.update_status.call_count == 2  # running, then result
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_worker.py::TestExecuteJobWithDB -v`
Expected: FAIL (get_repository not found)

**Step 3: Update worker.py**

Add imports:
```python
from ansible_runner_service.repository import JobRepository
from ansible_runner_service.database import get_engine, get_session
```

Add get_repository function:
```python
def get_repository() -> JobRepository:
    engine = get_engine()
    Session = get_session(engine)
    return JobRepository(Session())
```

Update get_job_store to accept repository:
```python
def get_job_store() -> JobStore:
    return JobStore(get_redis(), repository=get_repository())
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_worker.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/worker.py tests/test_worker.py
git commit -m "feat: wire up repository in worker for DB writes"
```

---

## Task 11: Wire Up Repository in API

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Modify: `tests/test_api.py`

**Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
class TestSubmitJobWithDB:
    def test_submit_async_writes_to_db(self, client, mock_redis):
        from unittest.mock import MagicMock, patch
        from pathlib import Path
        from ansible_runner_service.job_store import Job, JobStatus
        from datetime import datetime, timezone

        mock_job = Job(
            job_id="test-123",
            status=JobStatus.PENDING,
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )

        mock_store = MagicMock()
        mock_store.create_job.return_value = mock_job
        mock_repo = MagicMock()

        with patch("ansible_runner_service.main.get_job_store") as mock_get_store:
            # Return store configured with repo
            mock_get_store.return_value = mock_store

            with patch("ansible_runner_service.main.get_repository", return_value=mock_repo):
                with patch("ansible_runner_service.main.get_playbooks_dir", return_value=Path("playbooks")):
                    with patch("pathlib.Path.exists", return_value=True):
                        with patch("ansible_runner_service.main.enqueue_job"):
                            response = client.post(
                                "/api/v1/jobs",
                                json={"playbook": "hello.yml"},
                            )

        assert response.status_code == 202
```

**Step 2: Run test to verify current behavior**

Run: `.venv/bin/pytest tests/test_api.py::TestSubmitJobWithDB -v`
Expected: May pass (just verifying setup)

**Step 3: Update submit_job to use repository**

Update `get_job_store` dependency in main.py:
```python
def get_job_store(repository: JobRepository = Depends(get_repository)) -> JobStore:
    return JobStore(get_redis(), repository=repository)
```

**Step 4: Run all tests to verify it passes**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "feat: wire up repository in API for DB writes on job creation"
```

---

## Task 12: Add Startup Recovery Logic

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Create: `tests/test_recovery.py`

**Step 1: Write the failing test**

```python
# tests/test_recovery.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


class TestRecoverStaleJobs:
    def test_marks_stale_running_jobs_as_failed(self):
        from ansible_runner_service.main import recover_stale_jobs
        from ansible_runner_service.models import JobModel

        # Job that was "running" for over 1 hour and not in Redis
        stale_job = JobModel(
            id="stale-123",
            status="running",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        mock_repo = MagicMock()
        mock_repo.list_stale_running_jobs.return_value = [stale_job]

        mock_redis = MagicMock()
        mock_redis.exists.return_value = False  # Not in Redis

        recover_stale_jobs(mock_repo, mock_redis)

        mock_repo.update_status.assert_called_once_with(
            "stale-123",
            "failed",
            error="Worker crashed or timed out",
        )

    def test_skips_jobs_still_in_redis(self):
        from ansible_runner_service.main import recover_stale_jobs
        from ansible_runner_service.models import JobModel

        stale_job = JobModel(
            id="stale-123",
            status="running",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        mock_repo = MagicMock()
        mock_repo.list_stale_running_jobs.return_value = [stale_job]

        mock_redis = MagicMock()
        mock_redis.exists.return_value = True  # Still in Redis

        recover_stale_jobs(mock_repo, mock_redis)

        # Should NOT update since job is still active in Redis
        mock_repo.update_status.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_recovery.py -v`
Expected: FAIL (function doesn't exist)

**Step 3: Add list_stale_running_jobs to repository**

Add to `src/ansible_runner_service/repository.py`:

```python
from datetime import timedelta

def list_stale_running_jobs(
    self,
    stale_threshold: timedelta = timedelta(hours=1),
) -> list[JobModel]:
    """Find jobs that have been 'running' longer than threshold."""
    cutoff = datetime.now(timezone.utc) - stale_threshold
    return (
        self.session.query(JobModel)
        .filter(JobModel.status == "running")
        .filter(JobModel.started_at < cutoff)
        .all()
    )
```

Add import to repository.py:
```python
from datetime import datetime, timezone, timedelta
```

**Step 4: Add recover_stale_jobs function**

Add to `src/ansible_runner_service/main.py`:

```python
from contextlib import asynccontextmanager

def recover_stale_jobs(repository: JobRepository, redis: Redis) -> None:
    """Mark stale running jobs as failed on startup."""
    stale_jobs = repository.list_stale_running_jobs()

    for job in stale_jobs:
        # Only mark as failed if not in Redis (truly abandoned)
        if not redis.exists(f"job:{job.id}"):
            repository.update_status(
                job.id,
                "failed",
                error="Worker crashed or timed out",
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: recover stale jobs
    try:
        engine = get_engine()
        Session = get_session(engine)
        repository = JobRepository(Session())
        redis = get_redis()
        recover_stale_jobs(repository, redis)
    except Exception:
        pass  # Don't block startup if DB not ready

    yield

    # Shutdown: nothing to do


# Update app creation
app = FastAPI(title="Ansible Runner Service", lifespan=lifespan)
```

**Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_recovery.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/ansible_runner_service/main.py src/ansible_runner_service/repository.py tests/test_recovery.py
git commit -m "feat: add startup recovery for stale running jobs"
```

---

## Task 13: Integration Test

**Files:**
- Create: `tests/test_db_integration.py`

**Step 1: Write integration test**

```python
# tests/test_db_integration.py
import pytest
from datetime import datetime, timezone

pytestmark = pytest.mark.integration


class TestDatabaseIntegration:
    """Integration tests requiring MariaDB."""

    @pytest.fixture
    def db_session(self):
        """Create a test database session."""
        from ansible_runner_service.database import get_engine, get_session

        # Use test database
        engine = get_engine("mysql://root:devpassword@localhost:3306/ansible_runner_test")
        Session = get_session(engine)
        session = Session()

        # Create tables
        from ansible_runner_service.models import Base
        Base.metadata.create_all(engine)

        yield session

        # Cleanup
        session.rollback()
        Base.metadata.drop_all(engine)
        session.close()

    def test_create_and_get_job(self, db_session):
        from ansible_runner_service.repository import JobRepository

        repo = JobRepository(db_session)

        # Create
        job = repo.create(
            job_id="test-integration-123",
            playbook="hello.yml",
            extra_vars={"name": "Test"},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )

        assert job.id == "test-integration-123"
        assert job.status == "pending"

        # Get
        retrieved = repo.get("test-integration-123")
        assert retrieved is not None
        assert retrieved.playbook == "hello.yml"

    def test_update_status(self, db_session):
        from ansible_runner_service.repository import JobRepository

        repo = JobRepository(db_session)

        job = repo.create(
            job_id="test-update-123",
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime.now(timezone.utc),
        )

        repo.update_status(
            "test-update-123",
            "successful",
            finished_at=datetime.now(timezone.utc),
            result_rc=0,
            result_stdout="OK",
            result_stats={"localhost": {"ok": 1}},
        )

        updated = repo.get("test-update-123")
        assert updated.status == "successful"
        assert updated.result_rc == 0

    def test_list_jobs_with_filter(self, db_session):
        from ansible_runner_service.repository import JobRepository

        repo = JobRepository(db_session)

        # Create jobs with different statuses
        for i, status in enumerate(["pending", "successful", "failed"]):
            repo.create(
                job_id=f"test-list-{i}",
                playbook="hello.yml",
                extra_vars={},
                inventory="localhost,",
                created_at=datetime.now(timezone.utc),
            )
            if status != "pending":
                repo.update_status(f"test-list-{i}", status)

        # Filter by status
        failed_jobs, total = repo.list_jobs(status="failed")
        assert total == 1
        assert failed_jobs[0].id == "test-list-2"
```

**Step 2: Create test database**

Run: `docker exec -it $(docker ps -qf "ancestor=mariadb:11") mariadb -uroot -pdevpassword -e "CREATE DATABASE IF NOT EXISTS ansible_runner_test;"`

**Step 3: Run integration test**

Run: `.venv/bin/pytest tests/test_db_integration.py -v -m integration`
Expected: PASS (if MariaDB is running)

**Step 4: Commit**

```bash
git add tests/test_db_integration.py
git commit -m "test: add database integration tests"
```

---

## Task 14: Run All Tests and Final Verification

**Step 1: Run all unit tests**

Run: `.venv/bin/pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_db_integration.py`
Expected: All tests PASS

**Step 2: Start containers**

Run: `docker-compose up -d`
Run: `docker-compose ps`
Expected: Both redis and mariadb healthy

**Step 3: Run migration**

Run: `.venv/bin/alembic upgrade head`
Expected: Migration applied successfully

**Step 4: Run integration tests**

Run: `.venv/bin/pytest tests/test_db_integration.py -v -m integration`
Expected: PASS

**Step 5: Manual smoke test**

```bash
# Terminal 1: Start API
.venv/bin/uvicorn ansible_runner_service.main:app --reload

# Terminal 2: Start worker
.venv/bin/rq worker --url redis://localhost:6379

# Terminal 3: Test
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"playbook": "hello.yml"}'

# Check job in DB
docker exec -it $(docker ps -qf "ancestor=mariadb:11") \
  mariadb -uroot -pdevpassword -e "SELECT id, status, playbook FROM ansible_runner.jobs;"

# List jobs via API
curl http://localhost:8000/api/v1/jobs
```

**Step 6: Final commit**

```bash
git add -A
git commit -m "chore: final verification complete"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Dependencies & Docker | pyproject.toml, docker-compose.yml |
| 2 | Database engine | database.py |
| 3 | JobModel ORM | models.py |
| 4 | Alembic migrations | alembic/ |
| 5 | Job repository | repository.py |
| 6 | Write-through in JobStore | job_store.py |
| 7 | List schemas | schemas.py |
| 8 | GET /jobs endpoint | main.py |
| 9 | DB fallback for GET /jobs/{id} | main.py |
| 10 | Repository in worker | worker.py |
| 11 | Repository in API | main.py |
| 12 | Startup recovery | main.py, repository.py |
| 13 | Integration tests | test_db_integration.py |
| 14 | Final verification | - |
