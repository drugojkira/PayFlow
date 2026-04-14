"""
Pydantic schemas for request validation and response serialization.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from src.models import PaymentStatus


# ── Account ─────────────────────────────────────────────────


class CreateAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    balance: Decimal = Field(ge=0, default=Decimal("0"))


class AccountResponse(BaseModel):
    id: UUID
    name: str
    balance: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Payment ─────────────────────────────────────────────────


class CreatePaymentRequest(BaseModel):
    from_account_id: UUID
    to_account_id: UUID
    amount: Decimal = Field(gt=0)
    idempotency_key: str | None = None


class PaymentResponse(BaseModel):
    id: UUID
    from_account_id: UUID
    to_account_id: UUID
    amount: Decimal
    status: PaymentStatus
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Pagination ──────────────────────────────────────────────


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""

    items: list
    total: int
    page: int
    size: int
    pages: int


class PaymentListResponse(BaseModel):
    """Paginated list of payments."""

    items: list[PaymentResponse]
    total: int
    page: int
    size: int
    pages: int
