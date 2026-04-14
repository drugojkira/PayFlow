"""
FastAPI application entry point.

Usage:
  uvicorn src.app:app --reload
"""

import os

from fastapi import FastAPI

from src.logging_config import setup_logging
from src.middleware import RequestLoggingMiddleware, register_exception_handlers
from src.routes.payments import router

# Configure structured logging (JSON in production, colored in dev)
setup_logging(
    json_format=os.getenv("LOG_FORMAT", "console") == "json",
    log_level=os.getenv("LOG_LEVEL", "INFO"),
)

app = FastAPI(
    title="PayFlow",
    description="Reference architecture: Celery + RabbitMQ, Kafka Transactional Outbox, SELECT FOR UPDATE",
    version="1.0.0",
)

# ── Middleware & Error Handlers ─────────────────────────────
app.add_middleware(RequestLoggingMiddleware)
register_exception_handlers(app)

# ── Prometheus Metrics (/metrics endpoint) ──────────────────
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, include_in_schema=True)

app.include_router(router, tags=["payments"])


@app.get("/health")
def health():
    """
    Deep health check — verifies all dependencies are reachable.
    Returns 200 only if ALL checks pass, 503 otherwise.
    """
    from fastapi.responses import JSONResponse
    from src.health import check_all

    result = check_all()
    status_code = 200 if result["status"] == "healthy" else 503
    return JSONResponse(content=result, status_code=status_code)
