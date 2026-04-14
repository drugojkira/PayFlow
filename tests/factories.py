"""
Factory Boy factories for test data generation.

Factory Boy позволяет декларативно описать, как создавать тестовые объекты.
Вместо ручного конструирования Account(...) в каждом тесте, достаточно:

    account = AccountFactory(balance=Decimal("500.00"))

Фабрика автоматически заполнит остальные поля (id, name, timestamps).
"""

import uuid
from decimal import Decimal

import factory

from src.models import Account, OutboxEvent, Payment, PaymentStatus


class AccountFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Factory for Account model."""

    class Meta:
        model = Account
        sqlalchemy_session = None  # set in conftest.py per-test
        sqlalchemy_session_persistence = "flush"

    id = factory.LazyFunction(uuid.uuid4)
    name = factory.Sequence(lambda n: f"Account-{n}")
    balance = Decimal("1000.00")


class PaymentFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Factory for Payment model."""

    class Meta:
        model = Payment
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    id = factory.LazyFunction(uuid.uuid4)
    from_account_id = factory.LazyFunction(uuid.uuid4)
    to_account_id = factory.LazyFunction(uuid.uuid4)
    amount = Decimal("100.00")
    status = PaymentStatus.PENDING


class OutboxEventFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Factory for OutboxEvent model."""

    class Meta:
        model = OutboxEvent
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "flush"

    id = factory.LazyFunction(uuid.uuid4)
    aggregate_type = "payment"
    aggregate_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    event_type = "payment.created"
    payload = factory.LazyFunction(lambda: {"test": True})
    published = False
