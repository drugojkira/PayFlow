"""
Outbox Relay — polls the outbox_events table and publishes to Kafka.

Key pattern demonstrated:
  ★ SELECT FOR UPDATE SKIP LOCKED
    Allows multiple relay instances to run concurrently without
    processing the same events. Each instance locks a batch of rows;
    other instances skip already-locked rows.

Flow:
  1. SELECT unpublished events FOR UPDATE SKIP LOCKED
  2. Publish each event to Kafka
  3. Mark events as published
  4. COMMIT

If the relay crashes between steps 2 and 3, the events remain
unpublished and will be re-processed → at-least-once delivery.
"""

import json
import logging

from kafka import KafkaProducer
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import settings
from src.models import OutboxEvent

logger = logging.getLogger(__name__)


class OutboxRelay:
    """Relays outbox events from PostgreSQL to Kafka."""

    def __init__(self, db: Session, producer: KafkaProducer):
        self.db = db
        self.producer = producer

    def relay_events(self, batch_size: int | None = None) -> int:
        """
        Fetch a batch of unpublished events using SELECT FOR UPDATE SKIP LOCKED,
        publish them to Kafka, and mark them as published.

        Returns the number of events relayed.
        """
        if batch_size is None:
            batch_size = settings.OUTBOX_RELAY_BATCH_SIZE

        # ── SELECT FOR UPDATE SKIP LOCKED ───────────────────
        #
        # SKIP LOCKED — если другой воркер уже захватил строку,
        # мы её пропускаем вместо ожидания. Это позволяет запускать
        # несколько relay-инстансов параллельно.
        #
        events = (
            self.db.execute(
                select(OutboxEvent)
                .where(OutboxEvent.published.is_(False))
                .with_for_update(skip_locked=True)  # ← KEY: skip locked rows
                .order_by(OutboxEvent.created_at)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )

        if not events:
            return 0

        published_count = 0
        for event in events:
            try:
                future = self.producer.send(
                    topic=settings.KAFKA_PAYMENT_TOPIC,
                    key=event.aggregate_id.encode("utf-8"),
                    value=json.dumps(event.payload).encode("utf-8"),
                    headers=[
                        ("event_type", event.event_type.encode("utf-8")),
                        ("aggregate_type", event.aggregate_type.encode("utf-8")),
                        ("event_id", str(event.id).encode("utf-8")),
                    ],
                )
                # Wait for delivery confirmation
                future.get(timeout=10)
                event.published = True
                published_count += 1
            except Exception:
                logger.exception(f"Failed to publish event {event.id}")
                # Stop batch on first failure to preserve ordering
                break

        self.db.commit()
        logger.info(f"Relayed {published_count}/{len(events)} outbox events to Kafka")
        return published_count


def create_kafka_producer() -> KafkaProducer:
    """Creates a KafkaProducer with idempotent delivery settings."""
    return KafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        acks="all",
        retries=3,
        max_in_flight_requests_per_connection=1,
    )
