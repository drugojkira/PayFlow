"""
Unit tests for PaymentService.

Covers:
  - Successful payment creation (debit/credit + outbox event)
  - Insufficient funds
  - Account not found
  - Idempotency key (duplicate prevention)
  - Payment completion and failure flows
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from src.models import OutboxEvent, PaymentStatus
from src.services.payment import (
    AccountNotFoundError,
    InsufficientFundsError,
    PaymentService,
)
from tests.factories import AccountFactory


class TestCreatePayment:
    """Tests for PaymentService.create_payment"""

    def test_successful_payment(self, db):
        """Payment создаётся, баланс списывается, outbox event записывается."""
        sender = AccountFactory(balance=Decimal("500.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        payment = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("200.00"),
        )

        assert payment.status == PaymentStatus.PENDING
        assert payment.amount == Decimal("200.00")

        # Check balances updated
        db.refresh(sender)
        db.refresh(receiver)
        assert sender.balance == Decimal("300.00")
        assert receiver.balance == Decimal("300.00")

        # Check outbox event was written
        events = (
            db.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == str(payment.id))
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].event_type == "payment.created"
        assert events[0].published is False

    def test_insufficient_funds(self, db):
        """Payment отклоняется, если баланс недостаточен."""
        sender = AccountFactory(balance=Decimal("50.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        with pytest.raises(InsufficientFundsError):
            service.create_payment(
                from_account_id=sender.id,
                to_account_id=receiver.id,
                amount=Decimal("200.00"),
            )

        # Balances unchanged
        db.refresh(sender)
        assert sender.balance == Decimal("50.00")

    def test_sender_not_found(self, db):
        """Ошибка, если отправитель не найден."""
        import uuid

        receiver = AccountFactory()

        service = PaymentService(db)
        with pytest.raises(AccountNotFoundError, match="Sender account"):
            service.create_payment(
                from_account_id=uuid.uuid4(),
                to_account_id=receiver.id,
                amount=Decimal("100.00"),
            )

    def test_receiver_not_found(self, db):
        """Ошибка, если получатель не найден."""
        import uuid

        sender = AccountFactory()

        service = PaymentService(db)
        with pytest.raises(
            AccountNotFoundError, match="Sender account|Receiver account"
        ):
            service.create_payment(
                from_account_id=sender.id,
                to_account_id=uuid.uuid4(),
                amount=Decimal("100.00"),
            )

    def test_idempotency_key_prevents_duplicate(self, db):
        """Повторный вызов с тем же idempotency_key возвращает существующий платёж."""
        sender = AccountFactory(balance=Decimal("500.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        payment1 = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("100.00"),
            idempotency_key="unique-key-123",
        )

        payment2 = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("100.00"),
            idempotency_key="unique-key-123",
        )

        assert payment1.id == payment2.id

        # Balance debited only once
        db.refresh(sender)
        assert sender.balance == Decimal("400.00")


class TestCompletePayment:
    """Tests for PaymentService.complete_payment"""

    def test_complete_pending_payment(self, db):
        """Pending → Completed, outbox event записывается."""
        sender = AccountFactory(balance=Decimal("500.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        payment = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("100.00"),
        )

        completed = service.complete_payment(payment.id)
        assert completed.status == PaymentStatus.COMPLETED

        # Check two outbox events: created + completed
        events = (
            db.execute(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == str(payment.id))
                .order_by(OutboxEvent.created_at)
            )
            .scalars()
            .all()
        )
        assert len(events) == 2
        assert events[0].event_type == "payment.created"
        assert events[1].event_type == "payment.completed"


class TestFailPayment:
    """Tests for PaymentService.fail_payment"""

    def test_fail_with_refund(self, db):
        """Failed payment → баланс возвращается отправителю."""
        sender = AccountFactory(balance=Decimal("500.00"))
        receiver = AccountFactory(balance=Decimal("100.00"))

        service = PaymentService(db)
        payment = service.create_payment(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount=Decimal("200.00"),
        )

        # Sender: 500 - 200 = 300, Receiver: 100 + 200 = 300
        db.refresh(sender)
        db.refresh(receiver)
        assert sender.balance == Decimal("300.00")
        assert receiver.balance == Decimal("300.00")

        # Fail → refund
        failed = service.fail_payment(payment.id, reason="external gateway error")
        assert failed.status == PaymentStatus.FAILED

        # Balances restored
        db.refresh(sender)
        db.refresh(receiver)
        assert sender.balance == Decimal("500.00")
        assert receiver.balance == Decimal("100.00")

        # Outbox: created + failed
        events = (
            db.execute(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == str(payment.id))
                .order_by(OutboxEvent.created_at)
            )
            .scalars()
            .all()
        )
        assert len(events) == 2
        assert events[1].event_type == "payment.failed"
        assert events[1].payload["reason"] == "external gateway error"
