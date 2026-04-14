"""
Kafka Consumer — downstream processing of payment events.

★ Demonstrates:
  - Consumer group (несколько инстансов делят партиции между собой)
  - At-least-once delivery (commit offset ПОСЛЕ обработки)
  - Graceful shutdown
  - Event routing по event_type

Запуск:
  python -m src.kafka.consumer
"""

import json
import logging
import signal
import sys
from typing import Any, Callable

from kafka import KafkaConsumer
from kafka.consumer.fetcher import ConsumerRecord

from src.config import settings

logger = logging.getLogger(__name__)


class PaymentEventConsumer:
    """
    Consumes payment events from Kafka and routes them to handlers.

    Uses manual offset commit (enable_auto_commit=False) to guarantee
    at-least-once processing: offset is committed only AFTER the handler
    succeeds. If the consumer crashes mid-processing, the message
    will be re-delivered on restart.
    """

    def __init__(self, bootstrap_servers: str | None = None, group_id: str = "payment-consumer"):
        self.bootstrap_servers = bootstrap_servers or settings.KAFKA_BOOTSTRAP_SERVERS
        self.group_id = group_id
        self._running = False
        self._consumer: KafkaConsumer | None = None

        # Event type → handler mapping
        self._handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "payment.created": self._handle_payment_created,
            "payment.completed": self._handle_payment_completed,
            "payment.failed": self._handle_payment_failed,
        }

    def start(self):
        """Start consuming events. Blocks until shutdown."""
        self._consumer = KafkaConsumer(
            settings.KAFKA_PAYMENT_TOPIC,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,       # ← manual commit for at-least-once
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            consumer_timeout_ms=1000,       # poll timeout
        )

        self._running = True
        logger.info(
            f"Kafka consumer started | group={self.group_id} "
            f"topic={settings.KAFKA_PAYMENT_TOPIC} "
            f"servers={self.bootstrap_servers}"
        )

        while self._running:
            try:
                # poll() returns messages accumulated during timeout
                messages = self._consumer.poll(timeout_ms=1000)
                for tp, records in messages.items():
                    for record in records:
                        self._process_record(record)

                # Commit offsets after successful processing
                if messages:
                    self._consumer.commit()

            except Exception:
                logger.exception("Error processing Kafka messages")

        self._consumer.close()
        logger.info("Kafka consumer stopped")

    def stop(self):
        """Signal the consumer to stop gracefully."""
        logger.info("Shutting down Kafka consumer...")
        self._running = False

    def _process_record(self, record: ConsumerRecord):
        """Routes a Kafka record to the appropriate handler by event_type."""
        # Extract event_type from headers
        headers = {k: v.decode("utf-8") for k, v in record.headers} if record.headers else {}
        event_type = headers.get("event_type", "unknown")
        payload = record.value

        logger.info(
            f"Received event | type={event_type} "
            f"partition={record.partition} offset={record.offset} "
            f"key={record.key}"
        )

        handler = self._handlers.get(event_type)
        if handler:
            handler(payload)
        else:
            logger.warning(f"No handler for event_type={event_type}")

    # ── Event Handlers ──────────────────────────────────────

    def _handle_payment_created(self, payload: dict[str, Any]):
        """
        Handle payment.created event.
        Example: update analytics, send webhook to merchant, start fraud check.
        """
        payment_id = payload.get("payment_id")
        amount = payload.get("amount")
        logger.info(f"[handler] Payment created: {payment_id}, amount={amount}")
        # In real life:
        # - Send webhook to merchant
        # - Start fraud detection pipeline
        # - Update real-time analytics dashboard

    def _handle_payment_completed(self, payload: dict[str, Any]):
        """
        Handle payment.completed event.
        Example: send receipt, update ledger, trigger payout.
        """
        payment_id = payload.get("payment_id")
        logger.info(f"[handler] Payment completed: {payment_id}")
        # In real life:
        # - Send receipt email
        # - Update accounting ledger
        # - Trigger merchant payout

    def _handle_payment_failed(self, payload: dict[str, Any]):
        """
        Handle payment.failed event.
        Example: notify customer, create support ticket, alert ops.
        """
        payment_id = payload.get("payment_id")
        reason = payload.get("reason")
        logger.info(f"[handler] Payment failed: {payment_id}, reason={reason}")
        # In real life:
        # - Notify customer of failure
        # - Create support ticket
        # - Alert operations team


def main():
    """Entry point for standalone consumer process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    consumer = PaymentEventConsumer()

    # Graceful shutdown on SIGINT/SIGTERM
    def shutdown(sig, frame):
        consumer.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    consumer.start()


if __name__ == "__main__":
    main()
