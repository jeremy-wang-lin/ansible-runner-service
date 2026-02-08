# JobRequest Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign `JobRequest` to support structured inventory (inline Ansible YAML as JSON, git-sourced) and execution options (check, diff, tags, skip_tags, limit, verbosity), with full backward compatibility.

**Architecture:** The inventory field becomes a union of `str | InlineInventory | GitInventory` using Pydantic's discriminated union. Execution options are grouped under a nested `ExecutionOptions` model. The worker resolves inventory to a string or file path and maps options to `ansible_runner.run()` kwargs. Database `inventory` column migrates from `String(255)` to `JSON`; a new `options` JSON column is added.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy, Alembic, Redis, ansible-runner, pytest

**Design doc:** `docs/plans/2026-02-06-jobrequest-redesign-design.md`

---

### Task 1: Schema — InlineInventory and GitInventory models

**Files:**
- Modify: `src/ansible_runner_service/schemas.py:1-22` (imports and TypedDicts)
- Modify: `src/ansible_runner_service/schemas.py:53-57` (JobRequest)
- Test: `tests/test_schemas.py`

**Step 1: Write failing tests for new inventory schemas**

Add to `tests/test_schemas.py`:

```python
from ansible_runner_service.schemas import (
    InlineInventory,
    GitInventory,
    ExecutionOptions,
)


class TestInlineInventory:
    def test_valid_inline(self):
        inv = InlineInventory(
            type="inline",
            data={
                "webservers": {
                    "hosts": {"10.0.1.10": {"http_port": "8080"}, "10.0.1.11": None}
                }
            },
        )
        assert inv.type == "inline"
        assert "webservers" in inv.data

    def test_inline_requires_data(self):
        with pytest.raises(ValidationError):
            InlineInventory(type="inline")


class TestGitInventory:
    def test_valid_git_inventory(self):
        inv = GitInventory(
            type="git",
            repo="https://dev.azure.com/org/project/_git/inventory",
            path="production/hosts.yml",
        )
        assert inv.type == "git"
        assert inv.branch == "main"

    def test_git_inventory_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            GitInventory(
                type="git",
                repo="https://dev.azure.com/org/project/_git/inventory",
                path="../../../etc/passwd",
            )

    def test_git_inventory_absolute_path_rejected(self):
        with pytest.raises(ValueError):
            GitInventory(
                type="git",
                repo="https://dev.azure.com/org/project/_git/inventory",
                path="/etc/hosts",
            )
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_schemas.py::TestInlineInventory tests/test_schemas.py::TestGitInventory -v`
Expected: FAIL — `ImportError: cannot import name 'InlineInventory'`

**Step 3: Implement InlineInventory and GitInventory**

In `src/ansible_runner_service/schemas.py`, add after the `SourceConfig` line (line 22) and before `GitPlaybookSource` (line 25):

```python
class InlineInventory(BaseModel):
    type: Literal["inline"]
    data: dict[str, Any]


class GitInventory(BaseModel):
    type: Literal["git"]
    repo: str
    branch: str = "main"
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("Path traversal not allowed")
        return v


StructuredInventory = Annotated[
    Union[InlineInventory, GitInventory],
    Field(discriminator="type"),
]
```

Also add TypedDicts for the worker side (after existing TypedDicts):

```python
class InlineInventoryConfig(TypedDict):
    type: Literal["inline"]
    data: dict[str, Any]


class GitInventoryConfig(TypedDict):
    type: Literal["git"]
    repo: str
    branch: str
    path: str


InventoryConfig = InlineInventoryConfig | GitInventoryConfig
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_schemas.py::TestInlineInventory tests/test_schemas.py::TestGitInventory -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "feat: add InlineInventory and GitInventory schema models"
```

---

### Task 2: Schema — ExecutionOptions model

**Files:**
- Modify: `src/ansible_runner_service/schemas.py`
- Test: `tests/test_schemas.py`

**Step 1: Write failing tests for ExecutionOptions**

Add to `tests/test_schemas.py`:

```python
class TestExecutionOptions:
    def test_defaults(self):
        opts = ExecutionOptions()
        assert opts.check is False
        assert opts.diff is False
        assert opts.tags == []
        assert opts.skip_tags == []
        assert opts.limit is None
        assert opts.verbosity == 0
        assert opts.vault_password_file is None

    def test_all_options(self):
        opts = ExecutionOptions(
            check=True,
            diff=True,
            tags=["deploy", "config"],
            skip_tags=["debug"],
            limit="webservers",
            verbosity=3,
        )
        assert opts.check is True
        assert opts.tags == ["deploy", "config"]
        assert opts.verbosity == 3

    def test_verbosity_range(self):
        with pytest.raises(ValidationError):
            ExecutionOptions(verbosity=5)

    def test_verbosity_negative_rejected(self):
        with pytest.raises(ValidationError):
            ExecutionOptions(verbosity=-1)
```

Also add an `ExecutionOptionsConfig` TypedDict for worker side.

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_schemas.py::TestExecutionOptions -v`
Expected: FAIL — `ImportError`

**Step 3: Implement ExecutionOptions**

In `src/ansible_runner_service/schemas.py`, add after `StructuredInventory`:

```python
class ExecutionOptions(BaseModel):
    check: bool = False
    diff: bool = False
    tags: list[str] = Field(default_factory=list)
    skip_tags: list[str] = Field(default_factory=list)
    limit: str | None = None
    verbosity: int = Field(default=0, ge=0, le=4)
    vault_password_file: str | None = None


class ExecutionOptionsConfig(TypedDict, total=False):
    check: bool
    diff: bool
    tags: list[str]
    skip_tags: list[str]
    limit: str
    verbosity: int
    vault_password_file: str
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_schemas.py::TestExecutionOptions -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "feat: add ExecutionOptions schema model"
```

---

### Task 3: Schema — Update JobRequest with new inventory and options fields

**Files:**
- Modify: `src/ansible_runner_service/schemas.py:53-65` (JobRequest class)
- Test: `tests/test_schemas.py`

**Step 1: Write failing tests for updated JobRequest**

Add to `tests/test_schemas.py`:

```python
class TestJobRequestInventoryTypes:
    def test_string_inventory_still_works(self):
        req = JobRequest(playbook="hello.yml", inventory="myhost,")
        assert req.inventory == "myhost,"

    def test_inline_inventory(self):
        req = JobRequest(
            playbook="hello.yml",
            inventory={
                "type": "inline",
                "data": {"webservers": {"hosts": {"10.0.1.10": None}}},
            },
        )
        assert isinstance(req.inventory, InlineInventory)
        assert req.inventory.data["webservers"]["hosts"]["10.0.1.10"] is None

    def test_git_inventory(self):
        req = JobRequest(
            playbook="hello.yml",
            inventory={
                "type": "git",
                "repo": "https://dev.azure.com/org/project/_git/inv",
                "path": "prod/hosts.yml",
            },
        )
        assert isinstance(req.inventory, GitInventory)
        assert req.inventory.branch == "main"

    def test_invalid_inventory_type_rejected(self):
        with pytest.raises(ValidationError):
            JobRequest(
                playbook="hello.yml",
                inventory={"type": "unknown", "data": {}},
            )

    def test_default_inventory_unchanged(self):
        req = JobRequest(playbook="hello.yml")
        assert req.inventory == "localhost,"

    def test_options_default(self):
        req = JobRequest(playbook="hello.yml")
        assert req.options.check is False
        assert req.options.verbosity == 0

    def test_options_provided(self):
        req = JobRequest(
            playbook="hello.yml",
            options={"check": True, "tags": ["deploy"]},
        )
        assert req.options.check is True
        assert req.options.tags == ["deploy"]
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_schemas.py::TestJobRequestInventoryTypes -v`
Expected: FAIL — `inventory` field doesn't accept dict

**Step 3: Update JobRequest**

In `src/ansible_runner_service/schemas.py`, change the `JobRequest` class:

```python
class JobRequest(BaseModel):
    playbook: str | None = Field(default=None, min_length=1)
    source: GitSource | None = None
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str | StructuredInventory = "localhost,"
    options: ExecutionOptions = Field(default_factory=ExecutionOptions)

    @model_validator(mode="after")
    def validate_playbook_or_source(self):
        if self.playbook and self.source:
            raise ValueError("Provide either 'playbook' or 'source', not both")
        if not self.playbook and not self.source:
            raise ValueError("Must provide either 'playbook' or 'source'")
        return self
```

**Step 4: Run all schema tests to verify pass + backward compat**

Run: `source .venv/bin/activate && pytest tests/test_schemas.py -v`
Expected: ALL PASS (including existing tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "feat: update JobRequest with structured inventory and execution options"
```

---

### Task 4: Database migration — inventory column to JSON, add options column

**Files:**
- Create: `alembic/versions/jobrequest_redesign.py`
- Modify: `src/ansible_runner_service/models.py:20` (inventory column)
- Modify: `src/ansible_runner_service/models.py` (add options column)

**Step 1: Create Alembic migration**

Run: `source .venv/bin/activate && alembic revision -m "change inventory to json add options column"`

Then edit the generated migration file:

```python
"""change inventory to json add options column"""

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.alter_column(
        "jobs",
        "inventory",
        existing_type=sa.String(255),
        type_=sa.JSON,
        existing_nullable=False,
        postgresql_using="inventory::json",
    )
    op.add_column("jobs", sa.Column("options", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "options")
    op.alter_column(
        "jobs",
        "inventory",
        existing_type=sa.JSON,
        type_=sa.String(255),
        existing_nullable=False,
    )
```

**Step 2: Update SQLAlchemy model**

In `src/ansible_runner_service/models.py`, change line 20:

```python
# From:
inventory: Mapped[str] = mapped_column(String(255), nullable=False)
# To:
inventory: Mapped[Any] = mapped_column(JSON, nullable=False)
```

And add after line 27 (error column):

```python
options: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
```

**Step 3: Run migration**

Run: `source .venv/bin/activate && alembic upgrade head`
Expected: Migration applied successfully

**Step 4: Verify with existing model tests**

Run: `source .venv/bin/activate && pytest tests/test_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add alembic/versions/ src/ansible_runner_service/models.py
git commit -m "feat: migrate inventory to JSON column, add options column"
```

---

### Task 5: Update Job dataclass and JobStore for new fields

**Files:**
- Modify: `src/ansible_runner_service/job_store.py:29-43` (Job dataclass)
- Modify: `src/ansible_runner_service/job_store.py:60-100` (create_job)
- Modify: `src/ansible_runner_service/job_store.py:142-188` (serialize/deserialize)
- Test: `tests/test_job_store.py`

**Step 1: Write failing tests**

Add to `tests/test_job_store.py`:

```python
class TestJobStoreInventoryAndOptions:
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
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_job_store.py::TestJobStoreInventoryAndOptions -v`
Expected: FAIL

**Step 3: Update Job dataclass and JobStore**

In `src/ansible_runner_service/job_store.py`:

Update `Job` dataclass (line 35):
```python
inventory: str | dict  # was: inventory: str
```

Add after `source_branch` field (line 43):
```python
options: dict | None = None
```

Update `create_job()` signature (line 60-68) to accept `inventory: str | dict` and `options: dict | None = None`.

Update `create_job()` body to pass `options` to `Job()` constructor and to `self.repository.create()`.

Update `_save_job()` — serialize `inventory` as JSON when it's a dict:
```python
"inventory": json.dumps(job.inventory) if isinstance(job.inventory, dict) else job.inventory,
```

Add options serialization:
```python
"options": json.dumps(job.options) if job.options else "",
```

Update `_deserialize_job()` — deserialize `inventory`:
```python
inv_str = get_str("inventory")
try:
    inventory = json.loads(inv_str)
except (json.JSONDecodeError, ValueError):
    inventory = inv_str
```

Add options deserialization:
```python
options_str = get_str("options")
options = json.loads(options_str) if options_str else None
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_job_store.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/job_store.py tests/test_job_store.py
git commit -m "feat: update Job dataclass and JobStore for structured inventory and options"
```

---

### Task 6: Update Repository for new field types

**Files:**
- Modify: `src/ansible_runner_service/repository.py:15-40` (create method)
- Test: `tests/test_repository.py`

**Step 1: Write failing test**

Add to `tests/test_repository.py`:

```python
def test_create_job_with_dict_inventory(self, session):
    repo = JobRepository(session)
    inventory = {"type": "inline", "data": {"all": {"hosts": {"h1": None}}}}
    job = repo.create(
        job_id="inv-test",
        playbook="test.yml",
        extra_vars={},
        inventory=inventory,
        created_at=datetime.now(timezone.utc),
    )
    assert job.inventory == inventory

def test_create_job_with_options(self, session):
    repo = JobRepository(session)
    options = {"check": True, "tags": ["deploy"]}
    job = repo.create(
        job_id="opt-test",
        playbook="test.yml",
        extra_vars={},
        inventory="localhost,",
        created_at=datetime.now(timezone.utc),
        options=options,
    )
    assert job.options == options
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_repository.py::test_create_job_with_dict_inventory tests/test_repository.py::test_create_job_with_options -v`
Expected: FAIL

**Step 3: Update Repository**

In `src/ansible_runner_service/repository.py`, update the `create()` method signature:

```python
def create(
    self,
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str | dict,  # was: inventory: str
    created_at: datetime,
    source_type: str = "local",
    source_repo: str | None = None,
    source_branch: str | None = None,
    options: dict | None = None,  # new
) -> JobModel:
```

Add `options=options` to the `JobModel()` constructor.

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_repository.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/repository.py tests/test_repository.py
git commit -m "feat: update Repository to accept structured inventory and options"
```

---

### Task 7: Update queue to pass options

**Files:**
- Modify: `src/ansible_runner_service/queue.py:14-37`
- Test: `tests/test_queue.py`

**Step 1: Write failing test**

Add to `tests/test_queue.py`:

```python
def test_enqueue_with_options(self, mock_queue, redis):
    enqueue_job(
        job_id="test-123",
        playbook="hello.yml",
        extra_vars={},
        inventory="localhost,",
        options={"check": True, "tags": ["deploy"]},
        redis=redis,
    )
    mock_queue.return_value.enqueue.assert_called_once()
    call_kwargs = mock_queue.return_value.enqueue.call_args[1]["kwargs"]
    assert call_kwargs["options"] == {"check": True, "tags": ["deploy"]}
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_queue.py::test_enqueue_with_options -v`
Expected: FAIL — `enqueue_job() got unexpected keyword argument 'options'`

**Step 3: Update enqueue_job**

In `src/ansible_runner_service/queue.py`:

Update `enqueue_job()` signature — change `inventory: str` to `inventory: str | dict` and add `options: dict | None = None` parameter. Add `"options": options` to the kwargs dict.

Also update import to include `InventoryConfig`:
```python
from ansible_runner_service.schemas import SourceConfig, InventoryConfig
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_queue.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/queue.py tests/test_queue.py
git commit -m "feat: update enqueue_job to pass options to worker"
```

---

### Task 8: Update runner.py to accept execution options

**Files:**
- Modify: `src/ansible_runner_service/runner.py:17-50`
- Test: `tests/test_runner.py`

**Step 1: Write failing tests**

Add to `tests/test_runner.py`:

```python
def test_run_playbook_with_check_mode(self, mock_runner, tmp_path):
    playbook = tmp_path / "test.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []")

    run_playbook(
        playbook=str(playbook),
        extra_vars={},
        inventory="localhost,",
        options={"check": True, "diff": True},
    )
    call_kwargs = mock_runner.call_args[1]
    assert call_kwargs["cmdline"] == "--check --diff"

def test_run_playbook_with_tags(self, mock_runner, tmp_path):
    playbook = tmp_path / "test.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []")

    run_playbook(
        playbook=str(playbook),
        extra_vars={},
        inventory="localhost,",
        options={"tags": ["deploy", "config"], "verbosity": 2},
    )
    call_kwargs = mock_runner.call_args[1]
    assert call_kwargs["tags"] == "deploy,config"
    assert call_kwargs["verbosity"] == 2

def test_run_playbook_with_limit(self, mock_runner, tmp_path):
    playbook = tmp_path / "test.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []")

    run_playbook(
        playbook=str(playbook),
        extra_vars={},
        inventory="localhost,",
        options={"limit": "webservers", "skip_tags": ["debug"]},
    )
    call_kwargs = mock_runner.call_args[1]
    assert call_kwargs["limit"] == "webservers"
    assert call_kwargs["skip_tags"] == "debug"

def test_run_playbook_without_options(self, mock_runner, tmp_path):
    """Backward compat — no options param."""
    playbook = tmp_path / "test.yml"
    playbook.write_text("---\n- hosts: all\n  tasks: []")

    run_playbook(
        playbook=str(playbook),
        extra_vars={},
        inventory="localhost,",
    )
    call_kwargs = mock_runner.call_args[1]
    assert "cmdline" not in call_kwargs
    assert "tags" not in call_kwargs
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_runner.py -v`
Expected: FAIL

**Step 3: Update run_playbook**

In `src/ansible_runner_service/runner.py`, update the function signature and body:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_runner.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/runner.py tests/test_runner.py
git commit -m "feat: add execution options support to run_playbook"
```

---

### Task 9: Update worker to handle structured inventory and options

**Files:**
- Modify: `src/ansible_runner_service/worker.py:43-50` (_execute_local)
- Modify: `src/ansible_runner_service/worker.py:53-86` (_execute_git_playbook)
- Modify: `src/ansible_runner_service/worker.py:89-121` (_execute_git_role)
- Modify: `src/ansible_runner_service/worker.py:124-180` (execute_job)
- Test: `tests/test_worker.py`

**Step 1: Write failing tests for inline inventory**

Add to `tests/test_worker.py`:

```python
class TestInlineInventory:
    def test_inline_inventory_writes_yaml(self, mock_run_playbook, mock_redis, mock_db):
        """Inline inventory dict is written as YAML file."""
        from ansible_runner_service.worker import execute_job

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

        call_kwargs = mock_run_playbook.call_args[1]
        # inventory should be a file path, not the dict
        assert isinstance(call_kwargs["inventory"], str)
        assert call_kwargs["inventory"].endswith(".yml")

    def test_string_inventory_passed_through(self, mock_run_playbook, mock_redis, mock_db):
        """String inventory passed directly to runner."""
        from ansible_runner_service.worker import execute_job

        execute_job(
            job_id="str-test",
            playbook="test.yml",
            extra_vars={},
            inventory="localhost,",
        )

        call_kwargs = mock_run_playbook.call_args[1]
        assert call_kwargs["inventory"] == "localhost,"


class TestExecutionOptions:
    def test_options_passed_to_runner(self, mock_run_playbook, mock_redis, mock_db):
        """Options dict is forwarded to run_playbook."""
        from ansible_runner_service.worker import execute_job

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
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_worker.py::TestInlineInventory tests/test_worker.py::TestExecutionOptions -v`
Expected: FAIL

**Step 3: Implement worker changes**

In `src/ansible_runner_service/worker.py`:

Add `import yaml` at the top.

Add a helper function after imports:

```python
def _resolve_inventory(inventory: str | dict, tmpdir: str) -> str:
    """Resolve inventory to a string or file path for ansible-runner."""
    if isinstance(inventory, str):
        return inventory

    if inventory["type"] == "inline":
        inventory_path = os.path.join(tmpdir, "inventory.yml")
        with open(inventory_path, "w") as f:
            yaml.dump(inventory["data"], f, default_flow_style=False)
        return inventory_path

    if inventory["type"] == "git":
        providers = load_providers()
        provider = validate_repo_url(inventory["repo"], providers)
        repo_dir = os.path.join(tmpdir, "inventory_repo")
        clone_repo(
            repo_url=inventory["repo"],
            branch=inventory.get("branch", "main"),
            target_dir=repo_dir,
            provider=provider,
        )
        inv_path = os.path.join(repo_dir, inventory["path"])
        resolved = Path(inv_path).resolve()
        repo_root = Path(repo_dir).resolve()
        if not resolved.is_relative_to(repo_root):
            raise RuntimeError("Inventory path resolves outside repo directory")
        if not resolved.exists():
            raise RuntimeError(f"Inventory path not found: {inventory['path']}")
        return str(resolved)

    raise ValueError(f"Unknown inventory type: {inventory['type']}")
```

Update `_execute_local()`, `_execute_git_playbook()`, `_execute_git_role()` to accept and forward `options: dict | None = None` parameter to `run_playbook()`.

Update `execute_job()` signature:

```python
def execute_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str | dict,  # was: str
    options: dict | None = None,  # new
    source_config: SourceConfig | None = None,
) -> None:
```

In the body, resolve inventory before dispatch:

```python
# Resolve inventory (inline → YAML file, git → cloned path, string → pass through)
with tempfile.TemporaryDirectory() as inv_tmpdir:
    resolved_inventory = _resolve_inventory(inventory, inv_tmpdir)

    try:
        if source_config is None:
            result = _execute_local(playbook, extra_vars, resolved_inventory, options)
        elif source_config["type"] == "playbook":
            result = _execute_git_playbook(source_config, extra_vars, resolved_inventory, options)
        elif source_config["type"] == "role":
            result = _execute_git_role(source_config, extra_vars, resolved_inventory, options)
        ...
```

Note: The `inv_tmpdir` context needs to wrap the entire execution block so the inventory file exists while ansible-runner runs. Consider whether to use a separate tmpdir or the existing one in `_execute_git_playbook`. The simplest approach: resolve inventory into a tmpdir that stays alive through the entire execution, then nest the source-specific tmpdirs inside.

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_worker.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/worker.py tests/test_worker.py
git commit -m "feat: handle structured inventory and options in worker"
```

---

### Task 10: Update API route to pass inventory and options through

**Files:**
- Modify: `src/ansible_runner_service/main.py:134-185` (_handle_local_source)
- Modify: `src/ansible_runner_service/main.py:188-254` (_handle_git_source)
- Test: `tests/test_api.py`

**Step 1: Write failing tests**

Add to `tests/test_api.py`:

```python
class TestSubmitWithInventoryAndOptions:
    def test_inline_inventory_accepted(self, client, playbooks_dir):
        (playbooks_dir / "test.yml").write_text("---\n- hosts: all\n  tasks: []")
        response = client.post("/api/v1/jobs", json={
            "playbook": "test.yml",
            "inventory": {
                "type": "inline",
                "data": {"webservers": {"hosts": {"10.0.1.10": None}}},
            },
        })
        assert response.status_code == 202

    def test_options_accepted(self, client, playbooks_dir):
        (playbooks_dir / "test.yml").write_text("---\n- hosts: all\n  tasks: []")
        response = client.post("/api/v1/jobs", json={
            "playbook": "test.yml",
            "options": {"check": True, "tags": ["deploy"]},
        })
        assert response.status_code == 202

    def test_string_inventory_still_works(self, client, playbooks_dir):
        (playbooks_dir / "test.yml").write_text("---\n- hosts: all\n  tasks: []")
        response = client.post("/api/v1/jobs", json={
            "playbook": "test.yml",
            "inventory": "myhost,",
        })
        assert response.status_code == 202

    def test_invalid_inventory_type_rejected(self, client, playbooks_dir):
        (playbooks_dir / "test.yml").write_text("---\n- hosts: all\n  tasks: []")
        response = client.post("/api/v1/jobs", json={
            "playbook": "test.yml",
            "inventory": {"type": "invalid"},
        })
        assert response.status_code == 422
```

**Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_api.py::TestSubmitWithInventoryAndOptions -v`
Expected: FAIL

**Step 3: Update API route handlers**

In `src/ansible_runner_service/main.py`:

Update `_handle_local_source()` to serialize inventory and pass options:

```python
# Convert inventory for storage and queue
inventory = request.inventory
if not isinstance(inventory, str):
    inventory = inventory.model_dump()

options = request.options.model_dump(exclude_defaults=True) or None
```

Pass `inventory` (now str or dict) and `options` to `job_store.create_job()`, `enqueue_job()`, and `run_playbook()` (for sync mode).

Apply the same pattern to `_handle_git_source()`.

For sync mode `run_playbook()` calls, pass `options` as a dict:
```python
result = run_playbook(
    playbook=request.playbook,
    extra_vars=request.extra_vars,
    inventory=resolved_inventory,  # resolve inline here for sync
    playbooks_dir=playbooks_dir,
    options=request.options.model_dump(exclude_defaults=True) or None,
)
```

Note: For sync mode with inline inventory, you'll need to resolve it before calling `run_playbook()`. Either extract `_resolve_inventory` to a shared utility or handle it inline.

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_api.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "feat: wire inventory and options through API route handlers"
```

---

### Task 11: Full test suite verification

**Files:**
- All test files

**Step 1: Run full test suite**

Run: `source .venv/bin/activate && pytest tests/ -v`
Expected: ALL PASS — no regressions

**Step 2: Fix any failures**

If any tests fail, fix them. Most likely causes:
- Existing tests that mock `inventory` as a string where the function signature changed
- Test fixtures that need updating for new parameters

**Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve test regressions from JobRequest redesign"
```

---

### Task 12: Integration test — inline inventory with groups and host_vars

**Files:**
- Modify: `tests/test_integration.py`

**Step 1: Write integration test**

```python
class TestInlineInventoryIntegration:
    def test_inline_inventory_with_groups(self, client, playbooks_dir):
        """Full flow: inline inventory → YAML file → ansible-runner → success."""
        (playbooks_dir / "ping.yml").write_text(
            "---\n- hosts: all\n  gather_facts: false\n  tasks:\n"
            "    - ping:\n"
        )

        response = client.post("/api/v1/jobs?sync=true", json={
            "playbook": "ping.yml",
            "inventory": {
                "type": "inline",
                "data": {
                    "local": {
                        "hosts": {
                            "localhost": {"ansible_connection": "local"},
                        },
                    },
                },
            },
        })
        assert response.status_code == 200
        data = response.json()
        assert data["rc"] == 0
```

**Step 2: Run integration test**

Run: `source .venv/bin/activate && pytest tests/test_integration.py::TestInlineInventoryIntegration -v`
Expected: PASS (requires Redis + MariaDB running)

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test for inline inventory with groups"
```
