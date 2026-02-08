# Project: ansible-runner-service

REST API for running Ansible playbooks via FastAPI + Redis + MariaDB.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
docker-compose up -d
alembic upgrade head
rq worker &
```

## Testing

Requires Setup complete.

Note: FastAPI server is NOT needed (pytest uses ASGITransport). Only start uvicorn for manual API testing.

```bash
pytest tests/ -v
```

### When to run what

- **During development:** Run relevant subset (e.g., `pytest tests/test_api.py -v`)
- **When finishing a branch:** Full suite, no `--ignore` flags. All tests must pass.

### IMPORTANT: Do not proceed if any tests fail

When finishing a branch:

1. Ensure Setup complete (services running, worker started)
2. Run `pytest tests/ -v` and verify 0 failures, 0 errors
3. If tests fail: fix them. Do NOT rationalize as "pre-existing" or "infrastructure issues"
4. Only after all tests pass: proceed with merge/PR

## Worktree Directory

.worktrees
