# Ansible Runner Service

REST API for running Ansible playbooks and roles via FastAPI + Redis + MariaDB.

## Features

- **Unified source field** - Single API for local and git-based playbooks/roles
- **Sync and async execution** - Local sources run immediately (`?sync=true`) or queued; git sources always async
- **Bundled content support** - Run playbooks/roles baked into container images (Kubernetes-ready)
- **Git-based sources** - Execute playbooks and roles from Azure DevOps, GitLab, or other Git providers
- **Structured inventory** - Pass Ansible YAML inventory as JSON or fetch from Git
- **Execution options** - Support for check mode, diff, tags, limit, verbosity
- **Job persistence** - Jobs stored in MariaDB with Redis caching for fast access
- **API key authentication** - Per-client API keys with admin management endpoints
- **Provider allowlist** - Restrict Git sources to approved hosts and organizations

## Quick Start

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
docker-compose up -d
alembic upgrade head

# Set admin API key for authentication
export ADMIN_API_KEY=your-secret-admin-key

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
  -d '{"source": {"type": "local", "target": "playbook", "path": "hello.yml"}}'
```

### Run a local role from bundled collection (sync)

```bash
curl -X POST "http://localhost:8000/api/v1/jobs?sync=true" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "local",
      "target": "role",
      "collection": "mycompany.infra",
      "role": "nginx",
      "role_vars": {"port": 8080}
    },
    "inventory": "webservers,"
  }'
```

### Submit async job with options

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {"type": "local", "target": "playbook", "path": "deploy.yml"},
    "extra_vars": {"env": "prod"},
    "inventory": {"type": "inline", "data": {"webservers": {"hosts": {"10.0.1.10": null}}}},
    "options": {"check": true, "diff": true, "tags": ["deploy"]}
  }'
```

### Run playbook from Git (async)

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "git",
      "target": "playbook",
      "repo": "https://dev.azure.com/org/project/_git/ansible-playbooks",
      "path": "deploy/app.yml"
    },
    "inventory": "localhost,"
  }'
```

### Run role from Git collection (async)

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "git",
      "target": "role",
      "repo": "https://gitlab.company.com/team/ansible-collection.git",
      "role": "nginx"
    },
    "inventory": "webservers,"
  }'
```

## Authentication

API endpoints are protected by API key authentication. Health endpoints (`/health/*`) require no authentication.

### Create a client

Use the admin API key to create a client. The plaintext key is returned once:

```bash
curl -X POST "http://localhost:8000/admin/clients" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -d '{"name": "my-app"}'
```

### Use the client key

Pass the client key in the `X-API-Key` header:

```bash
curl -X POST "http://localhost:8000/api/v1/jobs?sync=true" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ars_..." \
  -d '{"source": {"type": "local", "target": "playbook", "path": "hello.yml"}}'
```

### List and revoke clients

```bash
# List all clients
curl -H "X-Admin-Key: $ADMIN_API_KEY" "http://localhost:8000/admin/clients"

# Revoke a client
curl -X DELETE -H "X-Admin-Key: $ADMIN_API_KEY" \
  "http://localhost:8000/admin/clients/my-app"
```

To disable authentication for development, set `AUTH_ENABLED=false`. See [docs/usage-guide.md](docs/usage-guide.md) for full details.

## Job Request Fields

| Field | Type | Description |
|-------|------|-------------|
| `source` | object | **Required.** Source definition (see below) |
| `extra_vars` | object | Variables passed to playbook |
| `inventory` | string or object | Host list, inline YAML, or git reference |
| `options` | object | Execution options (check, diff, tags, etc.) |

### Source Types

| Type | Target | Fields |
|------|--------|--------|
| `local` | `playbook` | `path` |
| `local` | `role` | `collection`, `role`, `role_vars` (optional) |
| `git` | `playbook` | `repo`, `branch` (optional), `path` |
| `git` | `role` | `repo`, `branch` (optional), `role`, `role_vars` (optional) |

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
