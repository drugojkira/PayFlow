"""
Payment Service — core business logic.

Key patterns demonstrated:
  ★ SELECT FOR UPDATE     — pessimistic row-level locking to prevent double-spending
  ★ Transactional Outbox  — event written in the SAME DB transaction as state change
  ★ Idempotency Key       — prevents duplicate payment creation on retries
  ★ Ordered locking       — accounts locked in sorted order to prevent deadlocks
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import Account, OutboxEvent, Payment, PaymentStatus


# ── Exceptions ──────────────────────────────────────────────


class InsufficientFundsError(Exception):
    """Raised when sender account has insufficient balance."""


class AccountNotFoundError(Exception):
    """Raised when a referenced account does not exist."""


class DuplicatePaymentError(Exception):
    """Raised when idempotency_key already exists."""


# ── Service ─────────────────────────────────────────────────


class PaymentService:
    """
    Stateless service that operates within a provided DB session.
    The caller is responsible for session lifecycle.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Create Payment ──────────────────────────────────────

    def create_payment(
        self,
        from_account_id: uuid.UUID,
        to_account_id: uuid.UUID,
        amount: Decimal,
        idempotency_key: str | None = None,
    ) -> Payment:
        """
        Creates a payment, debits sender, credits receiver.

        Flow:
          1. Check idempotency key (skip if duplicate)
          2. SELECT FOR UPDATE on both accounts (sorted order → no deadlocks)
          3. Validate balance
          4. Debit sender / credit receiver
          5. INSERT payment record
          6. INSERT outbox event (same transaction!)
          7. COMMIT
        """

        # ── Step 1: Idempotency ─────────────────────────────
        if idempotency_key:
            existing = self.db.execute(
                select(Payment).where(Payment.idempotency_key == idempotency_key)
            ).scalar_one_or_none()
            if existing:
                return existing

        # ── Step 2: SELECT FOR UPDATE ───────────────────────
        #
        # Блокируем обе строки accounts FOR UPDATE.
        # Сортируем по id, чтобы все транзакции блокировали строки
        # в одном и том же порядке → исключаем deadlock.
        #
        account_ids = sorted([from_account_id, to_account_id])
        accounts = (
            self.db.execute(
                select(Account)
                .where(Account.id.in_(account_ids))
                .with_for_update()  # ← PESSIMISTIC ROW-LEVEL LOCK
                .order_by(Account.id)
            )
            .scalars()
            .all()
        )

        account_map = {acc.id: acc for acc in accounts}
        sender = account_map.get(from_account_id)
        receiver = account_map.get(to_account_id)

        if not sender:
            raise AccountNotFoundError(f"Sender account {from_account_id} not found")
        if not receiver:
            raise AccountNotFoundError(f"Receiver account {to_account_id} not found")

        # ── Step 3: Validate balance ────────────────────────
        if sender.balance < amount:
            raise InsufficientFundsError(
                f"Account {from_account_id} balance={sender.balance}, required={amount}"
            )

        # ── Step 4: Debit / Credit ──────────────────────────
        sender.balance -= amount
        receiver.balance += amount

        # ── Step 5: Create payment record ───────────────────
        payment = Payment(
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount=amount,
            status=PaymentStatus.PENDING,
            idempotency_key=idempotency_key,
        )
        self.db.add(payment)
        self.db.flush()  # flush to get generated payment.id

        # ── Step 6: Transactional Outbox ────────────────────
        #
        # Записываем событие В ТУ ЖЕ транзакцию, что и изменение баланса.
        # Это гарантирует, что если транзакция откатится, событие тоже
        # не появится. А если коммитнется — событие точно будет в outbox.
        #
        outbox_event = OutboxEvent(
            aggregate_type="payment",
            aggregate_id=str(payment.id),
            event_type="payment.created",
            payload={
                "payment_id": str(payment.id),
                "from_account_id": str(from_account_id),
                "to_account_id": str(to_account_id),
                "amount": str(amount),
                "status": PaymentStatus.PENDING.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.db.add(outbox_event)

        # ── Step 7: Commit ──────────────────────────────────
        self.db.commit()
        self.db.refresh(payment)
        return payment

    # ── Complete Payment ────────────────────────────────────

    def complete_payment(self, payment_id: uuid.UUID) -> Payment:
        """
        Marks payment as COMPLETED.
        Writes outbox event in the same transaction.
        """
        payment = self.db.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        ).scalar_one_or_none()

        if not payment:
            raise ValueError(f"Payment {payment_id} not found")

        if payment.status != PaymentStatus.PENDING:
            raise ValueError(f"Payment {payment_id} is {payment.status}, expected PENDING")

        payment.status = PaymentStatus.COMPLETED
        payment.updated_at = datetime.now(timezone.utc)

        outbox_event = OutboxEvent(
            aggregate_type="payment",
            aggregate_id=str(payment.id),
            event_type="payment.completed",
            payload={
                "payment_id": str(payment.id),
                "status": PaymentStatus.COMPLETED.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.db.add(outbox_event)
        self.db.commit()
        self.db.refresh(payment)
        return payment

    # ── Fail Payment ────────────────────────────────────────

    def fail_payment(self, payment_id: uuid.UUID, reason: str) -> Payment:
        """
        Marks payment as FAILED and refunds the sender.
        Locks accounts with SELECT FOR UPDATE for safe refund.
        """
        payment = self.db.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        ).scalar_one_or_none()

        if not payment:
            raise ValueError(f"Payment {payment_id} not found")

        if payment.status not in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
            raise ValueError(f"Cannot fail payment in status {payment.status}")

        # Lock accounts in sorted order for refund
        account_ids = sorted([payment.from_account_id, payment.to_account_id])
        accounts = (
            self.db.execute(
                select(Account)
                .where(Account.id.in_(account_ids))
                .with_for_update()
                .order_by(Account.id)
            )
            .scalars()
            .all()
        )
        account_map = {acc.id: acc for acc in accounts}

        sender = account_map[payment.from_account_id]
        receiver = account_map[payment.to_account_id]

        # Refund
        sender.balance += payment.amount
        receiver.balance -= payment.amount

        payment.status = PaymentStatus.FAILED
        payment.updated_at = datetime.now(timezone.utc)

        outbox_event = OutboxEvent(
            aggregate_type="payment",
            aggregate_id=str(payment.id),
            event_type="payment.failed",
            payload={
                "payment_id": str(payment.id),
                "status": PaymentStatus.FAILED.value,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.db.add(outbox_event)
        self.db.commit()
        self.db.refresh(payment)
        return payment

    # ── Read ────────────────────────────────────────────────

    def get_payment(self, payment_id: uuid.UUID) -> Payment | None:
        return self.db.execute(
            select(Payment).where(Payment.id == payment_id)
        ).scalar_one_or_none()
