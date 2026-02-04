# Inventory Support — Design Analysis

## Current State

The `inventory` parameter is a plain string (`str`) defaulting to `"localhost,"`.
It passes through the entire system untouched — API → Redis/DB → worker → `ansible_runner.run(inventory=...)`.

- No validation beyond Python type checking
- No support for groups, host_vars, or structured inventory
- All tests use `"localhost,"` only
- DB column is `String(255)` — cannot hold structured data

## What the Design Document Specifies

The original design (`docs/plans/2026-01-15-ansible-api-service-design.md`) defines four inventory options:

### Option A: Inline hosts

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

### Option B: Dynamic inventory plugin

```json
"inventory": {
  "type": "dynamic",
  "plugin": "amazon.aws.aws_ec2",
  "config": { "regions": ["us-west-2"], "filters": { "tag:Environment": "production" } },
  "cloud_credential": "aws-prod"
}
```

### Option C: Inventory file from Git

```json
"inventory": {
  "type": "git",
  "repo": "git@github.com:org/inventory.git",
  "branch": "main",
  "path": "production/hosts.yml",
  "git_credential": "github-deploy-key"
}
```

### Option D: External inventory URL

```json
"inventory": {
  "type": "url",
  "url": "https://cmdb.internal.com/api/ansible/inventory",
  "format": "yaml",
  "headers": { "Authorization": "Bearer ${CMDB_TOKEN}" }
}
```

## Recommendation: Implement Option A First

### Why Option A

Most API callers already know their target hosts — they come from a deployment pipeline, a ticket, or an orchestrator. Inline hosts with groups and host_vars covers the majority of real use cases:

- Deploy to a known set of IPs with role-based grouping
- Pass per-host variables (ports, credentials, paths)
- No external dependencies (no cloud credentials, no git, no HTTP)

### Why defer B, C, D

Each introduces significant new dependencies:

- **B (dynamic)**: Requires cloud credential management, plugin installation, and the caller could achieve this by putting inventory plugin config in the playbook repo instead.
- **C (git)**: Overlaps with the existing git source infrastructure but would need separate clone/auth for inventory repos. Could reuse `git_service.py` but adds complexity.
- **D (url)**: Requires HTTP fetching with auth headers, format parsing, and error handling for external service failures.

These are worth building when there's a concrete use case driving them.

## Implementation Considerations

### Backward compatibility

The current API accepts `"inventory": "localhost,"` as a plain string. Changing it to a structured object breaks existing callers. The implementation should support both:

- **String**: Treat as comma-separated host list (current behavior). This is the legacy format.
- **Object**: Use the new structured format with `type`, `hosts`, `groups`, `host_vars`.

Pydantic's discriminated union or a custom validator can handle this.

### Database migration required

The `inventory` column is currently `String(255)`. Structured inventory with groups and host_vars won't fit. Options:

- Change to `JSON` column (consistent with `extra_vars`)
- Or `Text` storing serialized JSON

This requires an Alembic migration.

### ansible-runner integration

`ansible_runner.run()` accepts `inventory` as either:
- A comma-separated host string
- A path to an inventory file/directory

For structured inline inventory, the worker will need to generate an inventory file (YAML or INI) in the `private_data_dir` and pass its path to ansible-runner. This is the `write_inventory()` pattern described in the original design document's worker pseudocode.

### Validation

The design document includes host validation (hostname/IP regex). The implementation should validate:

- Hosts are valid hostnames or IP addresses
- Group names are valid Ansible group names
- Hosts referenced in groups exist in the hosts list
- Reasonable limits on list sizes

## Scope for Next Feature Branch

**In scope:**
- Pydantic schema for `InlineInventory` with `hosts`, `groups`, `host_vars`
- Backward-compatible `inventory` field accepting string or object
- Worker generates inventory YAML file from structured input
- DB migration: `inventory` column from `String(255)` to `JSON`
- Host/group validation
- Tests covering groups, host_vars, and backward compatibility

**Out of scope (deferred):**
- Dynamic inventory plugins (Option B)
- Git-based inventory (Option C)
- URL-based inventory (Option D)
- Credential management for inventory sources
