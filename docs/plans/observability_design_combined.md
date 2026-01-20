## Section 12: Observability

This covers how we monitor, trace, and debug the system in production.

### Three Pillars

```
┌─────────────────────────────────────────────────────────────────┐
│                      Observability                               │
├─────────────────┬─────────────────┬─────────────────────────────┤
│     Metrics     │     Logs        │     Traces                  │
│  (Prometheus)   │  (Structured)   │  (OpenTelemetry)            │
├─────────────────┼─────────────────┼─────────────────────────────┤
│ - Request rates │ - JSON format   │ - Request → Queue → Worker  │
│ - Job counts    │ - Correlation   │ - Cross-service context     │
│ - Queue depth   │   IDs           │ - Latency breakdown         │
│ - Error rates   │ - Log levels    │ - Error attribution         │
│ - Latencies     │ - Searchable    │                             │
└─────────────────┴─────────────────┴─────────────────────────────┘

┌─────────┬────────────────────────────┬──────────────────────────────────────┐
│ Pillar  │            Tool            │               Purpose                │
├─────────┼────────────────────────────┼──────────────────────────────────────┤
│ Logs    │ Structured JSON → Loki/ELK │ Debug, audit, troubleshoot           │
├─────────┼────────────────────────────┼──────────────────────────────────────┤
│ Metrics │ Prometheus                 │ Monitor health, alerting, dashboards │
├─────────┼────────────────────────────┼──────────────────────────────────────┤
│ Traces  │ OpenTelemetry → Jaeger     │ Track requests across services       │
└─────────┴────────────────────────────┴──────────────────────────────────────┘
```

---

### Metrics (Prometheus)

#### Key Metrics to Expose

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `ansible_api_requests_total` | Counter | method, endpoint, status | Request volume |
| `ansible_api_request_duration_seconds` | Histogram | method, endpoint, status_code | API latency |
| `ansible_api_jobs_submitted_total` | Counter | source_type | Job submission rate |
| `ansible_api_jobs_completed_total` | Counter | status | Job completion by status |
| `ansible_api_job_duration_seconds` | Histogram | source_type | Job execution time |
| `ansible_api_active_jobs` | Gauge | - | Currently running jobs |
| `ansible_api_queue_length` | Gauge | queue_name | Queue backlog |
| `ansible_api_active_workers` | Gauge | - | Number of active workers |
| `ansible_api_webhook_deliveries_total` | Counter | success | Webhook reliability |

#### Implementation

```python
# app/metrics.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Counters
JOBS_SUBMITTED = Counter(
    'ansible_api_jobs_submitted_total',
    'Total jobs submitted',
    ['source_type']
)

JOBS_COMPLETED = Counter(
    'ansible_api_jobs_completed_total',
    'Total jobs completed',
    ['status']  # success, failed, cancelled
)

WEBHOOK_DELIVERIES = Counter(
    'ansible_api_webhook_deliveries_total',
    'Total webhook delivery attempts',
    ['success']  # true, false
)

API_REQUESTS = Counter(
    'ansible_api_requests_total',
    'Total API requests',
    ['method', 'endpoint', 'status']
)

# Histograms
JOB_DURATION = Histogram(
    'ansible_api_job_duration_seconds',
    'Job execution duration',
    ['source_type'],
    buckets=[10, 30, 60, 120, 300, 600, 1800, 3600]
)

API_REQUEST_DURATION = Histogram(
    'ansible_api_request_duration_seconds',
    'API request duration',
    ['method', 'endpoint', 'status_code'],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10]
)

# Gauges
QUEUE_LENGTH = Gauge(
    'ansible_api_queue_length',
    'Current queue length',
    ['queue_name']
)

ACTIVE_WORKERS = Gauge(
    'ansible_api_active_workers',
    'Number of active workers'
)

ACTIVE_JOBS = Gauge(
    'ansible_api_active_jobs',
    'Number of currently running jobs'
)
```

#### FastAPI Metrics Middleware

```python
# app/middleware/metrics.py
from starlette.middleware.base import BaseHTTPMiddleware
import time

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time

        API_REQUEST_DURATION.labels(
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code
        ).observe(duration)

        API_REQUESTS.labels(
            method=request.method,
            endpoint=request.url.path,
            status=response.status_code
        ).inc()

        return response
```

#### Metrics Endpoint

```python
# app/api/metrics.py
from fastapi import APIRouter, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

router = APIRouter()

@router.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
```

#### Key Metrics to Monitor (Alert Thresholds)

| Metric | Alert Threshold |
|--------|-----------------|
| `jobs_completed_total{status="failed"}` | Rate > 10% of total |
| `job_duration_seconds` | p99 > 30 minutes |
| `queue_length` | > 100 for 5 minutes |
| `active_workers` | < 2 for 2 minutes |
| `request_duration_seconds` | p99 > 5 seconds |

---

### Structured Logging

#### Log Format (JSON)

```json
{
  "timestamp": "2026-01-17T10:05:30.123Z",
  "level": "INFO",
  "logger": "ansible_api.worker",
  "message": "Job completed",
  "job_id": "job-abc123",
  "request_id": "req-xyz789",
  "trace_id": "trace-123456",
  "duration_ms": 325000,
  "status": "success",
  "hosts_ok": 2,
  "hosts_failed": 0
}
```

#### Correlation ID Strategy

| Field | Purpose |
|-------|---------|
| `request_id` | Correlate API request → job |
| `job_id` | Track specific job across logs |
| `trace_id` | Link to distributed trace |
| `api_key_name` | Identify client (never log secrets) |

#### Implementation

```python
# app/logging.py
import structlog
import logging

def setup_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()  # JSON output
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

logger = structlog.get_logger()

# Usage in code
logger.info("job_started", job_id=job.id, source_type=payload['source']['type'])
logger.error("job_failed", job_id=job.id, error=str(e), retry_count=job.retry_count)
```

#### What to Log

| Event | Level | Fields |
|-------|-------|--------|
| Job submitted | INFO | job_id, source_type, api_key_name |
| Job started | INFO | job_id, worker_id |
| Job completed | INFO | job_id, status, duration_seconds |
| Job failed | ERROR | job_id, error_type, error_message |
| Credential accessed | INFO | credential_name (never values!) |
| Webhook sent | INFO | job_id, callback_url, status_code |
| Webhook failed | WARN | job_id, callback_url, error |

---

### Distributed Tracing (OpenTelemetry)

#### Trace Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Trace: POST /api/v1/jobs                                        │
├─────────────────────────────────────────────────────────────────┤
│ ├── api.submit_job (50ms)                                       │
│ │   ├── db.create_job (5ms)                                     │
│ │   └── redis.enqueue (3ms)                                     │
│ │                                                                │
│ └── worker.execute (5 min)        ← async, linked by job_id     │
│     ├── fetch_source (30s)                                      │
│     ├── resolve_credentials (10ms)                              │
│     ├── run_playbook (4 min)                                    │
│     ├── upload_logs (5s)                                        │
│     └── send_webhook (200ms)                                    │
└─────────────────────────────────────────────────────────────────┘
```

#### Implementation with Auto-Instrumentation

```python
# app/tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

def setup_tracing(app):
    # Setup tracer
    provider = TracerProvider()
    processor = BatchSpanProcessor(OTLPSpanExporter(
        endpoint="http://jaeger:4317"  # or otel-collector:4317
    ))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    # Auto-instrument libraries
    FastAPIInstrumentor.instrument_app(app)
    RedisInstrumentor().instrument()
    RequestsInstrumentor().instrument()

tracer = trace.get_tracer(__name__)
```

#### Manual Spans for Job Execution

```python
# In worker
def execute(self, job_id: str, payload: dict):
    with tracer.start_as_current_span("ansible_job_execute") as span:
        span.set_attribute("job.id", job_id)
        span.set_attribute("source.type", payload['source']['type'])

        with tracer.start_as_current_span("fetch_source"):
            project_dir = self._fetch_source(payload)

        with tracer.start_as_current_span("resolve_credentials"):
            creds = self._resolve_credentials(payload)

        with tracer.start_as_current_span("run_playbook"):
            result = self._run_playbook(job_id, private_data_dir, payload)

        span.set_attribute("job.status", "success" if result.rc == 0 else "failed")
```

---

### Health Endpoints

```python
# app/api/health.py
from fastapi import APIRouter, HTTPException

router = APIRouter()

@router.get("/health")
async def health():
    """Liveness probe - is the process running?"""
    return {"status": "ok"}

@router.get("/ready")
async def ready():
    """Readiness probe - can we serve traffic?"""
    checks = {
        "database": await check_db(),
        "redis": await check_redis(),
    }

    if all(checks.values()):
        return {"status": "ready", "checks": checks}

    raise HTTPException(503, {"status": "not_ready", "checks": checks})


async def check_db() -> bool:
    """Check database connectivity"""
    try:
        await db.execute("SELECT 1")
        return True
    except Exception:
        return False

async def check_redis() -> bool:
    """Check Redis connectivity"""
    try:
        await redis.ping()
        return True
    except Exception:
        return False
```

---

### Alerting Rules (Prometheus)

```yaml
# prometheus/alerts.yml
groups:
  - name: ansible-api
    rules:
      - alert: HighJobFailureRate
        expr: rate(ansible_api_jobs_completed_total{status="failed"}[5m]) / rate(ansible_api_jobs_completed_total[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Job failure rate > 10%"
          description: "{{ $value | humanizePercentage }} of jobs are failing"

      - alert: QueueBacklog
        expr: ansible_api_queue_length > 100
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Queue depth > 100 for 10 minutes"
          description: "Queue has {{ $value }} pending jobs"

      - alert: WorkerDown
        expr: ansible_api_active_workers < 1
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "No healthy workers"
          description: "All workers are down or unhealthy"

      - alert: HighAPILatency
        expr: histogram_quantile(0.99, rate(ansible_api_request_duration_seconds_bucket[5m])) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "API p99 latency > 5 seconds"
          description: "p99 latency is {{ $value | humanizeDuration }}"

      - alert: LongRunningJobs
        expr: histogram_quantile(0.99, rate(ansible_api_job_duration_seconds_bucket[5m])) > 1800
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Job p99 duration > 30 minutes"
          description: "p99 job duration is {{ $value | humanizeDuration }}"
```

---

### Grafana Dashboard Panels

| Panel | PromQL Query |
|-------|--------------|
| Jobs/minute | `rate(ansible_api_jobs_submitted_total[5m])` |
| Success rate | `rate(ansible_api_jobs_completed_total{status="success"}[5m]) / rate(ansible_api_jobs_completed_total[5m])` |
| Queue depth | `ansible_api_queue_length` |
| Job duration p50/p95/p99 | `histogram_quantile(0.99, rate(ansible_api_job_duration_seconds_bucket[5m]))` |
| Active workers | `ansible_api_active_workers` |
| API latency p95 | `histogram_quantile(0.95, rate(ansible_api_request_duration_seconds_bucket[5m]))` |
| Webhook success rate | `rate(ansible_api_webhook_deliveries_total{success="true"}[5m]) / rate(ansible_api_webhook_deliveries_total[5m])` |

---

### Design Decisions

| Decision | Approach |
|----------|----------|
| Log format | Structured JSON for easy parsing |
| Metrics | Prometheus (standard, wide ecosystem) |
| Tracing | OpenTelemetry (vendor-neutral) |
| Correlation | request_id + job_id + trace_id across all components |
| Health checks | Separate liveness vs readiness probes |
| Log sensitive data | Never log credential values, only names |
| Trace propagation | W3C Trace Context headers |
| Auto-instrumentation | FastAPI, Redis, Requests libraries |

---
### Combination Summary
What was combined:

  From v2 (structure/production-ready):
  - Three Pillars overview with tool mapping
  - Correlation ID strategy table
  - Health endpoints with liveness vs readiness separation
  - Concrete Prometheus alerting rules in YAML format
  - Clean design decisions table

  From v1 (implementation details):
  - Full structlog setup with all processors (filter_by_level, add_logger_name, StackInfoRenderer, format_exc_info)
  - "What to Log" table with specific events, levels, and fields
  - Complete metrics definitions (JOBS_SUBMITTED, JOBS_COMPLETED, WEBHOOK_DELIVERIES, etc.)
  - MetricsMiddleware for FastAPI auto-instrumentation
  - Metrics endpoint implementation
  - OpenTelemetry auto-instrumentation (FastAPIInstrumentor, RedisInstrumentor, RequestsInstrumentor)
  - Manual spans example for worker job execution
  - Grafana dashboard panels with PromQL queries
  - W3C Trace Context headers

  Enhanced:
  - Added check_db() and check_redis() helper implementations for health endpoints
  - Added description annotations to alerting rules
  - Unified metric naming with ansible_api_* prefix throughout
