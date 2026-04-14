"""
Integration tests for FastAPI endpoints.

Uses TestClient + real PostgreSQL (via testcontainers).
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.app import app
from src.database import get_db


@pytest.fixture()
def client(db: Session):
    """FastAPI test client with DB session override."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        # May be 200 or 503 depending on whether infra is running
        data = resp.json()
        assert "status" in data
        assert "checks" in data


class TestAccountEndpoints:
    def test_create_account(self, client):
        resp = client.post("/accounts", json={"name": "Alice", "balance": "1000.00"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Alice"
        assert data["balance"] == "1000.00"
        assert "id" in data

    def test_get_account(self, client):
        # Create
        resp = client.post("/accounts", json={"name": "Bob", "balance": "500.00"})
        account_id = resp.json()["id"]

        # Get
        resp = client.get(f"/accounts/{account_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Bob"

    def test_get_account_not_found(self, client):
        import uuid

        resp = client.get(f"/accounts/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestPaymentEndpoints:
    @patch("src.routes.payments.process_payment.delay")
    def test_create_payment(self, mock_delay, client):
        """POST /payments → creates payment, dispatches Celery task."""
        # Create two accounts
        resp1 = client.post("/accounts", json={"name": "Sender", "balance": "1000.00"})
        resp2 = client.post("/accounts", json={"name": "Receiver", "balance": "0.00"})
        sender_id = resp1.json()["id"]
        receiver_id = resp2.json()["id"]

        # Create payment
        resp = client.post("/payments", json={
            "from_account_id": sender_id,
            "to_account_id": receiver_id,
            "amount": "250.00",
        })

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "PENDING"
        assert data["amount"] == "250.00"

        # Celery task was dispatched
        mock_delay.assert_called_once_with(data["id"])

    @patch("src.routes.payments.process_payment.delay")
    def test_create_payment_insufficient_funds(self, mock_delay, client):
        """POST /payments → 400 if insufficient funds."""
        resp1 = client.post("/accounts", json={"name": "Poor", "balance": "10.00"})
        resp2 = client.post("/accounts", json={"name": "Rich", "balance": "0.00"})

        resp = client.post("/payments", json={
            "from_account_id": resp1.json()["id"],
            "to_account_id": resp2.json()["id"],
            "amount": "100.00",
        })

        assert resp.status_code == 400
        data = resp.json()
        # Global exception handler returns {"error": ..., "detail": ...}
        detail = data.get("detail", "").lower()
        assert "balance" in detail or "insufficient" in detail
        mock_delay.assert_not_called()

    @patch("src.routes.payments.process_payment.delay")
    def test_get_payment(self, mock_delay, client):
        """GET /payments/{id} → returns payment details."""
        resp1 = client.post("/accounts", json={"name": "A", "balance": "1000.00"})
        resp2 = client.post("/accounts", json={"name": "B", "balance": "0.00"})

        resp = client.post("/payments", json={
            "from_account_id": resp1.json()["id"],
            "to_account_id": resp2.json()["id"],
            "amount": "50.00",
        })
        payment_id = resp.json()["id"]

        resp = client.get(f"/payments/{payment_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == payment_id

    def test_get_payment_not_found(self, client):
        import uuid

        resp = client.get(f"/payments/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestPaginationEndpoint:
    """Tests for GET /payments with pagination."""

    @patch("src.routes.payments.process_payment.delay")
    def test_list_payments_pagination(self, mock_delay, client):
        """Create several payments, verify paginated response."""
        resp1 = client.post("/accounts", json={"name": "S", "balance": "10000.00"})
        resp2 = client.post("/accounts", json={"name": "R", "balance": "0.00"})
        sid = resp1.json()["id"]
        rid = resp2.json()["id"]

        # Create 5 payments
        for i in range(5):
            client.post("/payments", json={
                "from_account_id": sid,
                "to_account_id": rid,
                "amount": "10.00",
            })

        # Page 1, size 2
        resp = client.get("/payments?page=1&size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["size"] == 2
        assert data["pages"] == 3

    @patch("src.routes.payments.process_payment.delay")
    def test_list_payments_filter_by_status(self, mock_delay, client):
        """Filter payments by status."""
        resp1 = client.post("/accounts", json={"name": "S", "balance": "10000.00"})
        resp2 = client.post("/accounts", json={"name": "R", "balance": "0.00"})
        sid = resp1.json()["id"]
        rid = resp2.json()["id"]

        client.post("/payments", json={
            "from_account_id": sid,
            "to_account_id": rid,
            "amount": "10.00",
        })

        # All created payments are PENDING
        resp = client.get("/payments?status=PENDING")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

        resp = client.get("/payments?status=COMPLETED")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_payments_empty(self, client):
        """Empty list returns valid paginated response."""
        resp = client.get("/payments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["pages"] == 0
