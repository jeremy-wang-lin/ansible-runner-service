# Unified Source Field Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify `playbook` and `source` fields into a single `source` field that handles local and git sources for both playbooks and roles.

**Architecture:** Two-level discriminated union - `type` (local/git) determines source location, `target` (playbook/role) determines what executes. Local sources support bundled content baked into container image.

**Tech Stack:** Pydantic discriminated unions, FastAPI, SQLAlchemy

---

## Schema Design

### Source Types (MVP)

```python
class LocalPlaybookSource(BaseModel):
    type: Literal["local"]
    target: Literal["playbook"]
    path: str  # e.g., "deploy/app.yml"

class LocalRoleSource(BaseModel):
    type: Literal["local"]
    target: Literal["role"]
    collection: str  # e.g., "mycompany.infra"
    role: str  # e.g., "nginx"
    role_vars: dict[str, Any] = Field(default_factory=dict)

class GitPlaybookSource(BaseModel):
    type: Literal["git"]
    target: Literal["playbook"]
    repo: str
    branch: str = "main"
    path: str

class GitRoleSource(BaseModel):
    type: Literal["git"]
    target: Literal["role"]
    repo: str
    branch: str = "main"
    role: str
    role_vars: dict[str, Any] = Field(default_factory=dict)

Source = LocalPlaybookSource | LocalRoleSource | GitPlaybookSource | GitRoleSource
```

### Future Source Types (not this iteration)

- `galaxy` + `role` - Ansible Galaxy/Automation Hub
- `s3` + `playbook/role` - S3/MinIO artifacts
- `nexus` + `playbook/role` - Nexus artifacts

## Sync vs Async Support Matrix

**Rule:** Sync works when everything is local (no network I/O).

| Source | Inventory | Sync | Async |
|--------|-----------|------|-------|
| `local` + `playbook` | string/inline | ✓ | ✓ |
| `local` + `playbook` | git | ✗ | ✓ |
| `local` + `role` | string/inline | ✓ | ✓ |
| `local` + `role` | git | ✗ | ✓ |
| `git` + `playbook` | any | ✗ | ✓ |
| `git` + `role` | any | ✗ | ✓ |

## Bundled Content Directory Structure

Container layout for Kubernetes deployment:

```
/app/
├── playbooks/           # Local playbooks
│   ├── hello.yml
│   └── deploy/
│       └── app.yml
├── collections/         # Installed collections
│   └── ansible_collections/
│       └── mycompany/
│           └── infra/
│               ├── galaxy.yml
│               └── roles/
│                   ├── nginx/
│                   └── postgres/
└── src/
    └── ansible_runner_service/
```

Dockerfile example:

```dockerfile
FROM python:3.11-slim

RUN pip install ansible-core

COPY playbooks/ /app/playbooks/
COPY collections/ /app/collections/

# Or install from requirements
COPY requirements.yml /app/
RUN ansible-galaxy collection install -r /app/requirements.yml -p /app/collections/
```

## API Examples

### Local playbook (sync)

```bash
curl -X POST "http://localhost:8000/api/v1/jobs?sync=true" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {"type": "local", "target": "playbook", "path": "hello.yml"},
    "inventory": "localhost,"
  }'
```

### Local role (sync)

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
    "inventory": {"type": "inline", "data": {"webservers": {"hosts": {"10.0.1.10": null}}}}
  }'
```

### Git playbook (async)

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

### Git role (async)

```bash
curl -X POST "http://localhost:8000/api/v1/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "git",
      "target": "role",
      "repo": "https://gitlab.company.com/team/collection.git",
      "role": "nginx"
    },
    "inventory": "webservers,"
  }'
```

## Implementation Changes

| File | Changes |
|------|---------|
| `schemas.py` | New source models, remove `playbook` field, update `JobRequest` |
| `main.py` | Unified handler, remove `_handle_local_source`/`_handle_git_source` split |
| `worker.py` | Handle local role execution (wrapper playbook from bundled collection) |
| `runner.py` | Accept `collections_dir` parameter for `ANSIBLE_COLLECTIONS_PATH` |
| `job_store.py` | Update to store `source_type` + `source_target` |
| `models.py` | Add `source_target` column to jobs table |
| `repository.py` | Update queries for new column |
| `tests/*.py` | Update all tests for new schema |
| `README.md`, `docs/usage-guide.md` | Update documentation |

## Database Migration

```python
# alembic migration
op.add_column('jobs', sa.Column('source_target', sa.String(20), nullable=True))
# Backfill: existing rows based on source_type
```

## Breaking Changes

| Before | After |
|--------|-------|
| `{"playbook": "hello.yml"}` | `{"source": {"type": "local", "target": "playbook", "path": "hello.yml"}}` |
| `{"source": {"type": "playbook", ...}}` | `{"source": {"type": "git", "target": "playbook", ...}}` |
| `{"source": {"type": "role", ...}}` | `{"source": {"type": "git", "target": "role", ...}}` |

No API versioning needed (project not released).
