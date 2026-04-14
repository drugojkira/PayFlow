"""
Prometheus metrics for the payment gateway.

★ Metrics exposed at /metrics endpoint:
  - payments_created_total      — counter of created payments
  - payments_completed_total    — counter of completed payments
  - payments_failed_total       — counter of failed payments
  - payment_processing_seconds  — histogram of payment processing duration
  - outbox_events_relayed_total — counter of outbox events relayed to Kafka
  - outbox_lag_gauge            — gauge: number of unpublished outbox events

Plus automatic HTTP metrics from prometheus-fastapi-instrumentator:
  - http_requests_total
  - http_request_duration_seconds
  - http_request_size_bytes
  - http_response_size_bytes
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Payment Counters ────────────────────────────────────────

PAYMENTS_CREATED = Counter(
    "payments_created_total",
    "Total number of payments created",
    ["from_account", "to_account"],
)

PAYMENTS_COMPLETED = Counter(
    "payments_completed_total",
    "Total number of payments completed",
)

PAYMENTS_FAILED = Counter(
    "payments_failed_total",
    "Total number of payments failed",
    ["reason"],
)

# ── Payment Duration ────────────────────────────────────────

PAYMENT_PROCESSING_DURATION = Histogram(
    "payment_processing_seconds",
    "Time spent processing a payment in Celery worker",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

# ── Outbox Metrics ──────────────────────────────────────────

OUTBOX_EVENTS_RELAYED = Counter(
    "outbox_events_relayed_total",
    "Total number of outbox events published to Kafka",
)

OUTBOX_LAG = Gauge(
    "outbox_lag_events",
    "Number of unpublished outbox events (lag)",
)

# ── Celery Task Metrics ─────────────────────────────────────

TASK_RETRIES = Counter(
    "celery_task_retries_total",
    "Total number of Celery task retries",
    ["task_name"],
)

TASK_DLQ = Counter(
    "celery_task_dlq_total",
    "Total number of tasks sent to Dead Letter Queue",
    ["task_name"],
)
