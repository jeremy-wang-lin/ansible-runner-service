# Async Job Queue Design

## Overview

**Goal:** Convert synchronous job execution to async with Redis + rq.

**Scope:**
- `POST /api/v1/jobs` defaults to async (returns job_id immediately)
- `POST /api/v1/jobs?sync=true` for synchronous execution (backwards compatible)
- `GET /api/v1/jobs/{id}` for polling job status
- Redis for job queue (rq) and job state storage
- No database - job state stored in Redis

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      docker-compose                          │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                       Redis                            │  │
│  │  - Job queue (rq)                                      │  │
│  │  - Job state storage (hash per job)                    │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
        ▲                                    ▲
        │ enqueue                            │ dequeue
        │ read/write state                   │ read/write state
┌───────┴──────────┐                ┌────────┴─────────┐
│     FastAPI      │                │    rq Worker     │
│  POST /jobs      │                │  run_playbook()  │
│  GET /jobs/{id}  │                │  update status   │
└──────────────────┘                └──────────────────┘
```

**Job lifecycle:**
1. Client POSTs → API creates job record (pending) → enqueues to Redis → returns job_id
2. Worker dequeues → updates status (running) → executes playbook → updates status (successful/failed)
3. Client polls GET → gets current status + results when complete

## API Design

### POST /api/v1/jobs

**Request:**
```json
{"playbook": "hello.yml", "extra_vars": {"name": "World"}, "inventory": "localhost,"}
```

**Async response (default):** `202 Accepted`
```json
{
  "job_id": "abc123",
  "status": "pending",
  "created_at": "2026-01-21T10:00:00Z"
}
```

**Sync response (`?sync=true`):** `200 OK`
```json
{
  "status": "successful",
  "rc": 0,
  "stdout": "PLAY [Hello World]...",
  "stats": {"localhost": {"ok": 1, "changed": 0, "failures": 0}}
}
```

### GET /api/v1/jobs/{job_id}

**Response:**
```json
{
  "job_id": "abc123",
  "status": "successful",
  "playbook": "hello.yml",
  "created_at": "2026-01-21T10:00:00Z",
  "started_at": "2026-01-21T10:00:01Z",
  "finished_at": "2026-01-21T10:00:05Z",
  "result": {
    "rc": 0,
    "stdout": "PLAY [Hello World]...",
    "stats": {"localhost": {"ok": 1, "changed": 0, "failures": 0}}
  }
}
```

**Job statuses:**
- `pending` - Queued, waiting for worker
- `running` - Worker executing playbook
- `successful` - Completed with rc=0
- `failed` - Completed with rc!=0 or error

**Error responses:**
- `404` - Job ID not found
- `400` - Invalid job ID format

## Job State Storage

Jobs stored in Redis as hashes:

```
job:{job_id} = {
  "job_id": "abc123",
  "status": "pending",
  "playbook": "hello.yml",
  "extra_vars": "{\"name\": \"World\"}",
  "inventory": "localhost,",
  "created_at": "2026-01-21T10:00:00Z",
  "started_at": null,
  "finished_at": null,
  "result": null,
  "error": null
}
```

**TTL:** Jobs expire after 24 hours (configurable). For persistent history, add database later.

## Docker Setup

**docker-compose.yml:**
```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

**Running locally:**
```bash
# Terminal 1: Start Redis
docker-compose up -d

# Terminal 2: Start API
uvicorn ansible_runner_service.main:app --reload

# Terminal 3: Start worker
rq worker --url redis://localhost:6379
```

## File Structure

**New files:**
- `docker-compose.yml` - Redis container
- `src/ansible_runner_service/job_store.py` - Redis-backed job state
- `src/ansible_runner_service/queue.py` - Job enqueueing with rq
- `src/ansible_runner_service/worker.py` - Worker entry point and job execution
- `tests/test_async_jobs.py` - Integration tests with Redis

**Modified files:**
- `src/ansible_runner_service/main.py` - Add sync param, GET endpoint
- `src/ansible_runner_service/schemas.py` - Add JobSubmitResponse, JobDetail models
- `pyproject.toml` - Add rq, redis dependencies

## Testing Strategy

**Unit tests:** Mock Redis, test job store operations

**Integration tests:** Real Redis container, full async flow
- Submit job → returns job_id with pending status
- Poll pending job → eventually becomes successful
- Submit with `?sync=true` → returns full result immediately
- Poll with extra_vars → result contains expected output
- Poll nonexistent job → 404
- Worker handles playbook error → status=failed with error message

## Dependencies

Add to pyproject.toml:
```toml
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn>=0.27.0",
    "ansible-runner>=2.3.0",
    "rq>=1.16.0",
    "redis>=5.0.0",
]
```
