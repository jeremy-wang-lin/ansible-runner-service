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

### 3. Start Redis

```bash
docker-compose up -d
```

Verify Redis is running:
```bash
docker-compose ps
# Should show: async-job-queue-redis-1 running (healthy)
```

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
  "stats": {"localhost": {"ok": 1}}
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
    "stats": {"localhost": {"ok": 1}}
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

Requires Redis to be running:

```bash
docker-compose up -d
pytest tests/ -v
```

### Unit tests only (no Redis required)

```bash
pytest tests/ -v --ignore=tests/test_integration.py
```

### Integration tests only

```bash
pytest tests/test_integration.py -v -m integration
```

## Project Structure

```
.
├── docker-compose.yml          # Redis container
├── playbooks/                  # Ansible playbooks
│   └── hello.yml
├── src/ansible_runner_service/
│   ├── main.py                 # FastAPI app and endpoints
│   ├── runner.py               # Ansible runner wrapper
│   ├── schemas.py              # Pydantic models
│   ├── job_store.py            # Redis-backed job storage
│   ├── queue.py                # rq job enqueueing
│   └── worker.py               # Worker job execution
└── tests/
    ├── test_api.py             # API endpoint tests
    ├── test_integration.py     # Full flow tests (require Redis)
    ├── test_job_store.py       # Job store tests
    ├── test_queue.py           # Queue tests
    ├── test_runner.py          # Runner tests
    ├── test_schemas.py         # Schema tests
    └── test_worker.py          # Worker tests
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
