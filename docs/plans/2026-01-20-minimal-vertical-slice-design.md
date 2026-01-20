# Minimal Vertical Slice - Design Document

## Overview

**Goal:** Prove FastAPI can trigger ansible-runner and return results.

**Scope:**
- One endpoint: `POST /api/v1/jobs`
- Accept a playbook name and optional variables
- Run synchronously via ansible-runner against localhost
- Return execution results in the response
- No Redis, no database, no async workers

## Directory Structure

```
src/
  ansible_runner_service/
    __init__.py
    main.py           # FastAPI app + endpoint
    runner.py         # ansible-runner wrapper
    schemas.py        # Pydantic models
playbooks/
  hello.yml           # Test playbook (debug msg)
tests/
  test_api.py         # Basic API tests
pyproject.toml        # Dependencies
```

## Dependencies

**`pyproject.toml`:**
```toml
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn>=0.27.0",
    "ansible-runner>=2.3.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0.0", "httpx>=0.26.0"]
```

**Prerequisites:** Ansible must be installed (`pip install ansible` or system package).

## API Design

### Endpoint: `POST /api/v1/jobs`

**Request schema:**
```python
class JobRequest(BaseModel):
    playbook: str                      # e.g., "hello.yml"
    extra_vars: dict[str, Any] = {}    # Optional variables
    inventory: str = "localhost,"      # Default to localhost
```

**Response schema:**
```python
class JobResponse(BaseModel):
    status: str          # "successful", "failed", "canceled"
    rc: int              # Return code (0 = success)
    stdout: str          # Playbook output
    stats: dict          # Host stats (ok, changed, failed counts)
```

**Error handling:**
- `400 Bad Request` - Invalid playbook name or malformed request
- `404 Not Found` - Playbook file doesn't exist
- `500 Internal Server Error` - ansible-runner execution failure

**Validation rules:**
- `playbook` must be a filename (no path traversal like `../`)
- `playbook` must exist in the `playbooks/` directory
- `extra_vars` values must be JSON-serializable

**Example:**
```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"playbook": "hello.yml", "extra_vars": {"name": "World"}}'
```

## ansible-runner Integration

**Wrapper function in `runner.py`:**
```python
def run_playbook(
    playbook: str,
    extra_vars: dict,
    inventory: str,
    playbooks_dir: Path
) -> RunResult
```

**How ansible-runner works:**
- `ansible_runner.run()` expects a directory structure with `project/` containing playbooks
- We'll point it at our `playbooks/` directory
- It returns a `Runner` object with `status`, `rc`, `stdout`, and `stats`

**Our approach:**
- Use `playbook_path` parameter to point directly at our playbook file
- Pass `inventory` as a string (e.g., `"localhost,"`)
- Set `quiet=False` to capture stdout
- Run in a temp directory for artifacts to avoid clutter

**Result extraction:**
```python
@dataclass
class RunResult:
    status: str      # runner.status
    rc: int          # runner.rc
    stdout: str      # runner.stdout.read()
    stats: dict      # runner.stats
```

**Key considerations:**
- ansible-runner needs `ansible` installed
- Localhost execution needs `connection: local` in the playbook

## Test Playbook

**`playbooks/hello.yml`:**
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

**Why this playbook:**
- `connection: local` - No SSH needed
- `gather_facts: false` - Faster execution
- `debug` module - Safe, no side effects
- Accepts `name` variable - Proves extra_vars work

## Testing Strategy

**Test cases (`tests/test_api.py`):**

1. **Happy path** - Submit hello.yml, verify status=successful
2. **With extra_vars** - Pass `{"name": "Claude"}`, verify in output
3. **Missing playbook** - Request nonexistent.yml, expect 404
4. **Path traversal blocked** - Request `../etc/passwd`, expect 400
5. **Invalid JSON** - Malformed request body, expect 422

**Testing approach:**
- Use `pytest` with `httpx.AsyncClient` for FastAPI testing
- No mocking of ansible-runner (integration test)
- Tests actually run the playbook (fast since it's just debug)

**Running tests:**
```bash
pytest tests/ -v
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────┐
│  POST /api/v1/jobs                                  │
│  {"playbook": "hello.yml", "extra_vars": {...}}     │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│  FastAPI (main.py)                                  │
│  - Validate request (schemas.py)                    │
│  - Check playbook exists                            │
│  - Call runner.run_playbook()                       │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│  ansible-runner (runner.py)                         │
│  - Execute playbook synchronously                   │
│  - Capture stdout, status, stats                    │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│  Response                                           │
│  {"status": "successful", "rc": 0, "stdout": "..."}│
└─────────────────────────────────────────────────────┘
```
