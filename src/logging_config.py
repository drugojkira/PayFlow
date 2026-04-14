"""
Structured logging configuration with structlog.

★ Почему structlog, а не стандартный logging:
  - JSON-формат — легко парсить в ELK/Datadog/Grafana Loki
  - Контекстные переменные (correlation_id, payment_id) без передачи через аргументы
  - Человекочитаемый вывод в dev, JSON в production

Использование:
    import structlog
    logger = structlog.get_logger()
    logger.info("payment.created", payment_id="abc-123", amount=100.0)

Вывод (dev):
    2025-01-01 12:00:00 [info] payment.created  payment_id=abc-123 amount=100.0

Вывод (prod, JSON):
    {"event": "payment.created", "payment_id": "abc-123", "amount": 100.0, "timestamp": "..."}
"""

import logging
import sys

import structlog


def setup_logging(json_format: bool = False, log_level: str = "INFO"):
    """
    Configure structlog + stdlib logging.

    Args:
        json_format: True for production (JSON lines), False for dev (colored console)
        log_level: logging level string
    """
    # Shared processors for both structlog and stdlib
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,  # ← merge correlation_id etc.
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_format:
        # Production: JSON output
        renderer = structlog.processors.JSONRenderer()
    else:
        # Development: colored console output
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Quiet noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("kafka").setLevel(logging.WARNING)
