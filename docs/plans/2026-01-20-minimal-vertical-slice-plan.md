# Minimal Vertical Slice Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a single POST /api/v1/jobs endpoint that runs ansible-runner synchronously and returns results.

**Architecture:** FastAPI receives job request, validates playbook exists, calls ansible-runner synchronously, returns execution results. No queue, no database, no async workers.

**Tech Stack:** FastAPI, uvicorn, ansible-runner, pytest, httpx

---

## Task 1: Project Setup

**Files:**
- Create: `pyproject.toml`
- Create: `src/ansible_runner_service/__init__.py`

**Step 1: Create pyproject.toml**

```toml
[project]
name = "ansible-runner-service"
version = "0.1.0"
description = "REST API for running Ansible playbooks"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn>=0.27.0",
    "ansible-runner>=2.3.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.26.0",
]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: Create package init file**

```bash
mkdir -p src/ansible_runner_service
touch src/ansible_runner_service/__init__.py
```

**Step 3: Create virtual environment and install dependencies**

Run: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`

**Step 4: Verify installation**

Run: `python -c "import fastapi; import ansible_runner; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add pyproject.toml src/
git commit -m "feat: initialize project with FastAPI and ansible-runner dependencies"
```

---

## Task 2: Test Playbook

**Files:**
- Create: `playbooks/hello.yml`

**Step 1: Create playbooks directory**

```bash
mkdir -p playbooks
```

**Step 2: Create test playbook**

```yaml
---
- name: Hello World
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Say hello
      ansible.builtin.debug:
        msg: "Hello, {{ name | default('World') }}!"
```

**Step 3: Verify playbook runs manually**

Run: `ansible-playbook playbooks/hello.yml -e "name=Test"`
Expected: Output contains `Hello, Test!`

**Step 4: Commit**

```bash
git add playbooks/
git commit -m "feat: add hello.yml test playbook for localhost"
```

---

## Task 3: Pydantic Schemas

**Files:**
- Create: `src/ansible_runner_service/schemas.py`
- Create: `tests/test_schemas.py`

**Step 1: Write the failing test**

```python
# tests/test_schemas.py
import pytest
from pydantic import ValidationError

from ansible_runner_service.schemas import JobRequest, JobResponse


class TestJobRequest:
    def test_minimal_request(self):
        req = JobRequest(playbook="hello.yml")
        assert req.playbook == "hello.yml"
        assert req.extra_vars == {}
        assert req.inventory == "localhost,"

    def test_full_request(self):
        req = JobRequest(
            playbook="hello.yml",
            extra_vars={"name": "World"},
            inventory="myhost,",
        )
        assert req.extra_vars == {"name": "World"}
        assert req.inventory == "myhost,"

    def test_empty_playbook_rejected(self):
        with pytest.raises(ValidationError):
            JobRequest(playbook="")


class TestJobResponse:
    def test_response_creation(self):
        resp = JobResponse(
            status="successful",
            rc=0,
            stdout="PLAY [Hello]...",
            stats={"localhost": {"ok": 1, "changed": 0, "failures": 0}},
        )
        assert resp.status == "successful"
        assert resp.rc == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ansible_runner_service.schemas'`

**Step 3: Write minimal implementation**

```python
# src/ansible_runner_service/schemas.py
from typing import Any

from pydantic import BaseModel, Field


class JobRequest(BaseModel):
    playbook: str = Field(..., min_length=1)
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str = "localhost,"


class JobResponse(BaseModel):
    status: str
    rc: int
    stdout: str
    stats: dict[str, Any]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/schemas.py tests/test_schemas.py
git commit -m "feat: add JobRequest and JobResponse Pydantic schemas"
```

---

## Task 4: Runner Wrapper

**Files:**
- Create: `src/ansible_runner_service/runner.py`
- Create: `tests/test_runner.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ansible_runner_service.runner'`

**Step 3: Write minimal implementation**

```python
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
    playbooks_dir: Path,
) -> RunResult:
    """Run an Ansible playbook synchronously and return results."""
    playbook_path = playbooks_dir / playbook

    with tempfile.TemporaryDirectory() as tmpdir:
        runner = ansible_runner.run(
            private_data_dir=tmpdir,
            playbook=str(playbook_path),
            inventory=inventory,
            extravars=extra_vars,
            quiet=False,
        )

        stdout = runner.stdout.read() if runner.stdout else ""

        return RunResult(
            status=runner.status,
            rc=runner.rc,
            stdout=stdout,
            stats=runner.stats or {},
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/runner.py tests/test_runner.py
git commit -m "feat: add ansible-runner wrapper with RunResult dataclass"
```

---

## Task 5: FastAPI Endpoint

**Files:**
- Create: `src/ansible_runner_service/main.py`
- Create: `tests/test_api.py`

**Step 1: Write the failing test for happy path**

```python
# tests/test_api.py
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from ansible_runner_service.main import app, get_playbooks_dir


# Override playbooks directory for tests
@pytest.fixture
def playbooks_dir(tmp_path: Path):
    # Create test playbook
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
def client(playbooks_dir: Path):
    app.dependency_overrides[get_playbooks_dir] = lambda: playbooks_dir
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


class TestPostJobs:
    async def test_successful_job(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "hello.yml"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "successful"
        assert data["rc"] == 0
        assert "Hello, World!" in data["stdout"]

    async def test_with_extra_vars(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "hello.yml", "extra_vars": {"name": "Claude"}},
        )

        assert response.status_code == 200
        assert "Hello, Claude!" in response.json()["stdout"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py::TestPostJobs::test_successful_job -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ansible_runner_service.main'`

**Step 3: Write minimal implementation**

```python
# src/ansible_runner_service/main.py
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from ansible_runner_service.runner import run_playbook
from ansible_runner_service.schemas import JobRequest, JobResponse

app = FastAPI(title="Ansible Runner Service")

PLAYBOOKS_DIR = Path(__file__).parent.parent.parent.parent / "playbooks"


def get_playbooks_dir() -> Path:
    """Dependency for playbooks directory (allows test override)."""
    return PLAYBOOKS_DIR


@app.post("/api/v1/jobs", response_model=JobResponse)
def submit_job(
    request: JobRequest,
    playbooks_dir: Path = Depends(get_playbooks_dir),
) -> JobResponse:
    """Submit a playbook job for execution."""
    playbook_path = playbooks_dir / request.playbook

    if not playbook_path.exists():
        raise HTTPException(status_code=404, detail=f"Playbook not found: {request.playbook}")

    result = run_playbook(
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
        playbooks_dir=playbooks_dir,
    )

    return JobResponse(
        status=result.status,
        rc=result.rc,
        stdout=result.stdout,
        stats=result.stats,
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_api.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "feat: add POST /api/v1/jobs endpoint with FastAPI"
```

---

## Task 6: Error Handling Tests

**Files:**
- Modify: `tests/test_api.py`
- Modify: `src/ansible_runner_service/main.py`

**Step 1: Write the failing test for missing playbook**

Add to `tests/test_api.py`:

```python
    async def test_playbook_not_found(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "nonexistent.yml"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
```

**Step 2: Run test to verify it passes (already implemented)**

Run: `pytest tests/test_api.py::TestPostJobs::test_playbook_not_found -v`
Expected: PASS (404 handling already in main.py)

**Step 3: Write the failing test for path traversal**

Add to `tests/test_api.py`:

```python
    async def test_path_traversal_blocked(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/jobs",
            json={"playbook": "../etc/passwd"},
        )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()
```

**Step 4: Run test to verify it fails**

Run: `pytest tests/test_api.py::TestPostJobs::test_path_traversal_blocked -v`
Expected: FAIL (currently returns 404, not 400)

**Step 5: Add path traversal validation to main.py**

Update `submit_job` in `src/ansible_runner_service/main.py`:

```python
@app.post("/api/v1/jobs", response_model=JobResponse)
def submit_job(
    request: JobRequest,
    playbooks_dir: Path = Depends(get_playbooks_dir),
) -> JobResponse:
    """Submit a playbook job for execution."""
    # Block path traversal attempts
    if ".." in request.playbook or request.playbook.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid playbook name")

    playbook_path = playbooks_dir / request.playbook

    if not playbook_path.exists():
        raise HTTPException(status_code=404, detail=f"Playbook not found: {request.playbook}")

    result = run_playbook(
        playbook=request.playbook,
        extra_vars=request.extra_vars,
        inventory=request.inventory,
        playbooks_dir=playbooks_dir,
    )

    return JobResponse(
        status=result.status,
        rc=result.rc,
        stdout=result.stdout,
        stats=result.stats,
    )
```

**Step 6: Run test to verify it passes**

Run: `pytest tests/test_api.py -v`
Expected: PASS (4 tests)

**Step 7: Commit**

```bash
git add src/ansible_runner_service/main.py tests/test_api.py
git commit -m "feat: add path traversal protection and error handling tests"
```

---

## Task 7: Manual Verification

**Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass (7+ tests)

**Step 2: Start the server**

Run: `uvicorn ansible_runner_service.main:app --reload`
Expected: Server starts on http://127.0.0.1:8000

**Step 3: Test with curl**

Run:
```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"playbook": "hello.yml", "extra_vars": {"name": "World"}}'
```
Expected: JSON response with `"status": "successful"` and `"Hello, World!"` in stdout

**Step 4: Test error cases**

Run:
```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"playbook": "nonexistent.yml"}'
```
Expected: 404 response with "Playbook not found"

**Step 5: Check OpenAPI docs**

Visit: http://localhost:8000/docs
Expected: Interactive API documentation with POST /api/v1/jobs endpoint

**Step 6: Final commit**

```bash
git add -A
git commit -m "docs: verify minimal vertical slice implementation complete"
```

---

## Summary

After completing all tasks, you will have:

- `pyproject.toml` - Project configuration with dependencies
- `src/ansible_runner_service/` - Python package with:
  - `schemas.py` - Pydantic request/response models
  - `runner.py` - ansible-runner wrapper
  - `main.py` - FastAPI application
- `playbooks/hello.yml` - Test playbook
- `tests/` - Test suite with 7+ tests covering:
  - Schema validation
  - Runner execution
  - API happy path
  - Error handling (404, 400)

Total commits: 7
