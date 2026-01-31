# Ansible Runner Service - Usage Guide

## Prerequisites

- Python 3.11+
- Docker and Docker Compose
- Ansible installed locally (for playbook execution)

## Setup

### 1. Create and activate virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -e ".[dev]"
```

### 3. Start Redis and MariaDB

```bash
docker-compose up -d
```

Verify services are running:
```bash
docker-compose ps
# Should show:
#   redis    running (healthy)
#   mariadb  running (healthy)
```

### 4. Run database migrations

```bash
alembic upgrade head
```

This creates the required tables in MariaDB.

## Running the Service

### Start the API server

```bash
source .venv/bin/activate
uvicorn ansible_runner_service.main:app --reload
```

The API will be available at `http://localhost:8000`.

### Start the worker (for async jobs)

In a separate terminal:
```bash
source .venv/bin/activate
rq worker --url redis://localhost:6379
```

## API Usage

### Sync Mode (Immediate Execution)

Execute a playbook and wait for the result:

```bash
curl -X POST "http://localhost:8000/api/v1/jobs?sync=true" \
  -H "Content-Type: application/json" \
  -d '{"playbook": "hello.yml"}'
```

Response (200 OK):
```json
{
  "status": "successful",
  "rc": 0,
  "stdout": "...",
  "stats": {"ok": {"localhost": 1}, "changed": {}, "failures": {}}
}
```

### Async Mode (Queue for Background Execution)

Submit a job to the queue:

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{"playbook": "hello.yml"}'
```

Response (202 Accepted):
```json
{
  "job_id": "abc123-...",
  "status": "pending",
  "created_at": "2026-01-21T10:00:00+00:00"
}
```

Poll for job status:

```bash
curl "http://localhost:8000/api/v1/jobs/{job_id}"
```

Response (200 OK):
```json
{
  "job_id": "abc123-...",
  "status": "successful",
  "playbook": "hello.yml",
  "created_at": "2026-01-21T10:00:00+00:00",
  "started_at": "2026-01-21T10:00:01+00:00",
  "finished_at": "2026-01-21T10:00:05+00:00",
  "result": {
    "rc": 0,
    "stdout": "...",
    "stats": {"ok": {"localhost": 1}, "changed": {}, "failures": {}}
  },
  "error": null
}
```

### With Extra Variables

```bash
curl -X POST "http://localhost:8000/api/v1/jobs?sync=true" \
  -H "Content-Type: application/json" \
  -d '{
    "playbook": "hello.yml",
    "extra_vars": {"name": "Claude"},
    "inventory": "localhost,"
  }'
```

### Git Playbook Source

Execute a playbook from a Git repository (async only):

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "playbook",
      "repo": "https://dev.azure.com/xxxit/project/_git/ansible-playbooks",
      "branch": "main",
      "path": "deploy/app.yml"
    },
    "extra_vars": {"env": "prod"},
    "inventory": "localhost,"
  }'
```

### Git Role Source

Execute an Ansible role from a collection in a Git repository:

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "role",
      "repo": "https://gitlab.company.com/platform-team/ansible-collection.git",
      "branch": "v2.0.0",
      "role": "nginx",
      "role_vars": {"nginx_port": 8080}
    },
    "inventory": "webservers,"
  }'
```

The role name can be a short name (e.g., `nginx`) or a fully qualified collection name (e.g., `mycompany.infra.nginx`). Short names are automatically resolved using the collection's `galaxy.yml`.

### Configuring Git Providers

Git sources require provider configuration. Set the `GIT_PROVIDERS` environment variable:

```bash
export GIT_PROVIDERS='[
  {"type": "azure", "host": "dev.azure.com", "orgs": ["xxxit"], "credential_env": "AZURE_PAT"},
  {"type": "gitlab", "host": "gitlab.company.com", "orgs": ["platform-team"], "credential_env": "GITLAB_TOKEN"}
]'
export AZURE_PAT="your-azure-pat-token"
export GITLAB_TOKEN="your-gitlab-access-token"
```

See `config/git_providers.example.yaml` for a full example.

## Job Statuses

| Status | Description |
|--------|-------------|
| `pending` | Job queued, waiting for worker |
| `running` | Worker is executing the playbook |
| `successful` | Playbook completed with rc=0 |
| `failed` | Playbook failed or error occurred |

## API Documentation

Interactive API docs available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Running Tests

### All tests (unit + integration)

Requires Redis and MariaDB running with migrations applied:

```bash
docker-compose up -d
alembic upgrade head
pytest tests/ -v
```

### Unit tests only (no Redis or MariaDB required)

```bash
pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_db_integration.py --ignore=tests/test_queue_integration.py
```

### Integration tests only

```bash
pytest tests/test_integration.py -v -m integration
```

### E2E tests (require running rq worker)

```bash
# Terminal 1: Start rq worker
rq worker --url redis://localhost:6379

# Terminal 2: Run E2E tests
pytest tests/test_integration.py -v -m "integration and e2e"
```

## Inspecting the Database

After running docker-compose and executing tests or API requests, you can inspect the MariaDB database content.

### Connect to MariaDB CLI

```bash
# Connect to the main database
docker-compose exec mariadb mariadb -uroot -pdevpassword ansible_runner

# Or connect to the test database
docker-compose exec mariadb mariadb -uroot -pdevpassword ansible_runner_test
```

### Useful SQL Queries

Once connected to the MariaDB CLI:

```sql
-- List all jobs
SELECT id, status, playbook, created_at, finished_at FROM jobs;

-- View full job details (vertical format)
SELECT * FROM jobs WHERE id = 'your-job-id' \G

-- Check recent jobs
SELECT id, status, playbook, result_rc FROM jobs ORDER BY created_at DESC LIMIT 10;

-- View stats for completed jobs
SELECT id, result_stats FROM jobs WHERE result_stats IS NOT NULL;

-- Find failed jobs
SELECT id, playbook, error FROM jobs WHERE status = 'failed';
```

### One-liner from Shell

Run queries directly without entering the MariaDB CLI:

```bash
# Quick check of jobs table
docker-compose exec mariadb mariadb -uroot -pdevpassword ansible_runner \
  -e "SELECT id, status, playbook FROM jobs;"

# Check job count by status
docker-compose exec mariadb mariadb -uroot -pdevpassword ansible_runner \
  -e "SELECT status, COUNT(*) FROM jobs GROUP BY status;"
```

## Project Structure

```
.
├── docker-compose.yml          # Redis + MariaDB containers
├── docker/
│   └── init-test-db.sql        # Creates ansible_runner_test DB on first start
├── playbooks/                  # Ansible playbooks
│   └── hello.yml
├── alembic/                    # Database migrations
│   └── versions/
├── alembic.ini                 # Alembic configuration
├── config/
│   └── git_providers.example.yaml  # Example Git provider config
├── src/ansible_runner_service/
│   ├── main.py                 # FastAPI app and endpoints
│   ├── runner.py               # Ansible runner wrapper
│   ├── schemas.py              # Pydantic models
│   ├── job_store.py            # Redis-backed job storage (write-through to DB)
│   ├── queue.py                # rq job enqueueing
│   ├── worker.py               # Worker job execution
│   ├── database.py             # SQLAlchemy engine and session
│   ├── models.py               # ORM models (JobModel)
│   ├── repository.py           # Database CRUD operations
│   ├── git_config.py           # Git provider configuration and URL validation
│   └── git_service.py          # Git clone, collection install, FQCN resolution
└── tests/
    ├── test_api.py             # API endpoint tests
    ├── test_integration.py     # Full flow + E2E tests (require Redis + worker)
    ├── test_db_integration.py  # Database integration tests (require MariaDB)
    ├── test_queue_integration.py # Queue integration tests (require Redis)
    ├── test_job_store.py       # Job store tests
    ├── test_queue.py           # Queue tests
    ├── test_runner.py          # Runner tests
    ├── test_schemas.py         # Schema tests
    ├── test_worker.py          # Worker tests
    ├── test_git_config.py      # Git provider config tests
    └── test_git_service.py     # Git service tests
```

## Troubleshooting

### Redis connection refused

Ensure Redis is running:
```bash
docker-compose up -d
docker-compose ps
```

### Job stays in "pending" status

Ensure the rq worker is running:
```bash
rq worker --url redis://localhost:6379
```

### Playbook not found

Playbooks must be in the `playbooks/` directory at the project root.

### Worker not picking up jobs

Check Redis connectivity:
```bash
python3 -c "import redis; r = redis.Redis(); r.ping(); print('OK')"
```
