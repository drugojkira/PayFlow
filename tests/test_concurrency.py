"""
Concurrency test — validates SELECT FOR UPDATE prevents double-spending.

★ Это ключевой тест, демонстрирующий зачем нужен SELECT FOR UPDATE.

Сценарий:
  - У аккаунта $150 на балансе
  - Два потока одновременно пытаются списать $100
  - Без SELECT FOR UPDATE оба увидят balance=150, оба спишут → balance = -50 (баг!)
  - С SELECT FOR UPDATE второй поток ждёт, пока первый завершит транзакцию,
    затем видит balance=50 и получает InsufficientFundsError

Результат: ровно 1 успешный платёж, 1 отказ.
"""

import threading
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import Account
from src.services.payment import InsufficientFundsError, PaymentService


@pytest.fixture()
def concurrent_accounts(committed_db: Session):
    """Creates sender with $150 and receiver with $0 (committed to DB)."""
    sender = Account(id=uuid4(), name="Concurrent-Sender", balance=Decimal("150.00"))
    receiver = Account(id=uuid4(), name="Concurrent-Receiver", balance=Decimal("0.00"))
    committed_db.add_all([sender, receiver])
    committed_db.commit()
    return sender.id, receiver.id


def test_select_for_update_prevents_double_spending(
    session_factory, concurrent_accounts
):
    """
    Two threads attempt to debit $100 from a $150 account simultaneously.
    SELECT FOR UPDATE ensures only one succeeds.
    """
    sender_id, receiver_id = concurrent_accounts

    results = {"success": 0, "insufficient": 0, "errors": []}
    barrier = threading.Barrier(2, timeout=10)

    def attempt_payment():
        db = session_factory()
        try:
            barrier.wait()  # synchronize: both threads start at the same moment
            service = PaymentService(db)
            service.create_payment(sender_id, receiver_id, Decimal("100.00"))
            results["success"] += 1
        except InsufficientFundsError:
            results["insufficient"] += 1
        except Exception as e:
            results["errors"].append(str(e))
        finally:
            db.close()

    t1 = threading.Thread(target=attempt_payment)
    t2 = threading.Thread(target=attempt_payment)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    # ── Assertions ──────────────────────────────────────────
    assert results["errors"] == [], f"Unexpected errors: {results['errors']}"
    assert results["success"] == 1, f"Expected exactly 1 success: {results}"
    assert results["insufficient"] == 1, f"Expected exactly 1 insufficient: {results}"

    # ── Verify final balance ────────────────────────────────
    db = session_factory()
    sender = db.execute(select(Account).where(Account.id == sender_id)).scalar_one()
    assert sender.balance == Decimal("50.00"), (
        f"Sender balance should be $50, got ${sender.balance}"
    )
    db.close()


def test_no_negative_balance_under_concurrency(session_factory, concurrent_accounts):
    """
    Stress test: 5 threads, each trying to debit $50 from a $150 account.
    At most 3 should succeed (3 × $50 = $150).
    """
    sender_id, receiver_id = concurrent_accounts

    # Reset balance to 150 for this test
    db = session_factory()
    sender = db.execute(
        select(Account).where(Account.id == sender_id).with_for_update()
    ).scalar_one()
    sender.balance = Decimal("150.00")
    db.commit()
    db.close()

    results = {"success": 0, "insufficient": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(5, timeout=10)

    def attempt():
        db = session_factory()
        try:
            barrier.wait()
            service = PaymentService(db)
            service.create_payment(sender_id, receiver_id, Decimal("50.00"))
            with lock:
                results["success"] += 1
        except InsufficientFundsError:
            with lock:
                results["insufficient"] += 1
        except Exception:
            pass
        finally:
            db.close()

    threads = [threading.Thread(target=attempt) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert results["success"] == 3, f"Expected 3 successes: {results}"
    assert results["insufficient"] == 2, f"Expected 2 insufficient: {results}"

    # Final balance should be exactly $0
    db = session_factory()
    sender = db.execute(select(Account).where(Account.id == sender_id)).scalar_one()
    assert sender.balance == Decimal("0.00"), (
        f"Balance should be $0, got ${sender.balance}"
    )
    db.close()
