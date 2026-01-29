# Git Playbook Source Design

## Overview

**Goal:** Allow playbooks and roles to be fetched from Git repositories (Azure DevOps and GitLab) at runtime, instead of requiring local files.

**Scope:**
- Support playbook and role sources from Git
- Two Git providers: Azure DevOps and GitLab
- Organization-level credentials stored server-side (K8s secrets)
- Fresh clone per job execution (no caching)
- Backward compatible with existing local playbook option

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         API Request                              │
│  { "source": { "repo": "https://...", "path": "deploy.yml" } }  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      URL Validation                              │
│  1. Parse repo URL → extract host + org                         │
│  2. Check host matches configured provider                       │
│  3. Check org is in allowed list                                 │
│  4. Reject if not allowed (400 Bad Request)                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Worker                                    │
│  1. Look up credential for provider                              │
│  2. Clone repo to temp directory                                 │
│  3. Execute playbook/role                                        │
│  4. Clean up temp directory                                      │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration

### Git Providers Config

Environment variable or config file:

```yaml
# config/git_providers.yaml (or via environment)
providers:
  - type: azure
    host: dev.azure.com
    orgs:
      - xxxit
      - xxxplatform
    credential_env: AZURE_PAT

  - type: gitlab
    host: gitlab.company.com
    orgs:
      - platform-team
      - infra
    credential_env: GITLAB_TOKEN
```

### Kubernetes Deployment

```yaml
# ConfigMap for provider config
apiVersion: v1
kind: ConfigMap
metadata:
  name: ansible-runner-config
data:
  git_providers.yaml: |
    providers:
      - type: azure
        host: dev.azure.com
        orgs: ["xxxit", "xxxplatform"]
        credential_env: AZURE_PAT
      - type: gitlab
        host: gitlab.company.com
        orgs: ["platform-team", "infra"]
        credential_env: GITLAB_TOKEN

---
# Secret for credentials
apiVersion: v1
kind: Secret
metadata:
  name: ansible-runner-git-credentials
type: Opaque
stringData:
  AZURE_PAT: "azure-pat-token-here"
  GITLAB_TOKEN: "gitlab-access-token-here"
```

## API Schema

### Request Schema

```python
# Option 1: Local playbook (existing, backward compatible)
{
    "playbook": "hello.yml",
    "extra_vars": {"name": "World"},
    "inventory": "localhost,"
}

# Option 2: Git playbook source
{
    "source": {
        "type": "playbook",
        "repo": "https://dev.azure.com/xxxit/project/_git/ansible-playbooks",
        "branch": "main",          # optional, default: "main"
        "path": "deploy/app.yml"
    },
    "extra_vars": {"env": "prod"},
    "inventory": "localhost,"
}

# Option 3: Git role source
{
    "source": {
        "type": "role",
        "repo": "https://gitlab.company.com/platform-team/ansible-roles.git",
        "branch": "v2.0.0",        # optional, default: "main"
        "path": "roles/nginx",
        "role_vars": {             # optional, passed to role
            "nginx_port": 8080
        }
    },
    "inventory": "webservers,"
}
```

### Validation Rules

| Field | Required | Validation |
|-------|----------|------------|
| `source.type` | Yes | Must be "playbook" or "role" |
| `source.repo` | Yes | Must be valid URL from allowed provider/org |
| `source.branch` | No | Default: "main" |
| `source.path` | Yes | Relative path, no ".." allowed |
| `source.role_vars` | No | Only for type="role" |

### Error Responses

```json
// Repo not from allowed organization
{
    "detail": "Repository not allowed: host 'github.com' is not configured"
}

// Org not in allowed list
{
    "detail": "Repository not allowed: org 'unknown-org' is not in allowed list for dev.azure.com"
}

// Invalid path
{
    "detail": "Invalid path: path traversal not allowed"
}
```

## Git Clone Implementation

### Provider-Specific Auth

| Provider | URL Format | Auth Method |
|----------|------------|-------------|
| Azure DevOps | `https://dev.azure.com/org/project/_git/repo` | PAT embedded in URL |
| GitLab | `https://gitlab.company.com/group/repo.git` | Token embedded in URL |

### Clone Commands

```python
# Azure DevOps
# Original: https://dev.azure.com/xxxit/project/_git/repo
# With auth: https://{PAT}@dev.azure.com/xxxit/project/_git/repo
git clone --branch main --depth 1 https://{PAT}@dev.azure.com/xxxit/project/_git/repo /tmp/job-xxx

# GitLab
# Original: https://gitlab.company.com/group/repo.git
# With auth: https://oauth2:{TOKEN}@gitlab.company.com/group/repo.git
git clone --branch main --depth 1 https://oauth2:{TOKEN}@gitlab.company.com/group/repo.git /tmp/job-xxx
```

### Clone Options

- `--depth 1`: Shallow clone (faster, less disk)
- `--branch {branch}`: Specific branch/tag
- `--single-branch`: Only fetch specified branch

## Role Execution

For role sources, the worker generates a wrapper playbook:

```yaml
# Generated wrapper playbook for role execution
---
- name: Run role from Git
  hosts: all
  gather_facts: true
  roles:
    - role: /tmp/job-xxx/roles/nginx
      vars:
        nginx_port: 8080
```

## File Structure

### New Files

```
src/ansible_runner_service/
├── git_service.py          # Git clone logic
├── git_config.py           # Provider configuration
└── schemas.py              # Updated with GitSource, RoleSource

config/
└── git_providers.yaml      # Provider configuration (example)
```

### Modified Files

```
src/ansible_runner_service/
├── main.py                 # Updated job submission endpoint
├── worker.py               # Handle source types, clone repos
├── runner.py               # Support running from temp directories
└── schemas.py              # Add source schemas
```

## Security Considerations

### URL Validation

```python
def validate_repo_url(url: str, providers: list) -> tuple[str, str, str]:
    """
    Validate repo URL against allowed providers and orgs.

    Returns: (provider_type, host, org)
    Raises: ValueError if not allowed
    """
    parsed = urlparse(url)
    host = parsed.netloc

    # Find matching provider
    provider = next((p for p in providers if p['host'] == host), None)
    if not provider:
        raise ValueError(f"host '{host}' is not configured")

    # Extract org from path
    org = extract_org_from_path(parsed.path, provider['type'])

    if org not in provider['orgs']:
        raise ValueError(f"org '{org}' is not in allowed list for {host}")

    return provider['type'], host, org
```

### Path Validation

```python
def validate_path(path: str) -> None:
    """Ensure path doesn't escape repo directory."""
    if ".." in path or path.startswith("/"):
        raise ValueError("path traversal not allowed")
```

### Credential Security

- Credentials stored in K8s Secrets only
- Never logged or included in error messages
- Embedded in clone URL (in-memory only)
- Temp directories cleaned up after execution

### Audit Logging

```python
logger.info(
    "git_clone",
    job_id=job_id,
    repo=repo_url,        # URL without credentials
    branch=branch,
    provider=provider_type,
    org=org,
)
```

## Database Schema

No changes required. The job record stores:
- `playbook`: For local playbooks, the filename. For Git sources, the path within repo.
- `extra_vars`: Unchanged

Future consideration: Add `source_repo`, `source_branch` columns for audit trail.

## Testing Strategy

### Unit Tests

- URL validation (allowed/rejected orgs)
- Path validation (reject traversal)
- Provider matching logic
- Credential lookup

### Integration Tests

- Clone from test repo (mock Git server or real test repo)
- Execute playbook from cloned repo
- Role wrapper generation
- Cleanup after execution

### E2E Tests

- Full flow: submit job with Git source → clone → execute → verify result

## Backward Compatibility

The existing API format continues to work:

```json
// This still works - uses local playbooks directory
{"playbook": "hello.yml"}
```

Detection logic:
```python
if request.playbook:
    # Legacy mode: local playbook
    source_type = "local"
    playbook_path = playbooks_dir / request.playbook
elif request.source:
    # New mode: Git source
    source_type = request.source.type
    # Clone and get path...
```

## Future Enhancements (Not in MVP)

1. **Repo caching** - Cache clones between jobs for performance
2. **SSH key auth** - Support SSH keys in addition to tokens
3. **Nexus/S3 sources** - Bundled artifacts from artifact repositories
4. **Galaxy collections** - Install collections from Ansible Galaxy
5. **Source audit columns** - Track repo/branch in database

---

## Summary

| Aspect | Decision |
|--------|----------|
| Source types | Playbook and Role from Git |
| Git providers | Azure DevOps + GitLab |
| Credential storage | K8s Secrets / env vars |
| Credential in API | No - server-side only |
| Caching | None (fresh clone per job) |
| Backward compatible | Yes - local playbook still works |
