# Ansible Runner Service

REST API for running Ansible playbooks via FastAPI + Redis + MariaDB.

## Features

- **Sync and async execution** - Run local playbooks immediately (`?sync=true`) or queue for background processing
- **Git-based sources** - Execute playbooks and roles directly from Git repositories
- **Structured inventory** - Pass Ansible YAML inventory as JSON or fetch from Git
- **Execution options** - Support for check mode, diff, tags, limit, verbosity
- **Job persistence** - Jobs stored in MariaDB with Redis caching for fast access
- **Provider allowlist** - Restrict Git sources to approved hosts and organizations

## Quick Start

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
docker-compose up -d
alembic upgrade head

# Start API server
uvicorn ansible_runner_service.main:app --reload

# Start worker (separate terminal)
source .venv/bin/activate
rq worker
```

## API Examples

### Run a local playbook (sync)

```bash
curl -X POST "http://localhost:8000/api/v1/jobs?sync=true" \
  -H "Content-Type: application/json" \
  -d '{"playbook": "hello.yml"}'
```

### Submit async job with options

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "playbook": "deploy.yml",
    "extra_vars": {"env": "prod"},
    "inventory": {"type": "inline", "data": {"webservers": {"hosts": {"10.0.1.10": null}}}},
    "options": {"check": true, "diff": true, "tags": ["deploy"]}
  }'
```

### Run playbook from Git

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "playbook",
      "repo": "https://dev.azure.com/org/project/_git/ansible-playbooks",
      "path": "deploy/app.yml"
    },
    "inventory": "localhost,"
  }'
```

## Job Request Fields

| Field | Type | Description |
|-------|------|-------------|
| `playbook` | string | Local playbook name (required if no `source`) |
| `source` | object | Git source config (required if no `playbook`) |
| `extra_vars` | object | Variables passed to playbook |
| `inventory` | string or object | Host list, inline YAML, or git reference |
| `options` | object | Execution options (check, diff, tags, etc.) |

## Documentation

See [docs/usage-guide.md](docs/usage-guide.md) for complete documentation including:

- Setup and configuration
- All API endpoints and responses
- Sync vs async mode support matrix
- Git provider configuration
- Structured inventory formats
- Execution options reference
- Testing and troubleshooting

## Running Tests

```bash
docker-compose up -d
alembic upgrade head
pytest tests/ -v
```

## License

MIT
