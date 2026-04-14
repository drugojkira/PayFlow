"""
FastAPI routes for payments and accounts.

Exceptions (InsufficientFundsError, AccountNotFoundError, etc.) are handled
globally by exception handlers registered in src.middleware — no need for
try/except in every route.
"""

from uuid import UUID

import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import Account, Payment, PaymentStatus
from src.schemas import (
    AccountResponse,
    CreateAccountRequest,
    CreatePaymentRequest,
    PaymentListResponse,
    PaymentResponse,
)
from src.services.payment import PaymentService
from src.worker.tasks import process_payment

router = APIRouter()


# ── Accounts ────────────────────────────────────────────────


@router.post("/accounts", response_model=AccountResponse, status_code=201)
def create_account(body: CreateAccountRequest, db: Session = Depends(get_db)):
    account = Account(name=body.name, balance=body.balance)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(account_id: UUID, db: Session = Depends(get_db)):
    account = db.execute(
        select(Account).where(Account.id == account_id)
    ).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


# ── Payments ────────────────────────────────────────────────


@router.post("/payments", response_model=PaymentResponse, status_code=201)
def create_payment(body: CreatePaymentRequest, db: Session = Depends(get_db)):
    """
    Create a payment:
      1. Validates and reserves funds (SELECT FOR UPDATE)
      2. Writes outbox event (Transactional Outbox)
      3. Dispatches Celery task for async processing

    Errors (insufficient funds, account not found) are caught by
    global exception handlers → automatic 400/404 responses.
    """
    service = PaymentService(db)
    payment = service.create_payment(
        from_account_id=body.from_account_id,
        to_account_id=body.to_account_id,
        amount=body.amount,
        idempotency_key=body.idempotency_key,
    )

    # Dispatch async processing via Celery + RabbitMQ
    process_payment.delay(str(payment.id))

    return payment


@router.get("/payments", response_model=PaymentListResponse)
def list_payments(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: PaymentStatus | None = Query(None, description="Filter by status"),
    account_id: UUID | None = Query(None, description="Filter by sender or receiver"),
    db: Session = Depends(get_db),
):
    """
    List payments with pagination and optional filters.

    Query params:
      - page: page number (default 1)
      - size: items per page (default 20, max 100)
      - status: filter by PaymentStatus
      - account_id: filter payments where account is sender OR receiver
    """
    query = select(Payment)
    count_query = select(func.count(Payment.id))

    if status:
        query = query.where(Payment.status == status)
        count_query = count_query.where(Payment.status == status)
    if account_id:
        condition = (Payment.from_account_id == account_id) | (
            Payment.to_account_id == account_id
        )
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = db.execute(count_query).scalar()
    items = (
        db.execute(
            query.order_by(Payment.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .scalars()
        .all()
    )

    return PaymentListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total > 0 else 0,
    )


@router.get("/payments/{payment_id}", response_model=PaymentResponse)
def get_payment(payment_id: UUID, db: Session = Depends(get_db)):
    service = PaymentService(db)
    payment = service.get_payment(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment
