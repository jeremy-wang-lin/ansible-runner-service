# Project: ansible-runner-service

REST API for running Ansible playbooks via FastAPI + Redis + MariaDB.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
docker-compose up -d
alembic upgrade head
```

## Testing

### Full test suite

```bash
pytest tests/ -v
```

Requires Redis and MariaDB running (`docker-compose up -d`) with migrations applied (`alembic upgrade head`).

### When to run what

- **During development:** Run the relevant subset of tests for the code you're changing (e.g., `pytest tests/test_api.py -v`).
- **When finishing a branch (superpowers:finishing-a-development-branch):** Run the full test suite with no `--ignore` flags. All tests must pass before merge or PR.

### IMPORTANT: All tests must actually pass

When finishing a branch, **do not proceed if any tests fail or error**. This includes:

1. **Start all required services first:**
   ```bash
   docker-compose up -d          # Start Redis + MariaDB
   alembic upgrade head          # Apply migrations
   rq worker &                   # Start worker for E2E tests
   ```
   Note: The FastAPI server is NOT needed for tests (pytest uses ASGITransport to test the app directly). Only start uvicorn for manual API testing.

2. **Run full test suite and verify 0 failures, 0 errors:**
   ```bash
   pytest tests/ -v
   ```

3. **If tests fail:** Fix them before proceeding. Do NOT rationalize failures as "pre-existing" or "infrastructure issues" - either fix them or start the required services.

4. **Only after all tests pass:** Proceed with merge/PR options.

## Worktree Directory

.worktrees
