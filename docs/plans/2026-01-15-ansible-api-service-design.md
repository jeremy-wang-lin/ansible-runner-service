# Ansible API Service - Design Document

**Date:** 2026-01-15
**Status:** Draft - Under Review

## Overview

An API service that exposes endpoints for other components to run Ansible playbooks. Designed for CI/CD pipelines and microservice orchestration with support for heavy concurrent load.

## Requirements Summary

| Aspect | Decision |
|--------|----------|
| Use cases | CI/CD pipelines + microservice orchestration |
| Execution model | Sync and async (polling/webhook callbacks) |
| Scale | Heavy - hundreds of concurrent runs, distributed workers |
| Playbook source | Git-based, pulled at runtime |
| API authentication | API keys |
| Inventory | Combination (caller-provided, dynamic, pre-configured) |
| Credentials | Combination (centralized, vault, caller-provided) |
| Technology stack | Python + FastAPI + ansible-runner + rq (Redis Queue) |
| Message broker | Redis (rq for MVP, Celery-ready abstraction) |
| Database | MariaDB |
| Log storage | Object Storage (S3/MinIO) |
| Deployment | Kubernetes |
| Observability | Logs + Prometheus metrics + distributed tracing |

---

## High-Level Architecture

> **Design Note:** This architecture uses `rq` (Redis Queue) for the MVP to minimize complexity.
> The queue interface is abstracted to allow migration to Celery when scale demands it.

```
┌─────────────────────────────────────────────────────────────┐
│                        Clients                               │
│         (CI/CD pipelines, microservices, etc.)              │
└──────────┬─────────────────────────────────────┬────────────┘
           │ HTTPS + API Key                     │ SSE
           ▼                                     │ (live streaming)
┌─────────────────────────────────────────────────────────────┐
│                      API Layer                               │
│              FastAPI (2-3 replicas for MVP)                 │
│    - Request validation & authentication                    │
│    - Job submission & status queries                         │
│    - Log streaming endpoints (SSE)                          │
│    - Webhook dispatcher                                      │
└─────────────────────────┬───────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│    Redis     │  │   MariaDB    │  │  Object Storage  │
│ - rq queue   │  │ - Job records│  │  (S3/MinIO)      │
│ - Live events│  │ - API keys   │  │ - Full exec logs │
│ - Pub/sub    │  │ - Credentials│  │ - Artifacts      │
└──────────────┘  └──────────────┘  └──────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                    rq Workers                                │
│              (2-5 replicas for MVP)                         │
│      - Pull playbooks from Git                              │
│      - Execute via ansible-runner                           │
│      - Publish events to Redis (real-time)                  │
│      - Upload full logs to Object Storage                   │
└─────────────────────────────────────────────────────────────┘
```

### Request Flow

1. Client submits job to API layer
2. API validates request, creates job record in MariaDB, queues task in Redis
3. Available worker picks up task, clones/pulls Git repo, runs playbook
4. Worker streams events to Redis, updates final status in MariaDB
5. Worker uploads full logs to Object Storage
6. Client polls for status or receives webhook callback

### Execution Logs & Progress - Three Tiers

| Tier | Storage | Purpose | Retention |
|------|---------|---------|-----------|
| Live events | Redis Pub/Sub | Real-time task/play/host progress | Duration of job |
| Job summary | MariaDB | Status, timing, success/failure per host | Long-term |
| Full logs | Object Storage | Complete ansible-runner output + artifacts | Configurable |

### How Clients Get Progress

1. **Polling** - `GET /jobs/{id}` returns current status, summary, log URL
2. **Server-Sent Events (SSE)** - `GET /jobs/{id}/stream` for real-time events as playbook runs
3. **Webhook callback** - POST to client-specified URL on completion with results

### Migration-Friendly Queue Abstraction

The queue interface is abstracted to allow swapping `rq` for Celery without changing application code:

```python
# app/queue/interface.py - Abstract interface
from abc import ABC, abstractmethod
from typing import Any

class JobQueue(ABC):
    @abstractmethod
    def enqueue(self, job_type: str, payload: dict) -> str:
        """Submit job, return job_id"""
        pass

    @abstractmethod
    def get_status(self, job_id: str) -> dict:
        """Get job status from queue"""
        pass

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Cancel a queued/running job"""
        pass


# app/queue/rq_backend.py - MVP implementation
from rq import Queue
from redis import Redis

class RQJobQueue(JobQueue):
    def __init__(self, redis_url: str):
        self.redis = Redis.from_url(redis_url)
        self.queue = Queue(connection=self.redis)

    def enqueue(self, job_type: str, payload: dict) -> str:
        job = self.queue.enqueue(
            f"app.workers.{job_type}",
            payload,
            job_timeout="1h"
        )
        return job.id

    def get_status(self, job_id: str) -> dict:
        job = Job.fetch(job_id, connection=self.redis)
        return {"status": job.get_status(), "result": job.result}

    def cancel(self, job_id: str) -> bool:
        job = Job.fetch(job_id, connection=self.redis)
        job.cancel()
        return True


# app/queue/celery_backend.py - Future implementation (when scale demands)
class CeleryJobQueue(JobQueue):
    """Swap in when you need Celery's features:
    - Task routing to different queues
    - Task priorities
    - Advanced retry policies
    - Canvas workflows (chains, groups, chords)
    """
    pass


# Dependency injection in FastAPI
def get_queue() -> JobQueue:
    if settings.QUEUE_BACKEND == "celery":
        return CeleryJobQueue(settings.CELERY_BROKER_URL)
    return RQJobQueue(settings.REDIS_URL)
```

**Migration path to Celery:**
1. Implement `CeleryJobQueue` class
2. Change config: `QUEUE_BACKEND=celery`
3. Deploy Celery workers instead of rq workers
4. No API changes needed

---

## API Design

### Core Endpoints

```
# Job Management
POST   /api/v1/jobs                    # Submit new playbook job
GET    /api/v1/jobs                    # List jobs (with filters)
GET    /api/v1/jobs/{id}               # Get job status & summary
GET    /api/v1/jobs/{id}/stream        # SSE stream for live progress
GET    /api/v1/jobs/{id}/logs          # Get full log (redirect to storage)
DELETE /api/v1/jobs/{id}               # Cancel running job

# Credentials Management
POST   /api/v1/credentials             # Register credential set
GET    /api/v1/credentials             # List credentials (names only)
DELETE /api/v1/credentials/{name}      # Remove credential

# Health & Metrics
GET    /health                         # Liveness probe
GET    /ready                          # Readiness probe
GET    /metrics                        # Prometheus metrics
```

### Job Submission Request Schema

```json
{
  "source": { ... },           // What to run (playbook or role)
  "inventory": { ... },        // Target hosts
  "credentials": { ... },      // Authentication
  "extra_vars": { ... },       // Variables
  "options": { ... }           // Execution options
}
```

---

### Source Options (What to Run)

#### Option A: Playbook from Git repo

```json
"source": {
  "type": "playbook",
  "repo": "git@github.com:org/ansible-playbooks.git",
  "branch": "main",
  "path": "deploy/webserver.yml",
  "git_credential": "github-deploy-key"
}
```

#### Option B: Role from Git repo

```json
"source": {
  "type": "role",
  "repo": "git@github.com:org/ansible-roles.git",
  "branch": "v2.0.0",
  "path": "roles/nginx",
  "role_vars": {
    "nginx_port": 8080
  }
}
```

#### Option C: Role from Ansible Galaxy collection

```json
"source": {
  "type": "collection_role",
  "collection": "community.general",
  "role": "docker_swarm",
  "version": ">=6.0.0",
  "galaxy_server": "https://galaxy.ansible.com",
  "role_vars": {
    "swarm_manager": true
  }
}
```

#### Option D: Role from private Automation Hub / Galaxy

```json
"source": {
  "type": "collection_role",
  "collection": "mycompany.internal",
  "role": "app_deploy",
  "version": "1.2.0",
  "galaxy_server": "https://hub.internal.com/api/galaxy/",
  "galaxy_credential": "automation-hub-token"
}
```

---

### Inventory Options

#### Option A: Inline hosts (simple)

```json
"inventory": {
  "type": "inline",
  "hosts": ["10.0.1.10", "10.0.1.11"],
  "groups": {
    "webservers": ["10.0.1.10"],
    "databases": ["10.0.1.11"]
  },
  "host_vars": {
    "10.0.1.10": { "http_port": 8080 }
  }
}
```

#### Option B: Dynamic inventory plugin

```json
"inventory": {
  "type": "dynamic",
  "plugin": "amazon.aws.aws_ec2",
  "config": {
    "regions": ["us-west-2"],
    "filters": {
      "tag:Environment": "production"
    },
    "keyed_groups": [
      { "key": "tags.Role", "prefix": "role" }
    ]
  },
  "cloud_credential": "aws-prod"
}
```

#### Option C: Inventory file from Git

```json
"inventory": {
  "type": "git",
  "repo": "git@github.com:org/inventory.git",
  "branch": "main",
  "path": "production/hosts.yml",
  "git_credential": "github-deploy-key"
}
```

#### Option D: External inventory URL

```json
"inventory": {
  "type": "url",
  "url": "https://cmdb.internal.com/api/ansible/inventory",
  "format": "yaml",
  "headers": {
    "Authorization": "Bearer ${CMDB_TOKEN}"
  },
  "credential": "cmdb-api-token"
}
```

---

### Credentials Options

#### Option A: Reference stored credentials

```json
"credentials": {
  "ssh": "prod-ssh-key",
  "become_password": "prod-become",
  "vault_password": "prod-vault"
}
```

#### Option B: HashiCorp Vault integration

```json
"credentials": {
  "ssh": {
    "type": "hashicorp_vault",
    "vault_path": "secret/ansible/prod-ssh",
    "vault_key": "private_key",
    "vault_credential": "hcp-vault-token"
  },
  "become_password": {
    "type": "hashicorp_vault",
    "vault_path": "secret/ansible/prod-become",
    "vault_key": "password"
  }
}
```

#### Option C: Cloud provider credentials (for dynamic inventory)

```json
"credentials": {
  "ssh": "prod-ssh-key",
  "cloud": {
    "type": "aws",
    "credential": "aws-prod"
  }
}
```

#### Option D: Mixed - stored + vault

```json
"credentials": {
  "ssh": "prod-ssh-key",
  "become_password": {
    "type": "hashicorp_vault",
    "vault_path": "secret/sudo/prod"
  }
}
```

---

### Execution Options

```json
"options": {
  "timeout": 3600,
  "callback_url": "https://ci.example.com/webhook",
  "tags": ["deploy", "config"],
  "skip_tags": ["debug"],
  "limit": "webservers",
  "verbosity": 2,
  "check_mode": false,
  "diff_mode": true,
  "forks": 10
}
```

---

### Job Status Response

```json
{
  "id": "job-abc123",
  "status": "running",
  "created_at": "2026-01-15T10:00:00Z",
  "started_at": "2026-01-15T10:00:05Z",
  "finished_at": null,
  "progress": {
    "current_play": "Deploy application",
    "current_task": "Copy files",
    "hosts_ok": 1,
    "hosts_failed": 0,
    "hosts_pending": 1
  },
  "log_url": "https://storage.example.com/logs/job-abc123.log"
}
```

---

## Sections To Be Completed

The following sections need to be designed:

- [ ] Data Model (MariaDB schema)
- [ ] Worker Component Design
- [ ] Git Repository Caching Strategy
- [ ] Credential Storage & Encryption
- [ ] Error Handling & Retry Logic
- [ ] Webhook Delivery
- [ ] Kubernetes Deployment Architecture
- [ ] Observability (metrics, tracing, logging)
- [ ] Security Considerations
- [ ] API Rate Limiting

Completed:
- [x] High-Level Architecture
- [x] API Design (endpoints, request/response schemas)
- [x] Queue Abstraction (rq MVP with Celery migration path)

---

## Open Questions

1. Should we support running multiple playbooks in a single job?
2. What's the retention policy for job history and logs?
3. Do we need role-based access control (RBAC) for different API keys?
4. Should workers have dedicated pools for different workload types?

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-01-15 | - | Initial draft |
| 2026-01-15 | - | Simplified to rq-based architecture with Celery migration path |
