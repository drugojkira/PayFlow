"""
Global error handling and middleware for FastAPI.

★ Вместо ловли исключений в каждом route, регистрируем обработчики
  на уровне приложения. Любой route может бросить InsufficientFundsError,
  и оно автоматически вернёт 400 с правильным JSON-телом.
"""

import logging
import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.services.payment import (
    AccountNotFoundError,
    DuplicatePaymentError,
    InsufficientFundsError,
)

logger = logging.getLogger(__name__)


# ── Exception Handlers ──────────────────────────────────────


def register_exception_handlers(app: FastAPI):
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(InsufficientFundsError)
    async def insufficient_funds_handler(request: Request, exc: InsufficientFundsError):
        return JSONResponse(
            status_code=400, content={"error": "insufficient_funds", "detail": str(exc)}
        )

    @app.exception_handler(AccountNotFoundError)
    async def account_not_found_handler(request: Request, exc: AccountNotFoundError):
        return JSONResponse(
            status_code=404, content={"error": "account_not_found", "detail": str(exc)}
        )

    @app.exception_handler(DuplicatePaymentError)
    async def duplicate_payment_handler(request: Request, exc: DuplicatePaymentError):
        return JSONResponse(
            status_code=409, content={"error": "duplicate_payment", "detail": str(exc)}
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400, content={"error": "bad_request", "detail": str(exc)}
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception(f"Unhandled exception on {request.method} {request.url.path}")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": "Internal server error"},
        )


# ── Request Logging Middleware ──────────────────────────────


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs every request with:
      - correlation_id (X-Request-ID header or auto-generated UUID)
      - method, path, status_code, duration_ms
    """

    async def dispatch(self, request: Request, call_next):
        # Generate or extract correlation ID
        correlation_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id

        # Bind correlation_id to structlog context — all log entries
        # within this request will automatically include it
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        # Add correlation ID to response headers
        response.headers["X-Request-ID"] = correlation_id

        structlog.get_logger().info(
            "request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 1),
        )
        return response
