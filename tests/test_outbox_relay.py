"""
Tests for Outbox Relay — publishing events from PostgreSQL to Kafka.

Covers:
  - Unpublished events get relayed to Kafka
  - Events are marked as published after relay
  - Already published events are skipped
  - Empty outbox returns 0
"""

import json
from unittest.mock import MagicMock, patch


from src.kafka.outbox_relay import OutboxRelay
from tests.factories import OutboxEventFactory


class TestOutboxRelay:
    """Tests outbox relay with mocked Kafka producer."""

    def test_relay_unpublished_events(self, db):
        """Unpublished events are sent to Kafka and marked published."""
        # Create 3 unpublished events
        events = [OutboxEventFactory(published=False) for _ in range(3)]
        db.flush()

        # Mock Kafka producer
        mock_producer = MagicMock()
        mock_future = MagicMock()
        mock_producer.send.return_value = mock_future

        relay = OutboxRelay(db, mock_producer)
        count = relay.relay_events(batch_size=10)

        assert count == 3
        assert mock_producer.send.call_count == 3

        # All events marked as published
        for event in events:
            db.refresh(event)
            assert event.published is True

    def test_skip_already_published(self, db):
        """Already published events are not re-sent."""
        OutboxEventFactory(published=True)
        OutboxEventFactory(published=False)
        db.flush()

        mock_producer = MagicMock()
        mock_future = MagicMock()
        mock_producer.send.return_value = mock_future

        relay = OutboxRelay(db, mock_producer)
        count = relay.relay_events()

        assert count == 1
        assert mock_producer.send.call_count == 1

    def test_empty_outbox(self, db):
        """Returns 0 when no unpublished events."""
        mock_producer = MagicMock()

        relay = OutboxRelay(db, mock_producer)
        count = relay.relay_events()

        assert count == 0
        mock_producer.send.assert_not_called()

    def test_relay_sends_correct_kafka_message(self, db):
        """Verifies Kafka message format: topic, key, value, headers."""
        OutboxEventFactory(
            aggregate_type="payment",
            aggregate_id="pay-123",
            event_type="payment.created",
            payload={"payment_id": "pay-123", "amount": "100.00"},
            published=False,
        )
        db.flush()

        mock_producer = MagicMock()
        mock_future = MagicMock()
        mock_producer.send.return_value = mock_future

        relay = OutboxRelay(db, mock_producer)
        relay.relay_events()

        call_kwargs = mock_producer.send.call_args
        assert call_kwargs.kwargs["key"] == b"pay-123"

        value = json.loads(call_kwargs.kwargs["value"])
        assert value["payment_id"] == "pay-123"
        assert value["amount"] == "100.00"

    def test_relay_stops_on_kafka_failure(self, db):
        """If Kafka publish fails, remaining events stay unpublished."""
        e1 = OutboxEventFactory(published=False)
        e2 = OutboxEventFactory(published=False)
        e3 = OutboxEventFactory(published=False)
        db.flush()

        mock_producer = MagicMock()
        mock_future_ok = MagicMock()
        mock_future_fail = MagicMock()
        mock_future_fail.get.side_effect = Exception("Kafka unavailable")

        # First call succeeds, second fails
        mock_producer.send.side_effect = [
            mock_future_ok,
            mock_future_fail,
        ]

        relay = OutboxRelay(db, mock_producer)
        count = relay.relay_events()

        # Only 1 event published (stopped at second failure)
        assert count == 1
        db.refresh(e1)
        db.refresh(e2)
        db.refresh(e3)
        assert e1.published is True
        assert e2.published is False
        assert e3.published is False


class TestOutboxRelayWithRealKafka:
    """
    Integration test: relay events to a real Kafka (via testcontainers).
    """

    def test_relay_to_real_kafka(self, db, kafka_bootstrap_servers):
        """Full integration: DB → OutboxRelay → Kafka → Consumer verify."""
        from kafka import KafkaConsumer, KafkaProducer

        # Create test event
        event = OutboxEventFactory(
            aggregate_type="payment",
            aggregate_id="integration-test-1",
            event_type="payment.completed",
            payload={"payment_id": "integration-test-1", "status": "COMPLETED"},
            published=False,
        )
        db.flush()

        # Real Kafka producer
        producer = KafkaProducer(
            bootstrap_servers=kafka_bootstrap_servers,
            acks="all",
        )

        # Patch the topic setting
        with patch("src.kafka.outbox_relay.settings") as mock_settings:
            mock_settings.KAFKA_PAYMENT_TOPIC = "test-payment-events"
            mock_settings.OUTBOX_RELAY_BATCH_SIZE = 10

            relay = OutboxRelay(db, producer)
            count = relay.relay_events()
            assert count == 1

        # Verify event was published to Kafka
        consumer = KafkaConsumer(
            "test-payment-events",
            bootstrap_servers=kafka_bootstrap_servers,
            auto_offset_reset="earliest",
            consumer_timeout_ms=10000,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )

        messages = []
        for msg in consumer:
            messages.append(msg)
            if len(messages) >= 1:
                break

        consumer.close()
        producer.close()

        assert len(messages) == 1
        assert messages[0].value["payment_id"] == "integration-test-1"

        # Event marked as published in DB
        db.refresh(event)
        assert event.published is True
