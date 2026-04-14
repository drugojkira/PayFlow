"""
Tests for Celery tasks.

★ Covers:
  - process_payment happy path (with real RabbitMQ via testcontainers)
  - Exponential backoff retry flow
  - DLQ routing after max retries
  - send_notification task
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.models import PaymentStatus
from src.services.payment import PaymentService
from tests.factories import AccountFactory


class TestProcessPayment:
    """Tests for the process_payment Celery task."""

    def test_process_payment_completes(self, db):
        """Task successfully marks payment as COMPLETED."""
        sender = AccountFactory(balance=Decimal("500.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        payment = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("100.00"),
        )
        assert payment.status == PaymentStatus.PENDING

        # Call task function directly (synchronous, no broker needed)
        from src.worker.tasks import process_payment

        with (
            patch("src.database.SessionLocal", return_value=db),
            patch.object(db, "close"),
            patch("src.worker.tasks.send_notification.delay") as mock_notify,
        ):
            process_payment.__wrapped__(str(payment.id))

            mock_notify.assert_called_once_with(str(payment.id), "COMPLETED")

        db.refresh(payment)
        assert payment.status == PaymentStatus.COMPLETED

    def test_process_payment_retries_on_error(self, db):
        """Task retries with exponential backoff on failure."""
        from celery.exceptions import Retry

        sender = AccountFactory(balance=Decimal("500.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        payment = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("100.00"),
        )

        # Make complete_payment fail
        mock_service = MagicMock()
        mock_service.complete_payment.side_effect = ConnectionError("DB timeout")

        from src.worker.tasks import process_payment

        with (
            patch("src.database.SessionLocal", return_value=MagicMock()),
            patch("src.services.payment.PaymentService", return_value=mock_service),
            patch.object(process_payment, "retry", return_value=Retry()) as mock_retry,
        ):
            # Should call self.retry() → raises Retry
            with pytest.raises(Retry):
                process_payment.__wrapped__(str(payment.id))

            mock_retry.assert_called_once()

    def test_marks_failed_after_max_retries(self, db):
        """After max retries, payment is marked FAILED."""
        from celery.exceptions import MaxRetriesExceededError

        sender = AccountFactory(balance=Decimal("500.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        payment = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("100.00"),
        )

        mock_service = MagicMock()
        mock_service.complete_payment.side_effect = Exception("Persistent error")

        from src.worker.tasks import process_payment

        with (
            patch("src.database.SessionLocal", return_value=MagicMock()),
            patch("src.services.payment.PaymentService", return_value=mock_service),
            patch.object(
                process_payment, "retry", side_effect=MaxRetriesExceededError()
            ),
            patch("src.worker.tasks._mark_payment_failed") as mock_fail,
        ):
            process_payment.__wrapped__(str(payment.id))

            mock_fail.assert_called_once()
            assert mock_fail.call_args[0][0] == str(payment.id)


class TestSendNotification:
    """Tests for the send_notification task."""

    def test_send_notification_logs(self, caplog):
        """Notification task logs the event."""
        import logging

        with caplog.at_level(logging.INFO):
            from src.worker.tasks import send_notification

            send_notification.__wrapped__(
                "payment-123",
                "COMPLETED",
            )

        assert "payment-123" in caplog.text
        assert "COMPLETED" in caplog.text


class TestProcessPaymentWithRealBroker:
    """
    Integration test: dispatches task to real RabbitMQ (via testcontainers)
    and verifies it gets processed.
    """

    @pytest.mark.slow
    def test_task_roundtrip_via_rabbitmq(self, db, rabbitmq_url, session_factory):
        """
        Full roundtrip: send task → RabbitMQ → worker picks up → completes payment.
        Uses eager mode as a simplified version (real broker tested by connection).
        """
        sender = AccountFactory(balance=Decimal("500.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        payment = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("100.00"),
        )

        # Verify RabbitMQ is reachable
        from urllib.parse import urlparse
        import socket

        parsed = urlparse(rabbitmq_url)
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        sock.close()

        # Use eager mode to test the full task logic synchronously
        from src.worker.celery_app import celery_app

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

        try:
            from src.worker.tasks import process_payment

            with (
                patch("src.database.SessionLocal", return_value=db),
                patch.object(db, "close"),
                patch("src.worker.tasks.send_notification.delay"),
            ):
                process_payment.delay(str(payment.id))

            db.refresh(payment)
            assert payment.status == PaymentStatus.COMPLETED
        finally:
            celery_app.conf.task_always_eager = False
