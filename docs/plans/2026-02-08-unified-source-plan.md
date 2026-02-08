# Unified Source Field Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify `playbook` and `source` fields into a single `source` field with two-level discriminator (`type` + `target`).

**Architecture:** Replace current `playbook` + `source: GitSource` with unified `source: Source` where `type` is `local` or `git`, and `target` is `playbook` or `role`. Local sources support bundled content (baked into container). Sync mode supported for local sources with string/inline inventory.

**Tech Stack:** Pydantic discriminated unions, FastAPI, SQLAlchemy, Alembic

---

## Task 1: Update Schema Models

**Files:**
- Modify: `src/ansible_runner_service/schemas.py`
- Test: `tests/test_schemas.py`

**Step 1: Write the failing tests**

Add to `tests/test_schemas.py`:

```python
class TestLocalPlaybookSource:
    def test_minimal_source(self):
        from ansible_runner_service.schemas import LocalPlaybookSource
        source = LocalPlaybookSource(
            type="local",
            target="playbook",
            path="hello.yml",
        )
        assert source.type == "local"
        assert source.target == "playbook"
        assert source.path == "hello.yml"

    def test_path_traversal_rejected(self):
        from ansible_runner_service.schemas import LocalPlaybookSource
        with pytest.raises(ValueError):
            LocalPlaybookSource(
                type="local",
                target="playbook",
                path="../../../etc/passwd",
            )

    def test_absolute_path_rejected(self):
        from ansible_runner_service.schemas import LocalPlaybookSource
        with pytest.raises(ValueError):
            LocalPlaybookSource(
                type="local",
                target="playbook",
                path="/etc/passwd",
            )


class TestLocalRoleSource:
    def test_minimal_source(self):
        from ansible_runner_service.schemas import LocalRoleSource
        source = LocalRoleSource(
            type="local",
            target="role",
            collection="mycompany.infra",
            role="nginx",
        )
        assert source.type == "local"
        assert source.target == "role"
        assert source.collection == "mycompany.infra"
        assert source.role == "nginx"
        assert source.role_vars == {}

    def test_with_role_vars(self):
        from ansible_runner_service.schemas import LocalRoleSource
        source = LocalRoleSource(
            type="local",
            target="role",
            collection="mycompany.infra",
            role="nginx",
            role_vars={"port": 8080},
        )
        assert source.role_vars == {"port": 8080}


class TestUnifiedSourceDiscriminator:
    def test_local_playbook_discriminated(self):
        from ansible_runner_service.schemas import JobRequest
        req = JobRequest(
            source={"type": "local", "target": "playbook", "path": "hello.yml"},
        )
        assert req.source.type == "local"
        assert req.source.target == "playbook"

    def test_local_role_discriminated(self):
        from ansible_runner_service.schemas import JobRequest
        req = JobRequest(
            source={"type": "local", "target": "role", "collection": "mycompany.infra", "role": "nginx"},
        )
        assert req.source.type == "local"
        assert req.source.target == "role"

    def test_git_playbook_discriminated(self):
        from ansible_runner_service.schemas import JobRequest
        req = JobRequest(
            source={"type": "git", "target": "playbook", "repo": "https://dev.azure.com/org/p/_git/r", "path": "deploy.yml"},
        )
        assert req.source.type == "git"
        assert req.source.target == "playbook"

    def test_git_role_discriminated(self):
        from ansible_runner_service.schemas import JobRequest
        req = JobRequest(
            source={"type": "git", "target": "role", "repo": "https://gitlab.com/org/col.git", "role": "nginx"},
        )
        assert req.source.type == "git"
        assert req.source.target == "role"


class TestJobRequestUnifiedSource:
    def test_source_required(self):
        """Source is now required (no playbook field)."""
        with pytest.raises(ValidationError):
            JobRequest()

    def test_playbook_field_removed(self):
        """Playbook field no longer exists."""
        with pytest.raises(ValidationError):
            JobRequest(playbook="hello.yml")
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schemas.py::TestLocalPlaybookSource -v`
Expected: FAIL with `cannot import name 'LocalPlaybookSource'`

**Step 3: Implement the new source models**

Update `src/ansible_runner_service/schemas.py`:

```python
# Replace existing Git*Source classes with unified two-level discriminated types

class LocalPlaybookSource(BaseModel):
    type: Literal["local"]
    target: Literal["playbook"]
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("Path traversal not allowed")
        return v


class LocalRoleSource(BaseModel):
    type: Literal["local"]
    target: Literal["role"]
    collection: str
    role: str
    role_vars: dict[str, Any] = Field(default_factory=dict)


class GitPlaybookSource(BaseModel):
    type: Literal["git"]
    target: Literal["playbook"]
    repo: str
    branch: str = "main"
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("Path traversal not allowed")
        return v


class GitRoleSource(BaseModel):
    type: Literal["git"]
    target: Literal["role"]
    repo: str
    branch: str = "main"
    role: str
    role_vars: dict[str, Any] = Field(default_factory=dict)


# Two-level discriminated union
Source = Annotated[
    Union[LocalPlaybookSource, LocalRoleSource, GitPlaybookSource, GitRoleSource],
    Field(discriminator="type"),
]


class JobRequest(BaseModel):
    source: Source
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str | StructuredInventory = "localhost,"
    options: ExecutionOptions = Field(default_factory=ExecutionOptions)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schemas.py -v`
Expected: All new tests PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "$(cat <<'EOF'
feat: add unified source models with two-level discriminator

- Add LocalPlaybookSource, LocalRoleSource for bundled content
- Update GitPlaybookSource, GitRoleSource with target field
- Replace playbook + source with unified source field in JobRequest
- Add path validation for local sources

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Update TypedDict Configs for Queue

**Files:**
- Modify: `src/ansible_runner_service/schemas.py`
- Test: `tests/test_schemas.py`

**Step 1: Write the failing test**

```python
class TestSourceConfigTypedDicts:
    def test_local_playbook_config(self):
        from ansible_runner_service.schemas import LocalPlaybookSourceConfig
        config: LocalPlaybookSourceConfig = {
            "type": "local",
            "target": "playbook",
            "path": "hello.yml",
        }
        assert config["type"] == "local"
        assert config["target"] == "playbook"

    def test_local_role_config(self):
        from ansible_runner_service.schemas import LocalRoleSourceConfig
        config: LocalRoleSourceConfig = {
            "type": "local",
            "target": "role",
            "collection": "mycompany.infra",
            "role": "nginx",
            "role_vars": {},
        }
        assert config["collection"] == "mycompany.infra"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_schemas.py::TestSourceConfigTypedDicts -v`
Expected: FAIL with `cannot import name 'LocalPlaybookSourceConfig'`

**Step 3: Add TypedDict configs**

Update `src/ansible_runner_service/schemas.py`:

```python
class LocalPlaybookSourceConfig(TypedDict):
    type: Literal["local"]
    target: Literal["playbook"]
    path: str


class LocalRoleSourceConfig(TypedDict):
    type: Literal["local"]
    target: Literal["role"]
    collection: str
    role: str
    role_vars: dict[str, Any]


class GitPlaybookSourceConfig(TypedDict):
    type: Literal["git"]
    target: Literal["playbook"]
    repo: str
    branch: str
    path: str


class GitRoleSourceConfig(TypedDict):
    type: Literal["git"]
    target: Literal["role"]
    repo: str
    branch: str
    role: str
    role_vars: dict[str, Any]


SourceConfig = LocalPlaybookSourceConfig | LocalRoleSourceConfig | GitPlaybookSourceConfig | GitRoleSourceConfig
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schemas.py::TestSourceConfigTypedDicts -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "$(cat <<'EOF'
feat: add TypedDict configs for unified source types

For queue serialization and type checking.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add Database Migration

**Files:**
- Create: `alembic/versions/xxxx_add_source_target.py`
- Test: Run migration

**Step 1: Generate migration**

```bash
cd /Users/jeremy.lin/work/claude_code/ansible-runner-service/.worktrees/unified-source
source .venv/bin/activate
alembic revision -m "add source_target column"
```

**Step 2: Write migration content**

```python
"""add source_target column

Revision ID: xxxx
Revises: previous
Create Date: 2026-02-08
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = 'xxxx'
down_revision = 'previous'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('source_target', sa.String(20), nullable=True))
    # Backfill based on source_type
    # local → playbook (legacy local playbooks)
    # playbook/role → keep as-is (git sources already have target info in source_type)
    op.execute("""
        UPDATE jobs
        SET source_target = CASE
            WHEN source_type = 'local' THEN 'playbook'
            WHEN source_type = 'playbook' THEN 'playbook'
            WHEN source_type = 'role' THEN 'role'
            ELSE 'playbook'
        END
    """)
    op.alter_column('jobs', 'source_target', nullable=False)


def downgrade() -> None:
    op.drop_column('jobs', 'source_target')
```

**Step 3: Run migration**

Run: `alembic upgrade head`
Expected: Migration applies successfully

**Step 4: Verify migration**

```bash
mysql -h 127.0.0.1 -u root -p ansible_runner -e "DESCRIBE jobs;"
```
Expected: `source_target` column exists with `varchar(20) NOT NULL`

**Step 5: Commit**

```bash
git add alembic/versions/*.py
git commit -m "$(cat <<'EOF'
feat: add source_target column to jobs table

Backfills existing rows based on source_type.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Update JobModel

**Files:**
- Modify: `src/ansible_runner_service/models.py`
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_job_model_has_source_target():
    from ansible_runner_service.models import JobModel
    from datetime import datetime, timezone

    job = JobModel(
        id="test-123",
        status="pending",
        playbook="hello.yml",
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
        source_type="local",
        source_target="playbook",
    )
    assert job.source_target == "playbook"


def test_job_model_source_target_defaults():
    from ansible_runner_service.models import JobModel
    from datetime import datetime, timezone

    job = JobModel(
        id="test-123",
        status="pending",
        playbook="hello.yml",
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
    )
    assert job.source_type == "local"
    assert job.source_target == "playbook"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::test_job_model_has_source_target -v`
Expected: FAIL (source_target attribute doesn't exist)

**Step 3: Update JobModel**

Update `src/ansible_runner_service/models.py`:

```python
class JobModel(Base):
    __tablename__ = "jobs"

    # ... existing fields ...
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, insert_default="local")
    source_target: Mapped[str] = mapped_column(String(20), nullable=False, insert_default="playbook")
    source_repo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("source_type", "local")
        kwargs.setdefault("source_target", "playbook")
        super().__init__(**kwargs)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
feat: add source_target column to JobModel

Defaults to 'playbook' for backward compatibility.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Update JobStore and Job Dataclass

**Files:**
- Modify: `src/ansible_runner_service/job_store.py`
- Test: `tests/test_job_store.py`

**Step 1: Write the failing test**

Add to `tests/test_job_store.py`:

```python
def test_create_job_with_source_target(job_store):
    job = job_store.create_job(
        playbook="hello.yml",
        extra_vars={},
        inventory="localhost,",
        source_type="local",
        source_target="playbook",
    )
    assert job.source_target == "playbook"


def test_create_job_with_local_role(job_store):
    job = job_store.create_job(
        playbook="nginx",  # role name stored in playbook field
        extra_vars={},
        inventory="localhost,",
        source_type="local",
        source_target="role",
    )
    assert job.source_type == "local"
    assert job.source_target == "role"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_job_store.py::test_create_job_with_source_target -v`
Expected: FAIL

**Step 3: Update Job dataclass and JobStore**

Update `src/ansible_runner_service/job_store.py`:

```python
@dataclass
class Job:
    job_id: str
    status: JobStatus
    playbook: str
    extra_vars: dict[str, Any]
    inventory: str | dict
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: JobResult | None = None
    error: str | None = None
    source_type: str = "local"
    source_target: str = "playbook"
    source_repo: str | None = None
    source_branch: str | None = None
    options: dict | None = None


class JobStore:
    def create_job(
        self,
        playbook: str,
        extra_vars: dict[str, Any],
        inventory: str | dict,
        source_type: str = "local",
        source_target: str = "playbook",
        source_repo: str | None = None,
        source_branch: str | None = None,
        options: dict | None = None,
    ) -> Job:
        job = Job(
            job_id=str(uuid.uuid4()),
            status=JobStatus.PENDING,
            playbook=playbook,
            extra_vars=extra_vars,
            inventory=inventory,
            created_at=datetime.now(timezone.utc),
            source_type=source_type,
            source_target=source_target,
            source_repo=source_repo,
            source_branch=source_branch,
            options=options,
        )
        self._save_job(job)

        if self.repository:
            try:
                self.repository.create(
                    job_id=job.job_id,
                    playbook=playbook,
                    extra_vars=extra_vars,
                    inventory=inventory,
                    created_at=job.created_at,
                    source_type=source_type,
                    source_target=source_target,
                    source_repo=source_repo,
                    source_branch=source_branch,
                    options=options,
                )
            except Exception:
                self.redis.delete(self._job_key(job.job_id))
                raise

        return job

    def _save_job(self, job: Job) -> None:
        data = {
            # ... existing fields ...
            "source_type": job.source_type,
            "source_target": job.source_target,
            "source_repo": job.source_repo or "",
            "source_branch": job.source_branch or "",
            # ...
        }
        # ...

    def _deserialize_job(self, data: dict[bytes, bytes]) -> Job:
        # ...
        return Job(
            # ... existing fields ...
            source_type=get_str("source_type") or "local",
            source_target=get_str("source_target") or "playbook",
            source_repo=get_str("source_repo") or None,
            source_branch=get_str("source_branch") or None,
            # ...
        )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_job_store.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/job_store.py tests/test_job_store.py
git commit -m "$(cat <<'EOF'
feat: add source_target to Job dataclass and JobStore

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Update Repository

**Files:**
- Modify: `src/ansible_runner_service/repository.py`
- Test: `tests/test_repository.py`

**Step 1: Write the failing test**

Add to `tests/test_repository.py`:

```python
def test_create_job_with_source_target(test_session):
    repo = JobRepository(test_session)
    repo.create(
        job_id="test-target-1",
        playbook="hello.yml",
        extra_vars={},
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
        source_type="local",
        source_target="playbook",
    )

    job = repo.get("test-target-1")
    assert job.source_target == "playbook"


def test_create_job_with_local_role(test_session):
    repo = JobRepository(test_session)
    repo.create(
        job_id="test-role-1",
        playbook="nginx",
        extra_vars={},
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
        source_type="local",
        source_target="role",
    )

    job = repo.get("test-role-1")
    assert job.source_type == "local"
    assert job.source_target == "role"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_repository.py::test_create_job_with_source_target -v`
Expected: FAIL

**Step 3: Update JobRepository.create()**

Update `src/ansible_runner_service/repository.py`:

```python
def create(
    self,
    job_id: str,
    playbook: str,
    extra_vars: dict,
    inventory: str | dict,
    created_at: datetime,
    source_type: str = "local",
    source_target: str = "playbook",
    source_repo: str | None = None,
    source_branch: str | None = None,
    options: dict | None = None,
) -> JobModel:
    job = JobModel(
        id=job_id,
        status="pending",
        playbook=playbook,
        extra_vars=extra_vars,
        inventory=inventory,
        created_at=created_at,
        source_type=source_type,
        source_target=source_target,
        source_repo=source_repo,
        source_branch=source_branch,
        options=options,
    )
    self.session.add(job)
    self.session.commit()
    return job
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repository.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/repository.py tests/test_repository.py
git commit -m "$(cat <<'EOF'
feat: add source_target to JobRepository.create()

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update Main API Handler

**Files:**
- Modify: `src/ansible_runner_service/main.py`
- Test: `tests/test_api.py`

**Step 1: Write the failing tests**

Add to `tests/test_api.py`:

```python
class TestUnifiedSource:
    async def test_local_playbook_sync(self, playbooks_dir: Path):
        """Local playbook with sync mode."""
        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/jobs?sync=true",
                    json={
                        "source": {"type": "local", "target": "playbook", "path": "hello.yml"},
                    },
                )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "successful"
            assert "Hello, World!" in data["stdout"]
        finally:
            app.dependency_overrides.clear()

    async def test_local_playbook_async(self, playbooks_dir: Path):
        """Local playbook with async mode."""
        from ansible_runner_service.job_store import Job, JobStatus

        mock_store = MagicMock()
        mock_store.create_job.return_value = Job(
            job_id="local-async-1",
            status=JobStatus.PENDING,
            playbook="hello.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc),
            source_type="local",
            source_target="playbook",
        )
        mock_redis = MagicMock()

        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
        app.dependency_overrides[get_job_store] = lambda: mock_store
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                with patch("ansible_runner_service.main.enqueue_job") as mock_enqueue:
                    response = await client.post(
                        "/api/v1/jobs",
                        json={
                            "source": {"type": "local", "target": "playbook", "path": "hello.yml"},
                        },
                    )

            assert response.status_code == 202
            mock_store.create_job.assert_called_once()
            call_kwargs = mock_store.create_job.call_args[1]
            assert call_kwargs["source_type"] == "local"
            assert call_kwargs["source_target"] == "playbook"
        finally:
            app.dependency_overrides.clear()

    async def test_git_playbook_async(self, playbooks_dir: Path):
        """Git playbook - async only."""
        from ansible_runner_service.job_store import Job, JobStatus
        from ansible_runner_service.git_config import GitProvider

        mock_store = MagicMock()
        mock_store.create_job.return_value = Job(
            job_id="git-async-1",
            status=JobStatus.PENDING,
            playbook="deploy.yml",
            extra_vars={},
            inventory="localhost,",
            created_at=datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc),
            source_type="git",
            source_target="playbook",
        )
        mock_redis = MagicMock()

        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
        app.dependency_overrides[get_job_store] = lambda: mock_store
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                with patch("ansible_runner_service.main.enqueue_job") as mock_enqueue, \
                     patch("ansible_runner_service.main.load_providers") as mock_providers, \
                     patch("ansible_runner_service.main.validate_repo_url") as mock_validate:
                    mock_providers.return_value = [
                        GitProvider(type="azure", host="dev.azure.com", orgs=["org"], credential_env="PAT"),
                    ]
                    mock_validate.return_value = mock_providers.return_value[0]

                    response = await client.post(
                        "/api/v1/jobs",
                        json={
                            "source": {
                                "type": "git",
                                "target": "playbook",
                                "repo": "https://dev.azure.com/org/p/_git/r",
                                "path": "deploy.yml",
                            },
                        },
                    )

            assert response.status_code == 202
            call_kwargs = mock_store.create_job.call_args[1]
            assert call_kwargs["source_type"] == "git"
            assert call_kwargs["source_target"] == "playbook"
        finally:
            app.dependency_overrides.clear()

    async def test_git_source_sync_rejected(self, playbooks_dir: Path):
        """Sync mode not supported for git sources."""
        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                with patch("ansible_runner_service.main.load_providers") as mock_providers, \
                     patch("ansible_runner_service.main.validate_repo_url") as mock_validate:
                    from ansible_runner_service.git_config import GitProvider
                    mock_providers.return_value = [
                        GitProvider(type="azure", host="dev.azure.com", orgs=["org"], credential_env="PAT"),
                    ]
                    mock_validate.return_value = mock_providers.return_value[0]

                    response = await client.post(
                        "/api/v1/jobs?sync=true",
                        json={
                            "source": {
                                "type": "git",
                                "target": "playbook",
                                "repo": "https://dev.azure.com/org/p/_git/r",
                                "path": "deploy.yml",
                            },
                        },
                    )

            assert response.status_code == 400
            assert "sync" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    async def test_local_with_git_inventory_sync_rejected(self, playbooks_dir: Path):
        """Local source + git inventory cannot use sync mode."""
        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/jobs?sync=true",
                    json={
                        "source": {"type": "local", "target": "playbook", "path": "hello.yml"},
                        "inventory": {
                            "type": "git",
                            "repo": "https://dev.azure.com/org/p/_git/inv",
                            "path": "hosts.yml",
                        },
                    },
                )

            assert response.status_code == 400
            assert "git inventory" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    async def test_legacy_playbook_field_rejected(self, playbooks_dir: Path):
        """Legacy playbook field no longer accepted."""
        app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/jobs",
                    json={"playbook": "hello.yml"},
                )

            assert response.status_code == 422  # Validation error
        finally:
            app.dependency_overrides.clear()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py::TestUnifiedSource -v`
Expected: FAIL

**Step 3: Rewrite submit_job handler**

Update `src/ansible_runner_service/main.py`:

```python
from ansible_runner_service.schemas import (
    LocalPlaybookSource,
    LocalRoleSource,
    GitPlaybookSource,
    GitRoleSource,
    GitInventory,
    InlineInventory,
    JobRequest,
    JobResponse,
    JobSubmitResponse,
    # ... other imports
    LocalPlaybookSourceConfig,
    LocalRoleSourceConfig,
    GitPlaybookSourceConfig,
    GitRoleSourceConfig,
    SourceConfig,
)

PLAYBOOKS_DIR = Path(__file__).parent.parent.parent / "playbooks"
COLLECTIONS_DIR = Path(__file__).parent.parent.parent / "collections"


def get_playbooks_dir() -> Path:
    return PLAYBOOKS_DIR


def get_collections_dir() -> Path:
    return COLLECTIONS_DIR


@app.post(
    "/api/v1/jobs",
    response_model=Union[JobSubmitResponse, JobResponse],
    status_code=202,
)
def submit_job(
    request: JobRequest,
    sync: bool = Query(default=False, description="Run synchronously"),
    playbooks_dir: Path = Depends(get_playbooks_dir),
    collections_dir: Path = Depends(get_collections_dir),
    job_store: JobStore = Depends(get_job_store),
    redis: Redis = Depends(get_redis),
) -> Union[JobSubmitResponse, JobResponse]:
    """Submit a job for execution."""
    source = request.source

    # Validate sync mode constraints
    if sync:
        if source.type == "git":
            raise HTTPException(
                status_code=400,
                detail="Sync mode not supported for git sources. Use async mode.",
            )
        if isinstance(request.inventory, GitInventory):
            raise HTTPException(
                status_code=400,
                detail="Sync mode does not support git inventory. Use async mode.",
            )

    # Validate git repo if applicable
    if source.type == "git":
        providers = load_providers()
        try:
            validate_repo_url(source.repo, providers)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Determine playbook name for storage
    if source.target == "playbook":
        playbook_name = source.path
    else:  # role
        playbook_name = source.role

    # Build source_config for queue
    source_config = _build_source_config(source)

    # Serialize inventory
    inventory = request.inventory
    if not isinstance(inventory, str):
        inventory = inventory.model_dump()

    # Serialize options
    options = request.options.model_dump(exclude_defaults=True) or None

    if sync:
        return _execute_sync(
            source=source,
            extra_vars=request.extra_vars,
            inventory=request.inventory,
            options=options,
            playbooks_dir=playbooks_dir,
            collections_dir=collections_dir,
        )

    # Async mode
    job = job_store.create_job(
        playbook=playbook_name,
        extra_vars=request.extra_vars,
        inventory=inventory,
        source_type=source.type,
        source_target=source.target,
        source_repo=getattr(source, "repo", None),
        source_branch=getattr(source, "branch", None),
        options=options,
    )

    enqueue_job(
        job_id=job.job_id,
        playbook=playbook_name,
        extra_vars=request.extra_vars,
        inventory=inventory,
        source_config=source_config,
        options=options,
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


def _build_source_config(source) -> SourceConfig:
    """Build TypedDict source config for queue serialization."""
    if isinstance(source, LocalPlaybookSource):
        return LocalPlaybookSourceConfig(
            type="local",
            target="playbook",
            path=source.path,
        )
    elif isinstance(source, LocalRoleSource):
        return LocalRoleSourceConfig(
            type="local",
            target="role",
            collection=source.collection,
            role=source.role,
            role_vars=source.role_vars,
        )
    elif isinstance(source, GitPlaybookSource):
        return GitPlaybookSourceConfig(
            type="git",
            target="playbook",
            repo=source.repo,
            branch=source.branch,
            path=source.path,
        )
    elif isinstance(source, GitRoleSource):
        return GitRoleSourceConfig(
            type="git",
            target="role",
            repo=source.repo,
            branch=source.branch,
            role=source.role,
            role_vars=source.role_vars,
        )
    else:
        raise ValueError(f"Unknown source type: {type(source)}")


def _execute_sync(
    source,
    extra_vars: dict,
    inventory,
    options: dict | None,
    playbooks_dir: Path,
    collections_dir: Path,
) -> JSONResponse:
    """Execute job synchronously - only for local sources with string/inline inventory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Resolve inventory
        if isinstance(inventory, str):
            resolved_inventory = inventory
        else:  # InlineInventory
            inv_path = os.path.join(tmpdir, "inventory.yml")
            with open(inv_path, "w") as f:
                yaml.dump(inventory.data, f, default_flow_style=False)
            resolved_inventory = inv_path

        if isinstance(source, LocalPlaybookSource):
            # Validate path
            if ".." in source.path or source.path.startswith("/"):
                raise HTTPException(status_code=400, detail="Invalid playbook path")

            playbook_path = playbooks_dir / source.path
            if not playbook_path.exists():
                raise HTTPException(status_code=404, detail=f"Playbook not found: {source.path}")

            result = run_playbook(
                playbook=source.path,
                extra_vars=extra_vars,
                inventory=resolved_inventory,
                playbooks_dir=playbooks_dir,
                options=options,
            )
        elif isinstance(source, LocalRoleSource):
            # Generate wrapper playbook for local role
            fqcn = f"{source.collection}.{source.role}"
            wrapper_content = generate_role_wrapper_playbook(fqcn=fqcn, role_vars=source.role_vars)
            wrapper_path = os.path.join(tmpdir, "wrapper_playbook.yml")
            with open(wrapper_path, "w") as f:
                f.write(wrapper_content)

            result = run_playbook(
                playbook=wrapper_path,
                extra_vars=extra_vars,
                inventory=resolved_inventory,
                envvars={"ANSIBLE_COLLECTIONS_PATH": str(collections_dir)},
                options=options,
            )
        else:
            raise HTTPException(status_code=400, detail="Sync mode only supports local sources")

    return JSONResponse(
        status_code=200,
        content=JobResponse(
            status=result.status,
            rc=result.rc,
            stdout=result.stdout,
            stats=result.stats,
        ).model_dump(),
    )
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api.py::TestUnifiedSource -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "$(cat <<'EOF'
feat: unified source handler in submit_job

- Remove _handle_local_source and _handle_git_source
- Add _execute_sync for local sources with string/inline inventory
- Add _build_source_config for queue serialization
- Support local role sync execution

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Update Worker for Local Role Execution

**Files:**
- Modify: `src/ansible_runner_service/worker.py`
- Test: `tests/test_worker.py`

**Step 1: Write the failing test**

Add to `tests/test_worker.py`:

```python
class TestLocalRoleExecution:
    def test_execute_local_role(self, tmp_path):
        """Local role execution generates wrapper and runs."""
        from ansible_runner_service.worker import _execute_local_role
        from ansible_runner_service.schemas import LocalRoleSourceConfig
        from unittest.mock import patch, MagicMock

        source_config: LocalRoleSourceConfig = {
            "type": "local",
            "target": "role",
            "collection": "mycompany.infra",
            "role": "nginx",
            "role_vars": {"port": 8080},
        }

        collections_dir = tmp_path / "collections"
        collections_dir.mkdir()

        with patch("ansible_runner_service.worker.run_playbook") as mock_run, \
             patch("ansible_runner_service.worker.get_collections_dir") as mock_coll_dir:
            mock_coll_dir.return_value = collections_dir
            mock_run.return_value = MagicMock(rc=0, stdout="OK", stats={})

            result = _execute_local_role(
                source_config=source_config,
                extra_vars={"key": "value"},
                inventory="localhost,",
                options=None,
            )

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert "ANSIBLE_COLLECTIONS_PATH" in call_kwargs.get("envvars", {})
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker.py::TestLocalRoleExecution -v`
Expected: FAIL with `cannot import name '_execute_local_role'`

**Step 3: Add local role execution to worker**

Update `src/ansible_runner_service/worker.py`:

```python
COLLECTIONS_DIR = Path(__file__).parent.parent.parent / "collections"


def get_collections_dir() -> Path:
    return COLLECTIONS_DIR


def _execute_local_role(source_config, extra_vars, inventory, options=None):
    """Execute a local role from bundled collections."""
    collections_dir = get_collections_dir()

    fqcn = f"{source_config['collection']}.{source_config['role']}"
    role_vars = source_config.get("role_vars", {})

    with tempfile.TemporaryDirectory() as tmpdir:
        wrapper_content = generate_role_wrapper_playbook(fqcn=fqcn, role_vars=role_vars)
        wrapper_path = os.path.join(tmpdir, "wrapper_playbook.yml")
        with open(wrapper_path, "w") as f:
            f.write(wrapper_content)

        return run_playbook(
            playbook=wrapper_path,
            extra_vars=extra_vars,
            inventory=inventory,
            envvars={"ANSIBLE_COLLECTIONS_PATH": str(collections_dir)},
            options=options,
        )


def execute_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str | dict,
    options: dict | None = None,
    source_config: SourceConfig | None = None,
) -> None:
    """Execute a job - called by rq worker."""
    # ... existing setup ...

    try:
        with tempfile.TemporaryDirectory() as inv_tmpdir:
            resolved_inventory = _resolve_inventory(inventory, inv_tmpdir)

            if source_config is None:
                # Legacy: no source_config means old local playbook
                result = _execute_local(playbook, extra_vars, resolved_inventory, options)
            elif source_config["type"] == "local" and source_config["target"] == "playbook":
                result = _execute_local(source_config["path"], extra_vars, resolved_inventory, options)
            elif source_config["type"] == "local" and source_config["target"] == "role":
                result = _execute_local_role(source_config, extra_vars, resolved_inventory, options)
            elif source_config["type"] == "git" and source_config["target"] == "playbook":
                result = _execute_git_playbook(source_config, extra_vars, resolved_inventory, options)
            elif source_config["type"] == "git" and source_config["target"] == "role":
                result = _execute_git_role(source_config, extra_vars, resolved_inventory, options)
            else:
                raise ValueError(f"Unknown source: {source_config}")

        # ... rest of status update ...
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/worker.py tests/test_worker.py
git commit -m "$(cat <<'EOF'
feat: add local role execution to worker

- Add _execute_local_role for bundled collections
- Update execute_job to handle all four source types
- Add get_collections_dir for bundled content path

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Remove Legacy Tests and Update Existing Tests

**Files:**
- Modify: `tests/test_api.py`
- Modify: `tests/test_schemas.py`
- Modify: Other test files as needed

**Step 1: Identify tests using legacy playbook field**

Search for tests using `"playbook":` pattern.

**Step 2: Update all tests to use unified source**

Replace all instances of:
```python
json={"playbook": "hello.yml"}
```

With:
```python
json={"source": {"type": "local", "target": "playbook", "path": "hello.yml"}}
```

**Step 3: Remove obsolete test classes**

Remove:
- `TestJobRequestBackwardCompatibility` (playbook field tests)
- Any tests testing `playbook` + `source` mutual exclusion

**Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add tests/
git commit -m "$(cat <<'EOF'
test: update all tests for unified source field

- Replace playbook field with source in all tests
- Remove backward compatibility tests
- Update mocks to use new schema

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Update Integration Tests

**Files:**
- Modify: `tests/test_integration.py`
- Modify: `tests/test_queue_integration.py`

**Step 1: Update integration tests**

Replace legacy format with unified source in all integration tests.

**Step 2: Add local role integration test (if bundled collection exists)**

```python
async def test_local_role_async_execution(client):
    """Local role from bundled collection runs successfully."""
    # This test requires a bundled collection at collections/
    # Skip if not present
    collections_dir = Path(__file__).parent.parent / "collections"
    if not (collections_dir / "ansible_collections").exists():
        pytest.skip("No bundled collections for testing")

    response = await client.post(
        "/api/v1/jobs",
        json={
            "source": {
                "type": "local",
                "target": "role",
                "collection": "test.collection",
                "role": "example",
            },
        },
    )
    assert response.status_code == 202
```

**Step 3: Run integration tests**

Run: `pytest tests/test_integration.py tests/test_queue_integration.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add tests/test_integration.py tests/test_queue_integration.py
git commit -m "$(cat <<'EOF'
test: update integration tests for unified source

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Clean Up Legacy Code

**Files:**
- Modify: `src/ansible_runner_service/schemas.py`
- Modify: `src/ansible_runner_service/main.py`

**Step 1: Remove obsolete TypedDicts**

Remove old `PlaybookSourceConfig` and `RoleSourceConfig` if they were only for legacy git sources.

**Step 2: Clean up imports**

Remove unused imports from all modified files.

**Step 3: Run tests**

Run: `pytest tests/ -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/ansible_runner_service/
git commit -m "$(cat <<'EOF'
refactor: remove legacy source types and clean up imports

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Final Full Test Suite

**Step 1: Run complete test suite**

```bash
source .venv/bin/activate
pytest tests/ -v
```

**Step 2: Verify all tests pass**

Expected: 0 failures, 0 errors

**Step 3: If any failures, fix them**

---

## Task 13: Update Design Document

**Files:**
- Modify: `docs/plans/2026-02-07-unified-source-design.md`

**Step 1: Mark as implemented**

Add to the top of the design doc:

```markdown
> **Status:** Implemented in feature/unified-source branch
```

**Step 2: Commit**

```bash
git add docs/plans/2026-02-07-unified-source-design.md
git commit -m "$(cat <<'EOF'
docs: mark unified source design as implemented

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Summary

| Task | Description |
|------|-------------|
| 1 | Update schema models with two-level discriminator |
| 2 | Add TypedDict configs for queue serialization |
| 3 | Add database migration for source_target column |
| 4 | Update JobModel with source_target |
| 5 | Update JobStore and Job dataclass |
| 6 | Update Repository |
| 7 | Update main API handler |
| 8 | Add local role execution to worker |
| 9 | Update all existing tests |
| 10 | Update integration tests |
| 11 | Clean up legacy code |
| 12 | Run final full test suite |
| 13 | Update design document |
