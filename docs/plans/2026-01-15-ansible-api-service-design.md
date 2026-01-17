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

#### Option E: Playbook from Nexus (bundled artifact)

For environments where workers can't access Git directly (e.g., prod workers accessing dev Git).
CI/CD packages playbooks into .tgz and uploads to Nexus.

```json
"source": {
  "type": "nexus",
  "repository": "ansible-playbooks",
  "artifact": "app-deploy",
  "version": "1.2.0",
  "path": "deploy/webserver.yml",
  "nexus_credential": "nexus-prod"
}
```

#### Option F: Role from Nexus (bundled artifact)

```json
"source": {
  "type": "nexus",
  "repository": "ansible-roles",
  "artifact": "nginx-role",
  "version": "2.0.0",
  "role_name": "nginx",
  "role_vars": {
    "nginx_port": 8080
  },
  "nexus_credential": "nexus-prod"
}
```

#### Option G: Playbook from S3 / MinIO (bundled artifact)

```json
"source": {
  "type": "s3",
  "bucket": "ansible-artifacts",
  "key": "app-deploy/1.2.0.tgz",
  "path": "deploy/webserver.yml",
  "s3_credential": "s3-artifacts"
}
```

#### Option H: Role from S3 / MinIO (bundled artifact)

```json
"source": {
  "type": "s3",
  "bucket": "ansible-artifacts",
  "key": "roles/nginx-role/2.0.0.tgz",
  "role_name": "nginx",
  "role_vars": {
    "nginx_port": 8080
  },
  "s3_credential": "s3-artifacts"
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

## Data Model (MariaDB Schema)

### Core Tables

```sql
-- API Keys for authentication
CREATE TABLE api_keys (
    id VARCHAR(36) PRIMARY KEY,           -- UUID
    name VARCHAR(255) NOT NULL UNIQUE,    -- Human-readable name
    key_hash VARCHAR(255) NOT NULL,       -- bcrypt hash of API key
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP NULL,

    INDEX idx_key_hash (key_hash),
    INDEX idx_is_active (is_active)
);

-- Stored credentials (encrypted)
CREATE TABLE credentials (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,    -- Reference name
    type ENUM('ssh_key', 'password', 'vault_password',
              'cloud_aws', 'cloud_gcp', 'cloud_azure',
              'git_ssh', 'git_token', 'galaxy_token') NOT NULL,
    encrypted_data BLOB NOT NULL,         -- AES-256 encrypted JSON
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by VARCHAR(36),               -- api_key.id

    INDEX idx_name (name),
    INDEX idx_type (type)
);

-- Job records
CREATE TABLE jobs (
    id VARCHAR(36) PRIMARY KEY,           -- UUID
    status ENUM('pending', 'running', 'success', 'failed', 'cancelled') NOT NULL,

    -- Request details (stored as JSON for flexibility)
    source JSON NOT NULL,                 -- playbook/role source config
    inventory JSON NOT NULL,              -- inventory config
    credentials JSON NOT NULL,            -- credential references (not secrets)
    extra_vars JSON,                      -- extra variables
    options JSON,                         -- execution options

    -- Timing
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP NULL,
    finished_at TIMESTAMP NULL,

    -- Results summary
    exit_code INT NULL,
    hosts_ok INT DEFAULT 0,
    hosts_failed INT DEFAULT 0,
    hosts_unreachable INT DEFAULT 0,
    hosts_skipped INT DEFAULT 0,

    -- Log storage
    log_path VARCHAR(512),                -- Object storage path

    -- Callback
    callback_url VARCHAR(1024),
    callback_sent BOOLEAN DEFAULT FALSE,

    -- Tracking
    api_key_id VARCHAR(36),
    worker_id VARCHAR(255),               -- Which worker processed this

    INDEX idx_status (status),
    INDEX idx_created_at (created_at),
    INDEX idx_api_key (api_key_id),
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
);

-- Per-host results (for detailed reporting)
CREATE TABLE job_host_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_id VARCHAR(36) NOT NULL,
    hostname VARCHAR(255) NOT NULL,
    status ENUM('ok', 'failed', 'unreachable', 'skipped') NOT NULL,
    tasks_ok INT DEFAULT 0,
    tasks_failed INT DEFAULT 0,
    tasks_changed INT DEFAULT 0,
    tasks_skipped INT DEFAULT 0,
    error_message TEXT,

    INDEX idx_job_id (job_id),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| JSON for request fields | Flexible schema as API evolves; avoids migrations for new options |
| Separate host results table | Query failed hosts across jobs; detailed audit trail |
| Encrypted credentials blob | Single encrypted field simpler than per-column encryption |
| No job events table | Live events in Redis; full logs in Object Storage |

---

## Worker Component Design

### Worker Responsibilities

1. Pull jobs from rq queue
2. Prepare execution environment (Git clone, credentials, inventory)
3. Execute playbook via ansible-runner
4. Stream events to Redis for real-time progress
5. Upload logs to Object Storage
6. Update job status in MariaDB

### Worker Process Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        rq Worker                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. FETCH JOB                                                    │
│    - Dequeue job from Redis                                     │
│    - Update job status → 'running' in MariaDB                   │
│    - Record worker_id and started_at                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. PREPARE ENVIRONMENT                                          │
│    - Clone/pull Git repo (with caching)                         │
│    - Resolve credentials (fetch from DB or Vault)               │
│    - Generate inventory file (inline/dynamic/git)               │
│    - Create ansible-runner private_data_dir                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. EXECUTE PLAYBOOK                                             │
│    - Run via ansible_runner.run()                               │
│    - Stream events to Redis pub/sub (job:{id}:events)           │
│    - Capture stdout/stderr                                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. FINALIZE                                                     │
│    - Upload full logs to Object Storage                         │
│    - Parse results, update job_host_results                     │
│    - Update job status → 'success'/'failed' in MariaDB          │
│    - Send webhook callback if configured                        │
│    - Cleanup private_data_dir                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Core Worker Code Structure

```python
# app/workers/ansible_job.py
import ansible_runner
from redis import Redis
from app.services.git import GitService
from app.services.credentials import CredentialService
from app.services.inventory import InventoryService
from app.services.storage import StorageService
from app.db import get_db

class AnsibleJobWorker:
    def __init__(self, redis: Redis):
        self.redis = redis
        self.git = GitService()
        self.credentials = CredentialService()
        self.inventory = InventoryService()
        self.storage = StorageService()

    def execute(self, job_id: str, payload: dict):
        db = get_db()
        job = db.jobs.get(job_id)

        try:
            # Update status
            job.update(status='running', started_at=now(), worker_id=self.worker_id)

            # Prepare environment
            private_data_dir = self._prepare_environment(job, payload)

            # Execute with event streaming
            result = self._run_playbook(job_id, private_data_dir, payload)

            # Finalize
            self._finalize(job, result, private_data_dir)

        except Exception as e:
            job.update(status='failed', finished_at=now(), error=str(e))
            raise

    def _prepare_environment(self, job, payload) -> str:
        """Create ansible-runner private_data_dir with all required files"""

        private_data_dir = f"/tmp/ansible-jobs/{job.id}"

        # 1. Clone/pull playbook repo
        source = payload['source']
        project_dir = self.git.ensure_repo(
            repo=source['repo'],
            branch=source['branch'],
            credential=source.get('git_credential')
        )

        # 2. Resolve credentials → write to private_data_dir/env/
        creds = self.credentials.resolve(payload['credentials'])
        write_credentials(private_data_dir, creds)

        # 3. Generate inventory → write to private_data_dir/inventory/
        inv = self.inventory.generate(payload['inventory'])
        write_inventory(private_data_dir, inv)

        # 4. Write extra_vars → private_data_dir/env/extravars
        if payload.get('extra_vars'):
            write_extravars(private_data_dir, payload['extra_vars'])

        return private_data_dir

    def _run_playbook(self, job_id: str, private_data_dir: str, payload: dict):
        """Execute playbook with real-time event streaming"""

        def event_handler(event):
            # Publish to Redis for SSE subscribers
            self.redis.publish(
                f"job:{job_id}:events",
                json.dumps(event)
            )
            # Update progress in MariaDB (throttled)
            self._update_progress(job_id, event)

        options = payload.get('options', {})

        result = ansible_runner.run(
            private_data_dir=private_data_dir,
            playbook=payload['source']['path'],
            event_handler=event_handler,
            timeout=options.get('timeout', 3600),
            tags=options.get('tags'),
            skip_tags=options.get('skip_tags'),
            limit=options.get('limit'),
            verbosity=options.get('verbosity', 0),
            check=options.get('check_mode', False),
            diff=options.get('diff_mode', False),
            forks=options.get('forks', 5),
        )

        return result

    def _finalize(self, job, result, private_data_dir: str):
        """Upload logs, update DB, send webhook"""

        # Upload full stdout to object storage
        log_path = f"logs/{job.id}/stdout.txt"
        self.storage.upload(log_path, result.stdout.read())

        # Parse host results
        host_results = self._parse_host_results(result)
        for hr in host_results:
            db.job_host_results.create(job_id=job.id, **hr)

        # Update job record
        job.update(
            status='success' if result.rc == 0 else 'failed',
            finished_at=now(),
            exit_code=result.rc,
            hosts_ok=result.stats.get('ok', 0),
            hosts_failed=result.stats.get('failures', 0),
            hosts_unreachable=result.stats.get('unreachable', 0),
            log_path=log_path
        )

        # Send webhook
        if job.callback_url:
            self._send_webhook(job)

        # Cleanup
        shutil.rmtree(private_data_dir)
```

### Event Streaming via Redis Pub/Sub

Events are published to Redis channel `job:{id}:events`:

```python
# Play start event
{
    "event": "playbook_on_play_start",
    "timestamp": "2026-01-15T10:00:05Z",
    "data": {
        "play": "Deploy application",
        "hosts": ["10.0.1.10", "10.0.1.11"]
    }
}

# Task event
{
    "event": "runner_on_ok",
    "timestamp": "2026-01-15T10:00:10Z",
    "data": {
        "task": "Copy application files",
        "host": "10.0.1.10",
        "result": "changed"
    }
}

# Job complete event
{
    "event": "playbook_on_stats",
    "timestamp": "2026-01-15T10:05:00Z",
    "data": {
        "ok": {"10.0.1.10": 15, "10.0.1.11": 15},
        "failures": {},
        "unreachable": {}
    }
}
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| ansible-runner library | Official Red Hat library; handles process lifecycle, artifacts, events |
| Event handler callback | Real-time streaming without polling; ansible-runner native support |
| Redis Pub/Sub for events | Lightweight, no persistence needed; subscribers get live updates |
| Temp private_data_dir | Isolation between jobs; clean credential handling |
| Cleanup after job | Don't leak credentials or fill disk |

### Progress Update Implementation

The `_update_progress()` method updates MariaDB so polling clients see real-time status:

```python
def _update_progress(self, job_id: str, event: dict):
    """Update job progress in DB (throttled to significant events only)"""

    event_type = event['event']
    event_data = event.get('event_data', {})

    if event_type == 'playbook_on_play_start':
        db.jobs.update(job_id, progress={
            'current_play': event_data.get('play'),
            'current_task': None
        })

    elif event_type == 'playbook_on_task_start':
        db.jobs.update(job_id, progress={
            'current_task': event_data.get('task')
        })

    elif event_type == 'runner_on_ok':
        db.execute(
            "UPDATE jobs SET hosts_ok = hosts_ok + 1 WHERE id = %s",
            [job_id]
        )

    elif event_type == 'runner_on_failed':
        db.execute(
            "UPDATE jobs SET hosts_failed = hosts_failed + 1 WHERE id = %s",
            [job_id]
        )

    elif event_type == 'runner_on_unreachable':
        db.execute(
            "UPDATE jobs SET hosts_unreachable = hosts_unreachable + 1 WHERE id = %s",
            [job_id]
        )
```

### Log Handling Clarification

Logs are handled at two levels - the event handler does NOT write logs:

| Concern | Component | Storage |
|---------|-----------|---------|
| Real-time events | Event handler | Redis Pub/Sub (ephemeral) |
| Real-time stdout | Stdout callback | Redis Pub/Sub (ephemeral) |
| Full execution logs | ansible-runner artifacts | Object Storage (persistent) |

```
During execution:
┌────────────────────┐     ┌────────────────────┐
│   event_handler    │     │   stdout_callback  │
│   → Redis Pub/Sub  │     │   → Redis Pub/Sub  │
│   (structured)     │     │   (raw output)     │
└────────────────────┘     └────────────────────┘

After execution:
┌──────────────────────────────────────────────┐
│  ansible-runner stores in private_data_dir:  │
│  - artifacts/stdout (full output)            │
│  - artifacts/status                          │
│  - artifacts/rc (return code)                │
└──────────────────────────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│  _finalize() uploads to Object Storage       │
│  → logs/{job_id}/stdout.txt                  │
└──────────────────────────────────────────────┘
```

### Real-time Stdout Streaming

Clients connect to SSE endpoint to see live ansible output (like terminal experience):

**Worker streams stdout to Redis:**

```python
def _run_playbook(self, job_id: str, private_data_dir: str, payload: dict):
    """Execute playbook with real-time stdout streaming"""

    def event_handler(event):
        """Structured events (task status, host results)"""
        self.redis.publish(
            f"job:{job_id}:events",
            json.dumps({"type": "event", "data": event})
        )
        self._update_progress(job_id, event)

    # Capture stdout line by line
    stdout_lines = []

    def stdout_callback(line):
        """Raw stdout lines (ansible output as you'd see in terminal)"""
        stdout_lines.append(line)
        self.redis.publish(
            f"job:{job_id}:events",
            json.dumps({"type": "stdout", "line": line})
        )

    options = payload.get('options', {})

    runner_config = ansible_runner.RunnerConfig(
        private_data_dir=private_data_dir,
        playbook=payload['source']['path'],
        timeout=options.get('timeout', 3600),
        # ... other options
    )
    runner_config.prepare()

    runner = ansible_runner.Runner(config=runner_config)

    # Run with both callbacks
    result = runner.run()

    # Process events with our handlers
    for event in runner.events:
        event_handler(event)

    return result
```

**API endpoint for SSE streaming (client chooses content via query param):**

```
GET /jobs/{job_id}/stream?include=events        # Structured events only
GET /jobs/{job_id}/stream?include=stdout        # Raw stdout only
GET /jobs/{job_id}/stream?include=events,stdout # Both (default)
```

```python
# app/api/jobs.py
from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse
from typing import List

router = APIRouter()

@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str,
    include: List[str] = Query(default=["events", "stdout"])
):
    """
    Stream real-time job output via Server-Sent Events.

    Query params:
        include: Comma-separated list of content types
                 - "events": Structured events (task status, host results)
                 - "stdout": Raw ansible output (terminal-like)
                 Default: both
    """
    include_events = "events" in include
    include_stdout = "stdout" in include

    async def event_generator():
        pubsub = redis.pubsub()
        pubsub.subscribe(f"job:{job_id}:events")

        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    data = json.loads(message['data'])

                    # Filter based on client preference
                    if data.get('type') == 'stdout' and not include_stdout:
                        continue
                    if data.get('type') == 'event' and not include_events:
                        # Still check for completion even if not streaming events
                        if data['data'].get('event') == 'playbook_on_stats':
                            yield {"event": "done", "data": "{}"}
                            break
                        continue

                    yield {"event": "message", "data": json.dumps(data)}

                    # End stream when job completes
                    if data.get('type') == 'event':
                        if data['data'].get('event') == 'playbook_on_stats':
                            yield {"event": "done", "data": "{}"}
                            break
        finally:
            pubsub.unsubscribe()

    return EventSourceResponse(event_generator())
```

**Use cases for each mode:**

| Mode | Use case |
|------|----------|
| `include=events` | Programmatic clients that parse structured data (CI/CD pipelines) |
| `include=stdout` | Terminal-like UI that just displays raw output |
| `include=events,stdout` | Rich UI showing both progress metrics and live output |

**Client receives interleaved stdout and events (when both enabled):**

```
event: message
data: {"type": "stdout", "line": "PLAY [Deploy application] ***********************"}

event: message
data: {"type": "stdout", "line": ""}

event: message
data: {"type": "stdout", "line": "TASK [Gathering Facts] **************************"}

event: message
data: {"type": "event", "data": {"event": "runner_on_ok", "host": "10.0.1.10"}}

event: message
data: {"type": "stdout", "line": "ok: [10.0.1.10]"}

event: message
data: {"type": "stdout", "line": ""}

event: message
data: {"type": "stdout", "line": "TASK [Copy files] *******************************"}

event: done
data: {}
```

**Client example (JavaScript):**

```javascript
// Rich UI - both events and stdout
const eventSource = new EventSource('/api/v1/jobs/job-abc123/stream?include=events,stdout');

eventSource.onmessage = (e) => {
    const data = JSON.parse(e.data);

    if (data.type === 'stdout') {
        // Append to terminal-like display
        terminal.append(data.line + '\n');
    } else if (data.type === 'event') {
        // Update structured progress (e.g., host counters)
        updateProgress(data.data);
    }
};

eventSource.addEventListener('done', () => {
    eventSource.close();
    console.log('Job completed');
});


// CI/CD pipeline - events only (smaller payload, structured data)
const eventSource = new EventSource('/api/v1/jobs/job-abc123/stream?include=events');

eventSource.onmessage = (e) => {
    const event = JSON.parse(e.data).data;
    if (event.event === 'runner_on_failed') {
        console.error(`Task failed on ${event.host}`);
    }
};
```

---

## Playbook Source Services

Workers support multiple source types for fetching playbooks. An abstraction layer handles the differences.

### Source Type Summary

| Type | Use case | Network requirement |
|------|----------|---------------------|
| `playbook` / `role` | Direct Git access | Worker → Git server |
| `collection_role` | Ansible Galaxy | Worker → Galaxy/Hub |
| `nexus` | Bundled artifacts | Worker → Nexus |
| `s3` | Bundled artifacts | Worker → S3/MinIO |

### Artifact Service Implementation

```python
# app/services/playbook_source.py
from abc import ABC, abstractmethod
import tarfile
import subprocess
import requests
import boto3
from pathlib import Path

class PlaybookSource(ABC):
    @abstractmethod
    def fetch(self, config: dict, target_dir: str) -> str:
        """Fetch playbook/role source, return path to project directory"""
        pass


class GitPlaybookSource(PlaybookSource):
    """Handles type: playbook, role (direct Git clone)"""

    def fetch(self, config: dict, target_dir: str) -> str:
        env = self._get_git_env(config.get('git_credential'))

        # Clone repo
        subprocess.run(
            ["git", "clone", "--branch", config['branch'],
             "--depth", "1", config['repo'], target_dir],
            env=env,
            check=True
        )
        return target_dir

    def _get_git_env(self, credential: str) -> dict:
        import os
        env = os.environ.copy()

        if credential:
            cred = credential_service.get(credential)
            if cred['type'] == 'git_ssh':
                key_path = write_temp_key(cred['private_key'])
                env['GIT_SSH_COMMAND'] = f'ssh -i {key_path} -o StrictHostKeyChecking=no'
            elif cred['type'] == 'git_token':
                env['GIT_ASKPASS'] = '/opt/ansible-api/bin/git-token-helper.sh'
                env['GIT_TOKEN'] = cred['token']

        return env


class GalaxyPlaybookSource(PlaybookSource):
    """Handles type: collection_role (Ansible Galaxy/Automation Hub)"""

    def fetch(self, config: dict, target_dir: str) -> str:
        # Install collection
        collection = config['collection']
        version = config.get('version', '')
        galaxy_server = config.get('galaxy_server')

        cmd = ["ansible-galaxy", "collection", "install",
               f"{collection}{':' + version if version else ''}",
               "-p", target_dir]

        if galaxy_server:
            cmd.extend(["--server", galaxy_server])

        env = os.environ.copy()
        if config.get('galaxy_credential'):
            cred = credential_service.get(config['galaxy_credential'])
            env['ANSIBLE_GALAXY_SERVER_TOKEN'] = cred['token']

        subprocess.run(cmd, env=env, check=True)
        return target_dir


class NexusPlaybookSource(PlaybookSource):
    """Handles type: nexus (bundled artifacts from Nexus Raw repository)"""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def fetch(self, config: dict, target_dir: str) -> str:
        repository = config['repository']
        artifact = config['artifact']
        version = config['version']

        url = f"{self.base_url}/{repository}/{artifact}/{version}.tgz"

        # Get credentials
        auth = None
        if config.get('nexus_credential'):
            cred = credential_service.get(config['nexus_credential'])
            auth = (cred['username'], cred['password'])

        # Download
        response = requests.get(url, auth=auth, stream=True)
        response.raise_for_status()

        # Save and extract
        archive_path = Path(target_dir) / "artifact.tgz"
        with open(archive_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        extract_dir = Path(target_dir) / "project"
        extract_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(archive_path, 'r:gz') as tar:
            tar.extractall(extract_dir)

        archive_path.unlink()
        return str(extract_dir)


class S3PlaybookSource(PlaybookSource):
    """Handles type: s3 (bundled artifacts from S3/MinIO)"""

    def __init__(self, endpoint_url: str = None):
        self.endpoint_url = endpoint_url  # For MinIO; None for AWS S3

    def fetch(self, config: dict, target_dir: str) -> str:
        s3_config = {}

        if config.get('s3_credential'):
            cred = credential_service.get(config['s3_credential'])
            s3_config['aws_access_key_id'] = cred['access_key']
            s3_config['aws_secret_access_key'] = cred['secret_key']

        if self.endpoint_url:
            s3_config['endpoint_url'] = self.endpoint_url

        s3 = boto3.client('s3', **s3_config)

        # Download
        archive_path = Path(target_dir) / "artifact.tgz"
        s3.download_file(config['bucket'], config['key'], str(archive_path))

        # Extract
        extract_dir = Path(target_dir) / "project"
        extract_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(archive_path, 'r:gz') as tar:
            tar.extractall(extract_dir)

        archive_path.unlink()
        return str(extract_dir)


# Factory function
def get_playbook_source(source_type: str) -> PlaybookSource:
    sources = {
        'playbook': GitPlaybookSource(),
        'role': GitPlaybookSource(),
        'collection_role': GalaxyPlaybookSource(),
        'nexus': NexusPlaybookSource(settings.NEXUS_BASE_URL),
        's3': S3PlaybookSource(settings.S3_ENDPOINT_URL),
    }
    if source_type not in sources:
        raise ValueError(f"Unknown source type: {source_type}")
    return sources[source_type]
```

### Updated Worker Integration

```python
# In worker's _prepare_environment()

def _prepare_environment(self, job, payload) -> str:
    private_data_dir = f"/tmp/ansible-jobs/{job.id}"
    Path(private_data_dir).mkdir(parents=True, exist_ok=True)

    # 1. Fetch playbook source (Git, Nexus, or S3)
    source = payload['source']
    playbook_source = get_playbook_source(source['type'])
    project_dir = playbook_source.fetch(source, private_data_dir)

    # 2. Resolve credentials, inventory, etc. (unchanged)
    creds = self.credentials.resolve(payload['credentials'])
    write_credentials(private_data_dir, creds)

    inv = self.inventory.generate(payload['inventory'])
    write_inventory(private_data_dir, inv)

    if payload.get('extra_vars'):
        write_extravars(private_data_dir, payload['extra_vars'])

    return private_data_dir
```

### CI/CD Pipeline Example (Packaging & Uploading)

```yaml
# .gitlab-ci.yml
stages:
  - package
  - upload

package-playbooks:
  stage: package
  script:
    - tar -czvf app-deploy-${CI_COMMIT_TAG}.tgz -C playbooks .
  artifacts:
    paths:
      - app-deploy-${CI_COMMIT_TAG}.tgz

upload-to-nexus:
  stage: upload
  script:
    - |
      curl -u ${NEXUS_USER}:${NEXUS_PASS} \
        --upload-file app-deploy-${CI_COMMIT_TAG}.tgz \
        ${NEXUS_URL}/repository/ansible-playbooks/app-deploy/${CI_COMMIT_TAG}.tgz
    - |
      curl -u ${NEXUS_USER}:${NEXUS_PASS} \
        --upload-file app-deploy-${CI_COMMIT_TAG}.tgz \
        ${NEXUS_URL}/repository/ansible-playbooks/app-deploy/latest.tgz

upload-to-s3:
  stage: upload
  script:
    - aws s3 cp app-deploy-${CI_COMMIT_TAG}.tgz s3://ansible-artifacts/app-deploy/${CI_COMMIT_TAG}.tgz
    - aws s3 cp app-deploy-${CI_COMMIT_TAG}.tgz s3://ansible-artifacts/app-deploy/latest.tgz
```

---

## Sections To Be Completed

The following sections need to be designed:

- [x] Data Model (MariaDB schema)
- [x] Worker Component Design
- [x] Playbook Source Services (Git, Nexus, S3)
- [ ] Git Repository Caching Strategy (skipped for MVP)
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
- [x] Data Model (MariaDB schema)
- [x] Worker Component Design
- [x] Playbook Source Services (Git, Nexus, S3)

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
| 2026-01-15 | - | Added Data Model (MariaDB schema) |
| 2026-01-17 | - | Added Worker Component Design |
| 2026-01-17 | - | Added progress update, log handling, and stdout streaming details |
| 2026-01-17 | - | Updated SSE streaming to support client-selectable content via query param |
| 2026-01-17 | - | Added Playbook Source Services (Git, Nexus, S3) with bundled artifact support |
