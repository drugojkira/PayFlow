"""
Celery application configuration.

Broker: RabbitMQ (AMQP)
Tasks: src.worker.tasks

★ Key patterns:
  - Dead Letter Queue (DLQ) — failed messages routed to 'dead_letter' queue
  - Exponential backoff — retry_backoff=True
  - acks_late — ACK only after task completes, not before

Usage:
  celery -A src.worker.celery_app worker -l info -Q default,dead_letter
  celery -A src.worker.celery_app beat -l info
"""

from celery import Celery
from kombu import Exchange, Queue

from src.config import settings

celery_app = Celery(
    "payflow",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["src.worker.tasks"],
)

# ── DLQ: Dead Letter Queue via RabbitMQ ─────────────────────
#
# Когда сообщение отклоняется (reject) или превышает max_retries,
# RabbitMQ перенаправляет его в dead_letter exchange → dead_letter queue.
# Это позволяет инспектировать и повторно обработать failed messages.
#
default_exchange = Exchange("default", type="direct")
dead_letter_exchange = Exchange("dead_letter", type="direct")

celery_app.conf.update(
    # Queue definitions with DLQ routing
    task_queues=(
        Queue(
            "default",
            exchange=default_exchange,
            routing_key="default",
            queue_arguments={
                "x-dead-letter-exchange": "dead_letter",  # ← route rejected msgs here
                "x-dead-letter-routing-key": "dead_letter",
            },
        ),
        Queue(
            "dead_letter",
            exchange=dead_letter_exchange,
            routing_key="dead_letter",
        ),
    ),
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Reliability
    task_acks_late=True,  # ACK after task completes (not before)
    worker_prefetch_multiplier=1,  # Fetch one task at a time
    task_reject_on_worker_lost=True,  # Re-queue if worker dies
    # ★ Exponential backoff for retries
    # retry_backoff=True → delay = backoff * (2 ** (retries - 1))
    # retry 1: 3s, retry 2: 6s, retry 3: 12s (capped by retry_backoff_max)
    task_default_retry_delay=3,
    # Periodic tasks (Celery Beat)
    beat_schedule={
        "relay-outbox-events": {
            "task": "src.worker.tasks.relay_outbox_events",
            "schedule": settings.OUTBOX_RELAY_INTERVAL_SECONDS,
        },
    },
    # Time limits
    task_soft_time_limit=60,
    task_time_limit=120,
)
