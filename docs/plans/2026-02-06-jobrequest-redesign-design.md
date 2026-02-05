# JobRequest Redesign — Design

## Overview

**Goal:** Redesign the `JobRequest` API input to support structured inventory (standard Ansible YAML format as JSON, git-sourced files) and ansible-playbook execution options (check, diff, tags, limit, etc.), while maintaining backward compatibility.

**Scope:**
- Inline inventory using standard Ansible YAML structure (as JSON)
- Git-sourced inventory (static files, directories, and dynamic inventory scripts)
- Execution options: check, diff, tags, skip_tags, limit, verbosity
- Backward compatible with existing API format
- Reuse existing git provider/credential infrastructure

## Current State

The `inventory` parameter is a plain string (`str`) defaulting to `"localhost,"`. It passes through the system untouched: API → Redis/DB → worker → `ansible_runner.run(inventory=...)`.

- No validation beyond Python type checking
- No support for groups, host_vars, or structured inventory
- DB column is `String(255)` — cannot hold structured data
- No execution options (check, diff, tags, limit, etc.) — only `extra_vars` is supported

## API Schema

### Three Inventory Formats

**1. String (backward compat)** — comma-separated hosts, same as today:

```json
{
  "playbook": "deploy.yml",
  "inventory": "host1,host2,"
}
```

**2. Inline inventory** — standard Ansible YAML inventory structure as JSON:

```json
{
  "playbook": "deploy.yml",
  "inventory": {
    "type": "inline",
    "data": {
      "webservers": {
        "hosts": {
          "10.0.1.10": { "http_port": "8080" },
          "10.0.1.11": null
        },
        "vars": { "ansible_user": "deploy" }
      }
    }
  }
}
```

The `data` field accepts any valid Ansible YAML inventory structure as JSON. The `all` → `children` wrapper is implicit and can be omitted — Ansible treats top-level keys as children of `all`. Users can also use the explicit form when needed:

```json
{
  "inventory": {
    "type": "inline",
    "data": {
      "all": {
        "children": {
          "datacenter_east": {
            "children": {
              "webservers": { "hosts": { "10.0.1.10": null } },
              "dbservers": { "hosts": { "10.0.1.20": null } }
            }
          }
        }
      }
    }
  }
}
```

**3. Git inventory** — reference a file or directory in a git repo:

```json
{
  "playbook": "deploy.yml",
  "inventory": {
    "type": "git",
    "repo": "https://dev.azure.com/org/project/_git/inventory",
    "branch": "main",
    "path": "production/hosts.yml"
  }
}
```

The `path` can point to:
- A static inventory file (YAML or INI) — parsed by Ansible as-is
- A directory — Ansible merges all inventory sources in the directory
- An executable script — Ansible runs it and expects JSON output on stdout

### Validation Rules

| Field | Required | Validation |
|-------|----------|------------|
| `inventory` (string) | No | Default: `"localhost,"`. Passed directly to ansible-runner |
| `inventory.type` | Yes (object) | Must be `"inline"` or `"git"` |
| `inventory.data` | Yes (inline) | `dict[str, Any]` — any valid Ansible inventory structure |
| `inventory.repo` | Yes (git) | Must be valid URL from allowed provider/org (reuses `git_config.py`) |
| `inventory.branch` | No (git) | Default: `"main"` |
| `inventory.path` | Yes (git) | Relative path, no `..` or leading `/` allowed |

### Pydantic Schema

```python
class InlineInventory(BaseModel):
    type: Literal["inline"]
    data: dict[str, Any]


class GitInventory(BaseModel):
    type: Literal["git"]
    repo: str
    branch: str = "main"
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("Path traversal not allowed")
        return v


StructuredInventory = Annotated[
    Union[InlineInventory, GitInventory],
    Field(discriminator="type"),
]


class ExecutionOptions(BaseModel):
    check: bool = False                                  # --check (dry run)
    diff: bool = False                                   # --diff (show changes)
    tags: list[str] = Field(default_factory=list)        # --tags
    skip_tags: list[str] = Field(default_factory=list)   # --skip-tags
    limit: str | None = None                             # --limit (host pattern)
    verbosity: int = Field(default=0, ge=0, le=4)        # -v through -vvvv
    vault_password_file: str | None = None               # placeholder, deferred


class JobRequest(BaseModel):
    playbook: str | None = Field(default=None, min_length=1)
    source: GitSource | None = None
    extra_vars: dict[str, Any] = Field(default_factory=dict)
    inventory: str | StructuredInventory = "localhost,"
    options: ExecutionOptions = Field(default_factory=ExecutionOptions)
```

`extra_vars` remains at the top level — it's input data to the playbook, not an execution modifier. The `options` object groups ansible-playbook CLI options that control how the playbook is executed.

`vault_password_file` is a placeholder for future implementation. The field is accepted in the schema but not yet wired to ansible-runner.

Pydantic resolves the inventory union as:
- Input is a string → matches `str`
- Input is a dict with `"type": "inline"` → matches `InlineInventory`
- Input is a dict with `"type": "git"` → matches `GitInventory`
- Input is a dict with unknown/missing `type` → validation error

### Worker-Side TypedDicts

```python
class InlineInventoryConfig(TypedDict):
    type: Literal["inline"]
    data: dict[str, Any]


class GitInventoryConfig(TypedDict):
    type: Literal["git"]
    repo: str
    branch: str
    path: str


InventoryConfig = InlineInventoryConfig | GitInventoryConfig


class ExecutionOptionsConfig(TypedDict, total=False):
    check: bool
    diff: bool
    tags: list[str]
    skip_tags: list[str]
    limit: str
    verbosity: int
    vault_password_file: str
```

## Execution Options

### Options Overview

The `options` object groups ansible-playbook CLI options that control execution behavior. All fields are optional with sensible defaults.

| Option | Type | Default | ansible-runner mapping | Description |
|--------|------|---------|----------------------|-------------|
| `check` | `bool` | `false` | `cmdline="--check"` | Dry run — predict changes without applying them |
| `diff` | `bool` | `false` | `cmdline="--diff"` | Show file diffs when templates or files change |
| `tags` | `list[str]` | `[]` | `tags="t1,t2"` (direct param) | Only run tasks tagged with these values |
| `skip_tags` | `list[str]` | `[]` | `skip_tags="t1,t2"` (direct param) | Skip tasks tagged with these values |
| `limit` | `str \| null` | `null` | `limit="pattern"` (direct param) | Limit execution to a host pattern subset |
| `verbosity` | `int` | `0` | `verbosity=N` (direct param) | Verbosity level 0-4 (maps to `-v` through `-vvvv`) |
| `vault_password_file` | `str \| null` | `null` | Deferred | Placeholder for future vault integration |

### ansible-runner Parameter Mapping

`ansible_runner.run()` directly supports `tags`, `skip_tags`, `limit`, and `verbosity` as parameters. It automatically converts `verbosity=3` to `-vvv` internally.

`check` and `diff` are not direct parameters — they are passed via the `cmdline` parameter as raw CLI arguments (e.g., `cmdline="--check --diff"`).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         API Request                             │
│  "inventory": str | { "type": "inline" } | { "type": "git" }   │
│  "options": { "check": true, "tags": [...], ... }               │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Validation                                 │
│  Inventory — String: pass through                               │
│              Inline: validate type + data is dict                │
│              Git: validate repo URL against git_config           │
│  Options — validate types, ranges (verbosity 0-4), defaults     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Store (Redis + MariaDB)                        │
│  Inventory: string as-is, inline/git as JSON object             │
│  Options: stored as JSON object                                 │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Worker                                   │
│  Inventory — String: pass directly to ansible_runner.run()      │
│              Inline: yaml.dump(data) → write file → pass path   │
│              Git: clone repo → validate path → pass path        │
│  Options — tags/skip_tags/limit/verbosity: direct params        │
│            check/diff: passed via cmdline                       │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### String (backward compat)

No change. Passed directly to `ansible_runner.run(inventory="host1,host2,")`.

### Inline (`type: "inline"`)

1. API receives `data` dict, stores as JSON in DB and Redis
2. Worker receives the dict, calls `yaml.dump(data)`, writes it to a file in `private_data_dir`
3. Passes the file path to `ansible_runner.run(inventory="/path/to/inventory.yml")`

### Git (`type: "git"`)

1. API receives repo/branch/path, validates repo URL against `git_config.py`, stores config as JSON in DB and Redis
2. Worker clones the repo using `git_service.clone_repo()` (reuses existing infrastructure)
3. Validates the path exists within the cloned repo
4. If path is a file: passes the file path to `ansible_runner.run()`
5. If path is a directory: passes the directory path — Ansible merges all inventory sources in the directory
6. For executable scripts: git clone preserves the executable bit (`100755`), so Ansible detects the file as executable and runs it as a dynamic inventory script

### Worker Dispatch

```python
def execute_job(
    job_id: str,
    playbook: str,
    extra_vars: dict[str, Any],
    inventory: str | InventoryConfig,
    options: ExecutionOptionsConfig | None = None,
    source_config: SourceConfig | None = None,
) -> None:
    # --- Resolve inventory ---
    if isinstance(inventory, str):
        inv_arg = inventory
    elif inventory["type"] == "inline":
        inv_arg = _write_inline_inventory(inventory["data"], tmpdir)
    elif inventory["type"] == "git":
        inv_arg = _resolve_git_inventory(inventory, envvars)

    # --- Build ansible-runner kwargs ---
    run_kwargs = dict(
        private_data_dir=tmpdir,
        playbook=playbook_path,
        inventory=inv_arg,
        extravars=extra_vars,
    )

    if options:
        # Direct ansible-runner parameters
        if options.get("tags"):
            run_kwargs["tags"] = ",".join(options["tags"])
        if options.get("skip_tags"):
            run_kwargs["skip_tags"] = ",".join(options["skip_tags"])
        if options.get("limit"):
            run_kwargs["limit"] = options["limit"]
        if options.get("verbosity"):
            run_kwargs["verbosity"] = options["verbosity"]

        # cmdline-based options (not directly supported by ansible-runner)
        cmdline_parts = []
        if options.get("check"):
            cmdline_parts.append("--check")
        if options.get("diff"):
            cmdline_parts.append("--diff")
        if cmdline_parts:
            run_kwargs["cmdline"] = " ".join(cmdline_parts)
```

## Database Changes

### Migration

Alembic migration to change the `inventory` column:

```sql
-- From
inventory VARCHAR(255) NOT NULL

-- To
inventory JSON NOT NULL
```

The `JSON` column stores:
- A plain string (`"host1,host2,"`) for backward compat
- A JSON object (`{"type": "inline", "data": {...}}`) for inline
- A JSON object (`{"type": "git", "repo": "...", ...}`) for git

### SQLAlchemy Model

```python
class JobModel(Base):
    # Change from:
    # inventory: Mapped[str] = mapped_column(String(255), nullable=False)
    # To:
    inventory: Mapped[Any] = mapped_column(JSON, nullable=False)

    # New column:
    options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

`options` is nullable — existing rows and requests without options store `NULL`.

### Redis (Job Dataclass)

```python
@dataclass
class Job:
    # Change from:
    # inventory: str
    # To:
    inventory: str | dict

    # New field:
    options: dict | None = None
```

Redis serialization already uses JSON, so dicts serialize naturally.

## Git Inventory Details

### Credential & Provider Reuse

Git inventory repos use the same `git_config.py` provider/credential system as playbook sources:
- Repo URL must match a configured provider host and allowed organization
- Credentials are looked up from the provider's `credential_env`
- Same `git_service.clone_repo()` function handles the clone

### Dynamic Inventory Scripts

Git tracks files as either `100644` (not executable) or `100755` (executable). When `ansible_runner.run()` receives an inventory file path:
- Not executable → Ansible parses it as static inventory (YAML or INI)
- Executable → Ansible runs it and expects JSON output on stdout

Script dependencies and environment variables are the user's responsibility — the worker environment must be pre-configured with whatever the script needs. This is the same trust model as playbook execution: if the repo is in an allowed provider/org, its contents are trusted.

### Path Validation

Same rules as `GitPlaybookSource`:
- No `..` components (path traversal)
- No leading `/` (absolute path)
- Path must exist within the cloned repo (checked at execution time)

`GitInventory` and `GitPlaybookSource` are intentionally separate models despite sharing `repo`, `branch`, `path` fields — playbook paths must be `.yml` files while inventory paths can be files, directories, or executable scripts.

## Security Considerations

- **Inline inventory**: No security surface beyond what Ansible itself enforces. The `data` dict is written as YAML and parsed by Ansible's inventory loader.
- **Git inventory**: Same trust boundary as git playbook sources — repo must be in an allowed provider/org. No new credential exposure.
- **Dynamic scripts**: Arbitrary code execution, but within the same trust boundary as playbooks (which are also arbitrary code). The provider/org allowlist is the security gate.
- **Path traversal**: Blocked by `..` and `/` validation on `GitInventory.path`.

## Testing Strategy

### Unit Tests

- Pydantic schema validation: string, inline, git, invalid inputs
- `ExecutionOptions` validation: defaults, verbosity range, tag lists
- `_write_inline_inventory()`: generates correct YAML from dict
- Path validation: reject traversal attempts
- Backward compatibility: existing string format still accepted, omitted options default correctly

### Integration Tests

- Inline inventory with groups and host_vars → ansible-runner executes successfully
- Git inventory clone → file path passed to ansible-runner
- Git inventory directory → directory path passed to ansible-runner
- Worker dispatch: correct branch taken for each inventory type
- Execution options: check mode produces no changes, tags filter tasks, limit filters hosts

### Database Tests

- Migration: existing rows with string inventory survive migration
- JSON column stores and retrieves all three inventory formats correctly
- Options column stores and retrieves correctly, NULL for existing rows

## Backward Compatibility

- Existing API callers sending `"inventory": "host1,host2,"` continue to work with no changes
- Default remains `"localhost,"` when `inventory` is omitted
- `options` is entirely optional — omitting it uses all defaults (no check, no diff, no tags, etc.)
- Database migration preserves existing string inventory values in the new JSON column
- Existing rows get `NULL` for the new `options` column
- No breaking changes to the response schema

## Full API Example

```json
{
  "playbook": "deploy.yml",
  "inventory": {
    "type": "inline",
    "data": {
      "webservers": {
        "hosts": {
          "10.0.1.10": { "http_port": "8080" },
          "10.0.1.11": null
        },
        "vars": { "ansible_user": "deploy" }
      }
    }
  },
  "extra_vars": { "app_version": "2.1.0" },
  "options": {
    "check": true,
    "diff": true,
    "tags": ["deploy", "config"],
    "limit": "webservers",
    "verbosity": 2
  }
}
```

Minimal payload (all defaults, fully backward compatible):

```json
{
  "playbook": "hello.yml"
}
```

## Out of Scope

- Dynamic inventory plugins (`amazon.aws.aws_ec2`, etc.) — users can put these in the playbook repo or use a git inventory script
- External inventory URL — not needed if git covers the "inventory from remote source" use case
- Per-request credentials — reuse server-side provider credentials
- Inventory caching — fresh clone per job, same as playbook sources
- Vault password file implementation — field is a placeholder for future work

---

## Summary

| Aspect | Decision |
|--------|----------|
| Inline format | Standard Ansible YAML inventory structure as JSON |
| `data` validation | `dict[str, Any]` — Ansible is the authority on structure |
| Git inventory | Static files, directories, and executable scripts |
| Git credentials | Reuse existing `git_config.py` provider system |
| Script trust model | Provider/org allowlist (same as playbooks) |
| Dynamic script env | User's responsibility to configure worker environment |
| Path target | File or directory |
| Execution options | Nested under `options` object: check, diff, tags, skip_tags, limit, verbosity |
| `extra_vars` placement | Top-level (input data, not execution modifier) |
| Vault password file | Placeholder in schema, implementation deferred |
| ansible-runner mapping | tags/skip_tags/limit/verbosity as direct params; check/diff via `cmdline` |
| Database columns | `inventory`: `String(255)` → `JSON`; new `options`: `JSON` nullable |
| Backward compat | All new fields optional with sensible defaults |
