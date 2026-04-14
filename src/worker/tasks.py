"""
Celery tasks for the payment gateway.

★ Key patterns:
  - Exponential backoff: retry_backoff=True → delay doubles each attempt
  - retry_backoff_max: caps the maximum delay
  - retry_jitter: adds randomness to avoid thundering herd
  - DLQ: when max_retries exhausted, task is rejected → RabbitMQ routes to dead_letter queue

Tasks:
  - process_payment     — simulates external payment processing
  - send_notification   — sends notification about payment status
  - relay_outbox_events — periodic: polls outbox → publishes to Kafka
"""

import logging
import time
from uuid import UUID

from celery.exceptions import MaxRetriesExceededError

from src.metrics import (
    OUTBOX_EVENTS_RELAYED,
    PAYMENT_PROCESSING_DURATION,
    PAYMENTS_COMPLETED,
    PAYMENTS_FAILED,
    TASK_DLQ,
    TASK_RETRIES,
)
from src.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=5,
    acks_late=True,
    # ★ Exponential backoff
    retry_backoff=True,       # delay = retry_backoff * (2 ** retries)
    retry_backoff_max=300,    # cap: 5 minutes max
    retry_jitter=True,        # add randomness to prevent thundering herd
)
def process_payment(self, payment_id: str):
    """
    Celery task: processes a payment asynchronously.

    Retry timeline (with retry_backoff=True, base=1s):
      attempt 1: immediate
      retry  1: ~1s
      retry  2: ~2s
      retry  3: ~4s
      retry  4: ~8s
      retry  5: ~16s
    After 5 retries → MaxRetriesExceededError → payment marked FAILED → DLQ.
    """
    from src.database import SessionLocal
    from src.services.payment import PaymentService

    logger.info(
        f"[task] Processing payment {payment_id} "
        f"(attempt {self.request.retries + 1}/{self.max_retries + 1})"
    )
    db = SessionLocal()
    start = time.perf_counter()
    try:
        service = PaymentService(db)
        payment = service.complete_payment(UUID(payment_id))
        logger.info(f"[task] Payment {payment_id} → {payment.status.value}")

        PAYMENTS_COMPLETED.inc()
        PAYMENT_PROCESSING_DURATION.observe(time.perf_counter() - start)

        # Chain: send notification after successful processing
        send_notification.delay(payment_id, payment.status.value)

    except MaxRetriesExceededError:
        # ★ All retries exhausted — mark payment as FAILED
        # Message will be rejected → RabbitMQ routes it to DLQ
        logger.error(f"[task] Payment {payment_id}: max retries exhausted → DLQ")
        _mark_payment_failed(payment_id, "Max retries exhausted")
        PAYMENTS_FAILED.labels(reason="max_retries").inc()
        TASK_DLQ.labels(task_name="process_payment").inc()
        # Reject the message (don't requeue) → goes to DLQ
        self.reject(requeue=False)

    except Exception as exc:
        logger.warning(
            f"[task] Payment {payment_id} failed (attempt "
            f"{self.request.retries + 1}): {exc}"
        )
        TASK_RETRIES.labels(task_name="process_payment").inc()
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            logger.error(f"[task] Payment {payment_id}: max retries exhausted → DLQ")
            _mark_payment_failed(payment_id, f"Failed after retries: {exc}")
            PAYMENTS_FAILED.labels(reason="max_retries").inc()
            TASK_DLQ.labels(task_name="process_payment").inc()
    finally:
        db.close()


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def send_notification(self, payment_id: str, status: str):
    """
    Celery task: sends a notification about payment status.
    In real life: email, SMS, push notification, webhook callback.
    """
    logger.info(f"[task] Notification: payment {payment_id} → {status}")
    # Here you would integrate with SendGrid, Twilio, Firebase, etc.


@celery_app.task
def relay_outbox_events():
    """
    Periodic Celery task (runs via Celery Beat).
    Polls outbox_events table and publishes unpublished events to Kafka.
    """
    from src.database import SessionLocal
    from src.kafka.outbox_relay import OutboxRelay, create_kafka_producer

    db = SessionLocal()
    try:
        producer = create_kafka_producer()
        relay = OutboxRelay(db, producer)
        count = relay.relay_events()
        if count > 0:
            logger.info(f"[task] Relayed {count} outbox events to Kafka")
            OUTBOX_EVENTS_RELAYED.inc(count)
        producer.close()
    except Exception:
        logger.exception("[task] Outbox relay failed")
    finally:
        db.close()


def _mark_payment_failed(payment_id: str, reason: str):
    """Helper: mark a payment as FAILED after all retries exhausted."""
    from src.database import SessionLocal
    from src.services.payment import PaymentService

    db = SessionLocal()
    try:
        service = PaymentService(db)
        service.fail_payment(UUID(payment_id), reason=reason)
        logger.info(f"[task] Payment {payment_id} marked FAILED: {reason}")
    except Exception:
        logger.exception(f"[task] Could not mark payment {payment_id} as failed")
    finally:
        db.close()
