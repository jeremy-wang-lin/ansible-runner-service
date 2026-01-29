# ADR: Role Execution Strategy

**Date:** 2026-01-29
**Status:** Accepted

## Context

We need to support executing Ansible roles from Git repositories. The design requires deciding:

1. What format roles are stored in (standalone role, multi-role repo, or Ansible Collection)
2. How to install and execute them on the worker
3. How users specify which role to run in the API request

## Decision Drivers

- Roles are stored as **Ansible Collections** (Format C) with `galaxy.yml`, `roles/`, and `plugins/` directories
- Standard Ansible tooling should be used where possible
- Users shouldn't need to know internal filesystem paths
- Both short role names and fully qualified collection names (FQCN) should be supported

## Considered Options

### Role Format

| Format | Description | Fit |
|--------|-------------|-----|
| **A: Standalone role** | Repo root is a role (`tasks/`, `meta/`, etc.) | Not our case |
| **B: Multi-role repo** | Repo contains `roles/` directory with multiple roles | Not our case |
| **C: Ansible Collection** | Repo has `galaxy.yml`, `roles/`, `plugins/` | **Our format** |

### Execution Strategy

#### Option 1: `ansible-galaxy install` from Git (Chosen)

Install the collection using `ansible-galaxy`, then reference roles by FQCN.

```bash
ansible-galaxy collection install git+https://repo.git,branch -p /tmp/job-xxx/collections
```

Generated wrapper playbook:
```yaml
---
- name: Run role from Git
  hosts: all
  roles:
    - role: mycompany.infra.nginx
      vars:
        nginx_port: 8080
```

Run with:
```bash
ANSIBLE_COLLECTIONS_PATH=/tmp/job-xxx/collections ansible-playbook wrapper.yml
```

**Pros:**
- Uses standard Ansible tooling
- Handles collection dependencies automatically
- Roles are referenced by FQCN (idiomatic Ansible)
- Plugins from the collection are also available

**Cons:**
- Requires `galaxy.yml` in the repo
- Slightly slower than raw clone (galaxy install overhead)

#### Option 2: Clone + `roles_path`

Clone the repo and set `ANSIBLE_ROLES_PATH` to the cloned directory.

```bash
git clone https://repo.git /tmp/job-xxx/repo
ANSIBLE_ROLES_PATH=/tmp/job-xxx/repo/roles ansible-playbook wrapper.yml
```

**Pros:**
- Simple, fast
- No galaxy install overhead

**Cons:**
- Doesn't support collections (only loose roles)
- Collection plugins not available
- Not idiomatic for collections
- Dependencies not resolved

#### Option 3: Clone + absolute path in playbook

Clone the repo and reference the role by filesystem path.

```yaml
roles:
  - role: /tmp/job-xxx/roles/nginx
```

**Pros:**
- Simplest implementation

**Cons:**
- Exposes filesystem paths (not portable)
- Not idiomatic Ansible
- No collection support
- No dependency resolution

### FQCN Resolution

#### Approach A: User always specifies FQCN

API request: `"role": "mycompany.infra.nginx"`

**Pros:** Simple implementation  
**Cons:** Users must know namespace and collection name

#### Approach B: Derive FQCN from `galaxy.yml`

API request: `"role": "nginx"` -> system reads `galaxy.yml` -> `mycompany.infra.nginx`

**Pros:** Simpler for users  
**Cons:** Requires parsing `galaxy.yml`

#### Approach C: Combined (Chosen)

If role name contains dots, treat as FQCN. Otherwise, derive from `galaxy.yml`.

```python
if "." in role:
    fqcn = role                                # "mycompany.infra.nginx"
else:
    fqcn = f"{namespace}.{collection}.{role}"  # "nginx" -> read galaxy.yml
```

**Pros:** Flexible, covers both use cases  
**Cons:** Slightly more complex implementation

## Decision

1. **Role format:** Ansible Collection (Format C)
2. **Execution strategy:** Option 1 - `ansible-galaxy collection install` from Git
3. **FQCN resolution:** Approach C - Combined (auto-detect short name vs FQCN)

## Consequences

### Positive

- Standard Ansible tooling for installation and execution
- Collection plugins available alongside roles
- Users can use short names (convenience) or FQCN (precision)
- Dependencies handled by `ansible-galaxy`

### Negative

- Repos must be valid Ansible Collections with `galaxy.yml`
- `ansible-galaxy install` adds overhead vs raw clone
- FQCN auto-derivation requires reading `galaxy.yml` from installed collection

### Risks

- If a repo lacks `galaxy.yml`, collection install will fail. Mitigation: clear error message indicating the repo must be a valid Ansible Collection.
- Short role names that happen to contain dots (unlikely) would be misinterpreted as FQCN. Mitigation: FQCN requires exactly two dots (`namespace.collection.role`); validate this pattern.
