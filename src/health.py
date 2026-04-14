"""
Deep health check — verifies connectivity to all external dependencies.

Response example:
  {
    "status": "healthy",
    "checks": {
      "postgres": {"status": "up", "latency_ms": 2.1},
      "rabbitmq": {"status": "up", "latency_ms": 5.3},
      "kafka":    {"status": "up", "latency_ms": 12.0}
    }
  }

If any check fails:
  {
    "status": "unhealthy",
    "checks": {
      "postgres": {"status": "up", "latency_ms": 2.1},
      "rabbitmq": {"status": "down", "error": "Connection refused"},
      "kafka":    {"status": "up", "latency_ms": 12.0}
    }
  }
"""

import time
from typing import Any

from src.config import settings


def _check_postgres() -> dict[str, Any]:
    """Check PostgreSQL connectivity with a simple SELECT 1."""
    from sqlalchemy import text

    from src.database import engine

    start = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency = (time.perf_counter() - start) * 1000
        return {"status": "up", "latency_ms": round(latency, 1)}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def _check_rabbitmq() -> dict[str, Any]:
    """Check RabbitMQ connectivity via a socket connection to the AMQP port."""
    import socket
    from urllib.parse import urlparse

    start = time.perf_counter()
    try:
        parsed = urlparse(settings.CELERY_BROKER_URL)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5672
        sock = socket.create_connection((host, port), timeout=3)
        sock.close()
        latency = (time.perf_counter() - start) * 1000
        return {"status": "up", "latency_ms": round(latency, 1)}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def _check_kafka() -> dict[str, Any]:
    """Check Kafka connectivity by fetching cluster metadata."""
    start = time.perf_counter()
    try:
        from kafka import KafkaConsumer

        consumer = KafkaConsumer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            request_timeout_ms=3000,
        )
        topics = consumer.topics()  # forces metadata fetch
        consumer.close()
        latency = (time.perf_counter() - start) * 1000
        return {"status": "up", "latency_ms": round(latency, 1), "topics": len(topics)}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def check_all() -> dict[str, Any]:
    """Run all health checks and return aggregated result."""
    checks = {
        "postgres": _check_postgres(),
        "rabbitmq": _check_rabbitmq(),
        "kafka": _check_kafka(),
    }

    all_up = all(c["status"] == "up" for c in checks.values())

    return {
        "status": "healthy" if all_up else "unhealthy",
        "checks": checks,
    }
