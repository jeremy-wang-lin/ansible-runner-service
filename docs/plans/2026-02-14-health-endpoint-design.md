# Health Endpoint Design

> **Status:** Ready for implementation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add health check endpoints for Kubernetes liveness/readiness probes and debugging observability.

**Architecture:** Three endpoints with increasing detail - live (process), ready (dependencies), details (full status).

**Tech Stack:** FastAPI, Redis, SQLAlchemy

---

## Endpoint Overview

| Endpoint | Purpose | Response |
|----------|---------|----------|
| `GET /health/live` | Kubernetes liveness probe | `200 OK` if process running |
| `GET /health/ready` | Kubernetes readiness probe | `200 OK` if Redis + MariaDB reachable, else `503` |
| `GET /health/details` | Debugging/observability | Full JSON with dependencies, workers, metrics, versions |

### Behavior

- `/health/live` - Always returns 200 (if FastAPI is responding, process is alive)
- `/health/ready` - Checks Redis PING and MariaDB `SELECT 1`; returns 503 if either fails
- `/health/details` - Always returns 200 with status info (reports dependency status even when down)

### Kubernetes Usage

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
```

---

## Response Schemas

### `/health/live`

```json
{"status": "ok"}
```

### `/health/ready` (success - 200)

```json
{"status": "ok"}
```

### `/health/ready` (failure - 503)

```json
{"status": "error", "reason": "mariadb unreachable"}
```

### `/health/details`

```json
{
  "status": "ok",
  "dependencies": {
    "redis": {"status": "ok", "latency_ms": 2},
    "mariadb": {"status": "ok", "latency_ms": 5}
  },
  "workers": {
    "count": 3,
    "queues": ["default"]
  },
  "metrics": {
    "queue_depth": 12,
    "jobs_last_hour": 47
  },
  "version": {
    "app": "0.1.0",
    "ansible_core": "2.20.2",
    "python": "3.11.5"
  }
}
```

### Status Values

- `"ok"` - Dependency or service is healthy
- `"error"` - Dependency or service has failed

---

## Implementation Approach

| Component | Location | Changes |
|-----------|----------|---------|
| Health endpoints | `main.py` | Add 3 new routes under `/health` prefix |
| Dependency checks | `health.py` (new) | `check_redis()`, `check_mariadb()`, `get_worker_info()` |
| Metrics queries | `repository.py` | Add `count_jobs_since()` for jobs_last_hour |
| Version info | `health.py` | Read from `importlib.metadata` and `ansible --version` |

### No Database Migration

Read-only queries only - no schema changes required.

---

## Testing Strategy

| Test | Description |
|------|-------------|
| `test_health_live_returns_ok` | Always returns 200 with `{"status": "ok"}` |
| `test_health_ready_success` | Returns 200 when Redis + MariaDB reachable |
| `test_health_ready_redis_down` | Returns 503 when Redis unreachable |
| `test_health_ready_mariadb_down` | Returns 503 when MariaDB unreachable |
| `test_health_details_structure` | Validates full JSON structure |
| `test_health_details_latency_measured` | Confirms latency_ms is populated |
| `test_health_details_worker_count` | Confirms worker info from Redis |
| `test_health_details_versions` | Confirms app/ansible/python versions present |

### Mocking Approach

- Mock Redis/MariaDB connections for failure tests
- Use real connections for integration tests (existing docker-compose setup)

---

## Security Considerations

- **No authentication** for now - endpoints are internal use only
- Network-level security via Kubernetes ClusterIP (not exposed via Ingress)
- Can add optional API key auth later if needed (env var `HEALTH_DETAILS_API_KEY`)

---

## Future Enhancements (not this iteration)

- Prometheus metrics endpoint (`/metrics`)
- Custom probe timeouts via query params
- Historical health data
