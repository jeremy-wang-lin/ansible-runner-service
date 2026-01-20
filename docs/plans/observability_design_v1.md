⏺ Section 12: Observability                                                                                     11:54 PM  claude-opus-4-5-20251101

  Three pillars:
  ┌─────────┬────────────────────────────┬──────────────────────────────────────┐
  │ Pillar  │            Tool            │               Purpose                │
  ├─────────┼────────────────────────────┼──────────────────────────────────────┤
  │ Logs    │ Structured JSON → Loki/ELK │ Debug, audit, troubleshoot           │
  ├─────────┼────────────────────────────┼──────────────────────────────────────┤
  │ Metrics │ Prometheus                 │ Monitor health, alerting, dashboards │
  ├─────────┼────────────────────────────┼──────────────────────────────────────┤
  │ Traces  │ OpenTelemetry → Jaeger     │ Track requests across services       │
  └─────────┴────────────────────────────┴──────────────────────────────────────┘
  ---
  Logging:

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

  Log output (JSON):

  {
    "timestamp": "2026-01-17T10:00:05Z",
    "level": "info",
    "event": "job_started",
    "job_id": "job-abc123",
    "source_type": "nexus",
    "worker_id": "worker-0"
  }

  What to log:
  ┌─────────────────────┬───────┬───────────────────────────────────┐
  │        Event        │ Level │              Fields               │
  ├─────────────────────┼───────┼───────────────────────────────────┤
  │ Job submitted       │ INFO  │ job_id, source_type, api_key_name │
  ├─────────────────────┼───────┼───────────────────────────────────┤
  │ Job started         │ INFO  │ job_id, worker_id                 │
  ├─────────────────────┼───────┼───────────────────────────────────┤
  │ Job completed       │ INFO  │ job_id, status, duration_seconds  │
  ├─────────────────────┼───────┼───────────────────────────────────┤
  │ Job failed          │ ERROR │ job_id, error_type, error_message │
  ├─────────────────────┼───────┼───────────────────────────────────┤
  │ Credential accessed │ INFO  │ credential_name (never values!)   │
  ├─────────────────────┼───────┼───────────────────────────────────┤
  │ Webhook sent        │ INFO  │ job_id, callback_url, status_code │
  ├─────────────────────┼───────┼───────────────────────────────────┤
  │ Webhook failed      │ WARN  │ job_id, callback_url, error       │
  └─────────────────────┴───────┴───────────────────────────────────┘
  ---
  Metrics (Prometheus):

  # app/metrics.py
  from prometheus_client import Counter, Histogram, Gauge, generate_latest

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

  FastAPI metrics middleware:

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

          return response

  Metrics endpoint:

  # app/api/metrics.py
  from fastapi import APIRouter
  from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

  router = APIRouter()

  @router.get("/metrics")
  async def metrics():
      return Response(
          content=generate_latest(),
          media_type=CONTENT_TYPE_LATEST
      )

  Key metrics to monitor:
  ┌───────────────────────────────────────┬─────────────────────┐
  │                Metric                 │   Alert threshold   │
  ├───────────────────────────────────────┼─────────────────────┤
  │ jobs_completed_total{status="failed"} │ Rate > 10% of total │
  ├───────────────────────────────────────┼─────────────────────┤
  │ job_duration_seconds                  │ p99 > 30 minutes    │
  ├───────────────────────────────────────┼─────────────────────┤
  │ queue_length                          │ > 100 for 5 minutes │
  ├───────────────────────────────────────┼─────────────────────┤
  │ active_workers                        │ < 2 for 2 minutes   │
  ├───────────────────────────────────────┼─────────────────────┤
  │ request_duration_seconds              │ p99 > 5 seconds     │
  └───────────────────────────────────────┴─────────────────────┘
  ---
  Distributed Tracing (OpenTelemetry):

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
          endpoint="http://jaeger:4317"
      ))
      provider.add_span_processor(processor)
      trace.set_tracer_provider(provider)

      # Auto-instrument
      FastAPIInstrumentor.instrument_app(app)
      RedisInstrumentor().instrument()
      RequestsInstrumentor().instrument()

  tracer = trace.get_tracer(__name__)

  Manual spans for job execution:

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

  Trace flow:

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

  ---
  Grafana dashboard panels:
  ┌──────────────────────────┬───────────────────────────────────────────────────────────────────────────────────┐
  │          Panel           │                                       Query                                       │
  ├──────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │ Jobs/minute              │ rate(ansible_api_jobs_submitted_total[5m])                                        │
  ├──────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │ Success rate             │ rate(jobs_completed_total{status="success"}[5m]) / rate(jobs_completed_total[5m]) │
  ├──────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │ Queue depth              │ ansible_api_queue_length                                                          │
  ├──────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │ Job duration p50/p95/p99 │ histogram_quantile(0.99, job_duration_seconds_bucket)                             │
  ├──────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │ Active workers           │ ansible_api_active_workers                                                        │
  ├──────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │ API latency              │ histogram_quantile(0.95, request_duration_seconds_bucket)                         │
  └──────────────────────────┴───────────────────────────────────────────────────────────────────────────────────┘
  ---
  Design decisions:
  ┌────────────────────┬─────────────────────────────────────────┐
  │      Decision      │                Approach                 │
  ├────────────────────┼─────────────────────────────────────────┤
  │ Log format         │ Structured JSON for easy parsing        │
  ├────────────────────┼─────────────────────────────────────────┤
  │ Metrics            │ Prometheus (standard, wide ecosystem)   │
  ├────────────────────┼─────────────────────────────────────────┤
  │ Tracing            │ OpenTelemetry (vendor-neutral)          │
  ├────────────────────┼─────────────────────────────────────────┤
  │ Log sensitive data │ Never log credential values, only names │
  ├────────────────────┼─────────────────────────────────────────┤
  │ Trace propagation  │ W3C Trace Context headers               │
  └────────────────────┴─────────────────────────────────────────┘