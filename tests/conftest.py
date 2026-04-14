"""
Test fixtures using testcontainers.

testcontainers автоматически поднимает Docker-контейнеры для тестов:
  - PostgreSQL  — реальная база вместо SQLite/mock
  - RabbitMQ    — реальный брокер для Celery
  - Kafka       — реальный Kafka для outbox relay

Каждый тест получает чистую транзакцию, которая откатывается после теста.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.rabbitmq import RabbitMqContainer

from src.models import Base
from tests.factories import AccountFactory, OutboxEventFactory, PaymentFactory


# ── PostgreSQL ──────────────────────────────────────────────


@pytest.fixture(scope="session")
def postgres_container():
    """Starts a real PostgreSQL in Docker for the entire test session."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def engine(postgres_container):
    """Creates SQLAlchemy engine connected to test PostgreSQL."""
    url = postgres_container.get_connection_url()
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="session")
def session_factory(engine):
    """Session factory bound to the test engine."""
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(session_factory) -> Session:
    """
    Per-test DB session with automatic rollback.

    Каждый тест работает внутри транзакции. После теста транзакция
    откатывается — тесты не влияют друг на друга.
    """
    connection = session_factory().get_bind().connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    # Wire up Factory Boy to use this session
    AccountFactory._meta.sqlalchemy_session = session
    PaymentFactory._meta.sqlalchemy_session = session
    OutboxEventFactory._meta.sqlalchemy_session = session

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def committed_db(session_factory) -> Session:
    """
    Per-test DB session that actually commits.
    Used for concurrency tests where multiple threads need to see committed data.
    Cleans up by deleting created records.
    """
    session = session_factory()
    yield session
    # Cleanup: delete all test data
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()


# ── RabbitMQ ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def rabbitmq_container():
    """Starts a real RabbitMQ in Docker for the entire test session."""
    with RabbitMqContainer("rabbitmq:3-management-alpine") as rmq:
        yield rmq


@pytest.fixture(scope="session")
def rabbitmq_url(rabbitmq_container) -> str:
    host = rabbitmq_container.get_container_host_ip()
    port = rabbitmq_container.get_exposed_port(5672)
    return f"amqp://guest:guest@{host}:{port}//"


# ── Kafka ───────────────────────────────────────────────────


@pytest.fixture(scope="session")
def kafka_container():
    """Starts a real Kafka in Docker for the entire test session."""
    with KafkaContainer("confluentinc/cp-kafka:7.5.0") as kafka:
        yield kafka


@pytest.fixture(scope="session")
def kafka_bootstrap_servers(kafka_container) -> str:
    return kafka_container.get_bootstrap_server()
