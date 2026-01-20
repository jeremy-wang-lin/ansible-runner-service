  Section 13: Observability

  This covers how we monitor, trace, and debug the system in production.

  Three Pillars

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

  Metrics (Prometheus)

  Key metrics to expose:
  ┌──────────────────────────────────────┬───────────┬──────────────────────────┬─────────────────────┐
  │                Metric                │   Type    │          Labels          │       Purpose       │
  ├──────────────────────────────────────┼───────────┼──────────────────────────┼─────────────────────┤
  │ ansible_api_requests_total           │ Counter   │ method, endpoint, status │ Request volume      │
  ├──────────────────────────────────────┼───────────┼──────────────────────────┼─────────────────────┤
  │ ansible_api_request_duration_seconds │ Histogram │ method, endpoint         │ API latency         │
  ├──────────────────────────────────────┼───────────┼──────────────────────────┼─────────────────────┤
  │ ansible_jobs_total                   │ Counter   │ status, source_type      │ Job throughput      │
  ├──────────────────────────────────────┼───────────┼──────────────────────────┼─────────────────────┤
  │ ansible_jobs_duration_seconds        │ Histogram │ status                   │ Job execution time  │
  ├──────────────────────────────────────┼───────────┼──────────────────────────┼─────────────────────┤
  │ ansible_jobs_active                  │ Gauge     │ status                   │ Current job counts  │
  ├──────────────────────────────────────┼───────────┼──────────────────────────┼─────────────────────┤
  │ ansible_queue_depth                  │ Gauge     │ queue_name               │ Queue backlog       │
  ├──────────────────────────────────────┼───────────┼──────────────────────────┼─────────────────────┤
  │ ansible_worker_busy                  │ Gauge     │ worker_id                │ Worker utilization  │
  ├──────────────────────────────────────┼───────────┼──────────────────────────┼─────────────────────┤
  │ ansible_webhook_deliveries_total     │ Counter   │ success                  │ Webhook reliability │
  └──────────────────────────────────────┴───────────┴──────────────────────────┴─────────────────────┘
  Implementation:

  # app/metrics.py
  from prometheus_client import Counter, Histogram, Gauge

  # API metrics
  api_requests = Counter(
      'ansible_api_requests_total',
      'Total API requests',
      ['method', 'endpoint', 'status']
  )

  api_latency = Histogram(
      'ansible_api_request_duration_seconds',
      'API request latency',
      ['method', 'endpoint'],
      buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
  )

  # Job metrics
  jobs_total = Counter(
      'ansible_jobs_total',
      'Total jobs processed',
      ['status', 'source_type']
  )

  jobs_duration = Histogram(
      'ansible_jobs_duration_seconds',
      'Job execution duration',
      ['status'],
      buckets=[10, 30, 60, 120, 300, 600, 1800, 3600]
  )

  jobs_active = Gauge(
      'ansible_jobs_active',
      'Currently active jobs',
      ['status']
  )

  # Queue metrics
  queue_depth = Gauge(
      'ansible_queue_depth',
      'Jobs waiting in queue',
      ['queue_name']
  )

  Structured Logging

  Log format (JSON):

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

  Key fields:
  ┌──────────────┬─────────────────────────────────────┐
  │    Field     │               Purpose               │
  ├──────────────┼─────────────────────────────────────┤
  │ request_id   │ Correlate API request → job         │
  ├──────────────┼─────────────────────────────────────┤
  │ job_id       │ Track specific job across logs      │
  ├──────────────┼─────────────────────────────────────┤
  │ trace_id     │ Link to distributed trace           │
  ├──────────────┼─────────────────────────────────────┤
  │ api_key_name │ Identify client (never log secrets) │
  └──────────────┴─────────────────────────────────────┘
  Implementation:

  # app/logging.py
  import structlog

  structlog.configure(
      processors=[
          structlog.stdlib.add_log_level,
          structlog.processors.TimeStamper(fmt="iso"),
          structlog.processors.JSONRenderer()
      ],
      context_class=dict,
      logger_factory=structlog.stdlib.LoggerFactory(),
  )

  logger = structlog.get_logger()

  # Usage
  logger.info("job_completed",
      job_id=job.id,
      request_id=request_id,
      duration_ms=duration,
      status="success"
  )

  Distributed Tracing (OpenTelemetry)

  Trace flow:

  ┌──────────────────────────────────────────────────────────────────┐
  │ Trace: Submit and execute job                                     │
  ├──────────────────────────────────────────────────────────────────┤
  │                                                                   │
  │  [API] POST /jobs ─────────────────────────────────────────────  │
  │    ├─ [DB] Insert job record ──────                              │
  │    └─ [Redis] Enqueue job ─────────                              │
  │                                                                   │
  │  [Worker] Execute job ───────────────────────────────────────────│
  │    ├─ [Git] Clone repo ────────────────                          │
  │    ├─ [DB] Fetch credentials ──────                              │
  │    ├─ [Ansible] Run playbook ────────────────────────────────────│
  │    ├─ [S3] Upload logs ────────                                  │
  │    └─ [HTTP] Send webhook ─────                                  │
  │                                                                   │
  └──────────────────────────────────────────────────────────────────┘

  Implementation:

  # app/tracing.py
  from opentelemetry import trace
  from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
  from opentelemetry.sdk.trace import TracerProvider
  from opentelemetry.sdk.trace.export import BatchSpanProcessor

  # Setup
  provider = TracerProvider()
  processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="otel-collector:4317"))
  provider.add_span_processor(processor)
  trace.set_tracer_provider(provider)

  tracer = trace.get_tracer("ansible-api")

  # Usage in worker
  with tracer.start_as_current_span("execute_job") as span:
      span.set_attribute("job.id", job_id)
      span.set_attribute("job.source_type", source_type)

      with tracer.start_as_current_span("fetch_source"):
          project_dir = playbook_source.fetch(...)

      with tracer.start_as_current_span("run_playbook"):
          result = ansible_runner.run(...)

  Health Endpoints

  # app/api/health.py

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

  Alerting Rules (Prometheus)

  groups:
    - name: ansible-api
      rules:
        - alert: HighJobFailureRate
          expr: rate(ansible_jobs_total{status="failed"}[5m]) / rate(ansible_jobs_total[5m]) > 0.1
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "Job failure rate > 10%"

        - alert: QueueBacklog
          expr: ansible_queue_depth > 100
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "Queue depth > 100 for 10 minutes"

        - alert: WorkerDown
          expr: sum(ansible_worker_busy) < 1
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: "No healthy workers"

  Design Decisions
  ┌───────────────┬───────────────────────────────────────────┐
  │   Decision    │                 Approach                  │
  ├───────────────┼───────────────────────────────────────────┤
  │ Metrics       │ Prometheus (standard, pull-based)         │
  ├───────────────┼───────────────────────────────────────────┤
  │ Logging       │ Structured JSON (searchable, parseable)   │
  ├───────────────┼───────────────────────────────────────────┤
  │ Tracing       │ OpenTelemetry (vendor-neutral)            │
  ├───────────────┼───────────────────────────────────────────┤
  │ Correlation   │ request_id + job_id + trace_id across all │
  ├───────────────┼───────────────────────────────────────────┤
  │ Health checks │ Separate liveness vs readiness            │
  └───────────────┴───────────────────────────────────────────┘