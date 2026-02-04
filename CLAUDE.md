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

## Worktree Directory

.worktrees
