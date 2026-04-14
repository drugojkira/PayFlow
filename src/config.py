"""
Centralized configuration via pydantic-settings.
All values can be overridden through environment variables or .env file.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── PostgreSQL ──────────────────────────────────────────
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/payments"

    # ── RabbitMQ / Celery ───────────────────────────────────
    CELERY_BROKER_URL: str = "amqp://guest:guest@localhost:5672//"
    CELERY_RESULT_BACKEND: str = "rpc://"

    # ── Kafka ───────────────────────────────────────────────
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_PAYMENT_TOPIC: str = "payment-events"

    # ── Outbox Relay ────────────────────────────────────────
    OUTBOX_RELAY_INTERVAL_SECONDS: float = 5.0
    OUTBOX_RELAY_BATCH_SIZE: int = 100

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
