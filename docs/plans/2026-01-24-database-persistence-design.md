# Database Persistence Design

## Overview

**Goal:** Add MariaDB for durable job history while keeping Redis for fast live state.

**Scope:** Jobs table only. API keys, credentials, and other tables deferred to later iterations.

**Approach:** Write-through - Worker writes to both Redis (fast polling) and MariaDB (durable history).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     docker-compose                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │    Redis     │  │   MariaDB    │  │   (Future: S3)   │  │
│  │  - Queue     │  │  - Jobs      │  │  - Full logs     │  │
│  │  - Live state│  │  - History   │  │                  │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
        ▲                    ▲
        │                    │
        │ read/write         │ write (durable)
        │ (fast, ephemeral)  │ read (history, list)
        │                    │
┌───────┴────────────────────┴───────┐
│              FastAPI               │
│  POST /jobs  GET /jobs/{id}  GET /jobs │
└────────────────┬───────────────────┘
                 │
                 │ enqueue
                 ▼
┌────────────────────────────────────┐
│            rq Worker               │
│  - Update Redis (fast)             │
│  - Write to MariaDB (durable)      │
└────────────────────────────────────┘
```

## Data Flow

**Write path:**
| Event | Redis | MariaDB |
|-------|-------|---------|
| Job created | Write (pending) | Write (pending) |
| Worker starts | Update (running) | Update (running) |
| Worker completes | Update (result) | Update (result) |
| TTL expires (24h) | Auto-delete | Preserved |

**Read path:**
- `GET /jobs/{id}` → Try Redis first (fast for active jobs), fallback to DB
- `GET /jobs` (list) → Always query DB (supports filtering, pagination)

## Database Schema

```sql
CREATE TABLE jobs (
    id VARCHAR(36) PRIMARY KEY,
    status VARCHAR(20) NOT NULL,
    playbook VARCHAR(255) NOT NULL,
    extra_vars JSON,
    inventory VARCHAR(255) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    started_at DATETIME(6),
    finished_at DATETIME(6),
    result_rc INT,
    result_stdout MEDIUMTEXT,
    result_stats JSON,
    error TEXT,

    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
);
```

**SQLAlchemy model:**
```python
class JobModel(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True)
    status = Column(String(20), nullable=False)
    playbook = Column(String(255), nullable=False)
    extra_vars = Column(JSON)
    inventory = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    result_rc = Column(Integer)
    result_stdout = Column(Text)
    result_stats = Column(JSON)
    error = Column(Text)
```

## API Changes

### New Endpoint: GET /api/v1/jobs

List jobs with filtering and pagination:

```
GET /api/v1/jobs?status=failed&limit=20&offset=0
```

**Query parameters:**
- `status` - Filter by status (pending, running, successful, failed)
- `limit` - Max results (default 20, max 100)
- `offset` - Pagination offset

**Response:**
```json
{
  "jobs": [
    {
      "job_id": "abc123",
      "status": "successful",
      "playbook": "hello.yml",
      "created_at": "2026-01-21T10:00:00Z",
      "finished_at": "2026-01-21T10:00:05Z"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

## Recovery Logic

On API startup, detect and handle stale jobs:

```python
def recover_stale_jobs():
    """Mark jobs that were running when system crashed as failed."""
    stale_jobs = db.query(
        "SELECT id FROM jobs WHERE status = 'running' AND started_at < %s",
        [datetime.now() - timedelta(hours=1)]
    )
    for job in stale_jobs:
        if not redis.exists(f"job:{job.id}"):
            db.execute(
                "UPDATE jobs SET status = 'failed', error = 'Worker crashed' WHERE id = %s",
                [job.id]
            )
```

## Docker Compose Addition

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  mariadb:
    image: mariadb:11
    ports:
      - "3306:3306"
    environment:
      MYSQL_ROOT_PASSWORD: devpassword
      MYSQL_DATABASE: ansible_runner
    volumes:
      - mariadb_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mariadb-admin", "ping", "-h", "localhost"]
      interval: 5s
      timeout: 3s
      retries: 3

volumes:
  mariadb_data:
```

## File Structure

**New files:**
- `src/ansible_runner_service/database.py` - SQLAlchemy engine, session management
- `src/ansible_runner_service/models.py` - JobModel ORM class
- `src/ansible_runner_service/repository.py` - Job CRUD operations (DB layer)
- `alembic.ini` - Alembic configuration
- `alembic/` - Database migrations

**Modified files:**
- `docker-compose.yml` - Add MariaDB container
- `pyproject.toml` - Add sqlalchemy, alembic, mysqlclient
- `job_store.py` - Add DB writes alongside Redis
- `main.py` - Add GET /jobs endpoint, startup recovery hook
- `worker.py` - Write to DB on status changes
- `schemas.py` - Add JobListResponse, JobSummary

## Dependencies

Add to pyproject.toml:
```toml
dependencies = [
    ...
    "sqlalchemy>=2.0.0",
    "alembic>=1.13.0",
    "mysqlclient>=2.2.0",
]
```

---

## Design Considerations & Future Migration

### Current Schema vs Full Design

This schema is simplified to match the current implementation. The full production schema is documented in `docs/plans/2026-01-15-ansible-api-service-design.md` Section 5.

| Field | Current (MVP) | Future (Full Design) | Migration Trigger |
|-------|---------------|----------------------|-------------------|
| `playbook` | VARCHAR(255) | `source` JSON | When adding git/nexus/s3 sources |
| `inventory` | VARCHAR(255) | `inventory` JSON | When adding dynamic/git inventory |
| `result_stdout` | MEDIUMTEXT | `log_path` VARCHAR | When adding object storage |
| `result_stats` | JSON | Separate columns | See comparison below |
| `credentials` | Not present | JSON references | When adding credential system |
| `options` | Not present | JSON | When adding execution options |
| `callback_url` | Not present | VARCHAR(1024) | When adding webhooks |
| `api_key_id` | Not present | VARCHAR(36) + FK | When adding API auth |
| `worker_id` | Not present | VARCHAR(255) | When adding worker tracking |

### Why Simplified Now

1. **Current code reality** - Only supports local playbooks, no git/nexus/s3
2. **No object storage** - Full stdout stored in DB for now
3. **No credential system** - Not implemented yet
4. **No API auth** - Not implemented yet
5. **Incremental delivery** - Get DB working first, expand later

### Migration Path

When adding each feature, create Alembic migrations:

1. **Git/Nexus sources:**
   ```sql
   ALTER TABLE jobs ADD COLUMN source JSON;
   UPDATE jobs SET source = JSON_OBJECT('type', 'local', 'playbook', playbook);
   ALTER TABLE jobs DROP COLUMN playbook;
   ```

2. **Object storage for logs:**
   ```sql
   ALTER TABLE jobs ADD COLUMN log_path VARCHAR(512);
   -- Migrate existing stdout to S3, update log_path
   ALTER TABLE jobs DROP COLUMN result_stdout;
   ```

3. **Credentials, options, callbacks:**
   ```sql
   ALTER TABLE jobs ADD COLUMN credentials JSON;
   ALTER TABLE jobs ADD COLUMN options JSON;
   ALTER TABLE jobs ADD COLUMN callback_url VARCHAR(1024);
   ALTER TABLE jobs ADD COLUMN callback_sent BOOLEAN DEFAULT FALSE;
   ```

4. **API authentication:**
   ```sql
   ALTER TABLE jobs ADD COLUMN api_key_id VARCHAR(36);
   ALTER TABLE jobs ADD COLUMN worker_id VARCHAR(255);
   ALTER TABLE jobs ADD FOREIGN KEY (api_key_id) REFERENCES api_keys(id);
   ```

### result_stats: JSON vs Separate Columns

**Current (JSON blob - ansible-runner native format):**
```sql
result_stats JSON  -- {"ok": {"localhost": 1}, "changed": {}, "failures": {}, ...}
```

Note: ansible-runner returns stats organized by stat type, then hosts. This format is more
convenient for aggregation (e.g., summing failures across all hosts).

**Original design (separate columns):**
```sql
hosts_ok INT DEFAULT 0,
hosts_failed INT DEFAULT 0,
hosts_unreachable INT DEFAULT 0,
hosts_skipped INT DEFAULT 0,
```

**Comparison:**

| Aspect | JSON (current) | Separate Columns |
|--------|----------------|------------------|
| Query filtering | Harder (`JSON_EXTRACT`) | Easy (`WHERE hosts_failed > 0`) |
| Aggregation | Complex | Simple (`SUM(hosts_failed)`) |
| Index support | Limited (virtual columns) | Native |
| Per-host detail | Preserved | Lost (only totals) |
| Schema flexibility | High | Low |
| Storage | Slightly larger | Compact |

**Recommendation:** Migrate to separate columns when:
- You need to query "find all jobs with failures" frequently
- You need aggregation reports (total failures this week)
- Per-host detail can move to `job_host_results` table (per original design)

**Migration when ready:**
```sql
ALTER TABLE jobs ADD COLUMN hosts_ok INT DEFAULT 0;
ALTER TABLE jobs ADD COLUMN hosts_failed INT DEFAULT 0;
ALTER TABLE jobs ADD COLUMN hosts_unreachable INT DEFAULT 0;
ALTER TABLE jobs ADD COLUMN hosts_skipped INT DEFAULT 0;

-- Populate from JSON (aggregating across all hosts)
-- ansible-runner format: {"ok": {"host1": 1, "host2": 1}, "failures": {"host3": 1}}
UPDATE jobs SET
  hosts_ok = (
    SELECT COALESCE(SUM(value), 0)
    FROM JSON_TABLE(result_stats, '$.ok.*' COLUMNS(value INT PATH '$')) AS t
  ),
  hosts_failed = (
    SELECT COALESCE(SUM(value), 0)
    FROM JSON_TABLE(result_stats, '$.failures.*' COLUMNS(value INT PATH '$')) AS t
  )
WHERE result_stats IS NOT NULL;

-- Keep result_stats for per-host detail, or drop if using job_host_results table
```

---

## Reference

- Full production schema: `docs/plans/2026-01-15-ansible-api-service-design.md` Section 5
- Data model rationale: `docs/plans/2026-01-15-ansible-api-service-design.md` Section 5 "Design Decisions"
