# Authentication Design

## Overview

Per-client API key authentication for the ansible-runner-service REST API. Each calling service gets its own API key, stored as a SHA-256 hash in MariaDB. Admin operations managed via bootstrap key from environment variable.

## Deployment Context

- Internal services behind Istio ingress gateway
- Many internal service clients
- Istio provides mTLS between services; app-level auth adds defense-in-depth

## Architecture

```
Istio Ingress Gateway
        │
        ▼
FastAPI Middleware (auth)
  │
  ├─ /health/*   → No auth (K8s probes)
  ├─ /admin/*    → ADMIN_API_KEY (env var)
  ├─ /api/v1/*   → Per-client API key (DB)
  └─ Other paths → No auth (docs, openapi.json)
        │
        ▼
Endpoint handlers (unchanged)
```

- **Middleware-based**: Auth check before any endpoint logic
- **X-API-Key header**: Standard API key header
- **SHA-256 hashed** keys in DB: Never store plaintext
- **In-memory cache**: Load on startup, reload on admin changes

## Database Schema

New `clients` table:

```sql
CREATE TABLE clients (
    id          VARCHAR(36) PRIMARY KEY,   -- UUID
    name        VARCHAR(255) NOT NULL UNIQUE,  -- e.g., "svc-deploy"
    api_key_hash VARCHAR(64) NOT NULL,     -- SHA-256 hex digest
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at  DATETIME NULL              -- NULL = active, set = revoked
);
```

- **Key generation**: Random 32-byte hex token (64 chars), generated on client creation
- **Plaintext returned once** in creation response, only hash stored
- **Soft-delete**: `revoked_at` set on revocation (audit trail preserved)

## Admin Endpoints

Protected by `ADMIN_API_KEY` environment variable.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/admin/clients` | Create new client → returns plaintext key once |
| `GET` | `/admin/clients` | List all clients (no keys shown) |
| `DELETE` | `/admin/clients/{name}` | Revoke a client (soft-delete) |

### Request/Response

```bash
# Create client
curl -X POST http://localhost:8000/admin/clients \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "svc-deploy"}'

# Response (201)
{"name": "svc-deploy", "api_key": "a1b2c3...64chars", "created_at": "2026-02-27T10:00:00Z"}

# Use client key
curl http://localhost:8000/api/v1/jobs -H "X-API-Key: a1b2c3...64chars"

# Revoke client
curl -X DELETE http://localhost:8000/admin/clients/svc-deploy \
  -H "X-API-Key: $ADMIN_API_KEY"
```

### Error Responses

| Status | When | Body |
|--------|------|------|
| 401 | Missing X-API-Key header | `{"detail": "Missing API key"}` |
| 401 | Invalid or revoked key | `{"detail": "Invalid API key"}` |
| 409 | Client name already exists | `{"detail": "Client already exists"}` |
| 404 | Revoke non-existent client | `{"detail": "Client not found"}` |

## Auth Middleware Flow

```
Request arrives
    │
    ├─ /health/* → Pass through
    ├─ /admin/*  → Hash X-API-Key → compare to ADMIN_API_KEY hash → 401 or pass
    ├─ /api/*    → Hash X-API-Key → lookup in cache → 401 or pass
    └─ Other     → Pass through
```

### Cache Strategy

| Aspect | Detail |
|--------|--------|
| Load | On startup, load all active clients into `dict[key_hash → client_name]` |
| Lookup | Hash incoming key, check dict. O(1) per request |
| Invalidation | Admin create/revoke triggers immediate cache reload |
| No TTL | Cache changes only via admin endpoints |

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ADMIN_API_KEY` | Yes (if auth enabled) | None | Admin key for `/admin/*` endpoints |
| `AUTH_ENABLED` | No | `true` | Set to `false` to disable auth (dev/testing) |

`AUTH_ENABLED=false` allows existing tests to pass without modification and simplifies local development.

## Test Strategy

| Test | Verifies |
|------|----------|
| Health endpoints without auth | `/health/*` always exempt |
| API returns 401 without key | Middleware rejects unauthenticated |
| API returns 401 with invalid key | Invalid keys rejected |
| API passes with valid client key | Happy path |
| Admin create returns key once | Key generation and response |
| Admin revoke invalidates key | Revoked keys rejected immediately |
| Admin rejects client keys | Only ADMIN_API_KEY works for admin |
| Cache reload after changes | New/revoked keys effective without restart |

## Status

Ready for implementation.
