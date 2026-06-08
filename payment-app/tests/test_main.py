import os
import sys
from pathlib import Path
import json

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root.resolve()))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.main import app
from app.outbox import Outbox
import app.services as services
from app.database import SessionLocal
from app.models import Customer, Purchase

client = TestClient(app)

@pytest.fixture
def tmp_outbox(tmp_path, monkeypatch):
    outdir = tmp_path / "outbox"
    outbox = Outbox(outdir)
    monkeypatch.setattr(services._default_client, "outbox", outbox, raising=False)
    monkeypatch.setattr(services._default_service.external_client, "outbox", outbox, raising=False)
    return outdir

@pytest.fixture
def write_csv(tmp_path):
    def _writer(customers_rows=None, purchases_rows=None):
        customers = tmp_path / "customers.csv"
        purchases = tmp_path / "purchases.csv"

        if customers_rows is None:
            customers_rows = [["1", "1", "Doe", "John", "10000", "Town", "john@example.com"]]
        if purchases_rows is None:
            purchases_rows = [["p1", "1", "10", "1", "9.99", "EUR", "2024-12-01"]]

        header_c = "customer_id;title;lastname;firstname;postal_code;city;email\n"
        customers.write_text(header_c + "\n".join(";".join(r) for r in customers_rows) + "\n", encoding="utf-8")

        header_p = "purchase_identifier;customer_id;product_id;quantity;price;currency;date\n"
        purchases.write_text(header_p + "\n".join(";".join(r) for r in purchases_rows) + "\n", encoding="utf-8")

        return str(customers), str(purchases)
    return _writer


@pytest.fixture(autouse=True)
def clear_db():
    # ensure DB tables are clean between tests
    db: Session = SessionLocal()
    try:
        db.query(Purchase).delete()
        db.query(Customer).delete()
        db.commit()
    finally:
        db.close()


def test_import_csv_success_populates_db(write_csv):
    customers_path, purchases_path = write_csv(
        customers_rows=[["10", "1", "Doe", "John", "10000", "Town", "john@example.com"]],
        purchases_rows=[["p10", "10", "10", "2", "19.99", "EUR", "2024-12-01"]],
    )

    resp = client.post("/api/import-csv", json={
        "customers_file_path": customers_path,
        "purchased_file_path": purchases_path
    })
    assert resp.status_code == 201
    assert resp.json().get("status") == "success"

    db = SessionLocal()
    try:
        c = db.query(Customer).filter(Customer.customer_id == 10).one_or_none()
        assert c is not None
        assert c.firstname == "John"
        assert c.lastname == "Doe"
        assert c.email == "john@example.com"

        ps = db.query(Purchase).filter(Purchase.customer_id == 10, Purchase.product_id == 10).all()
        assert len(ps) == 1
        assert abs(ps[0].price - 19.99) < 1e-6
        assert ps[0].quantity == 2
    finally:
        db.close()


def test_import_csv_invalid_customers_returns_400(write_csv):
    # missing required columns firstname/lastname/email -> import should raise ValueError -> 400
    customers_rows = [["999", "", "", "", "", "", ""]]
    customers_path, purchases_path = write_csv(customers_rows=customers_rows, purchases_rows=[])
    resp = client.post("/api/import-csv", json={
        "customers_file_path": customers_path,
        "purchased_file_path": purchases_path
    })
    assert resp.status_code == 400


def test_send_customers_no_data_is_noop():
    # empty DB -> manual trigger should return success message (cron strategy)
    resp = client.post("/api/send-customers")
    assert resp.status_code == 200
    assert "Cron sync exécuté" in resp.json().get("message", "")


def test_send_customers_success_marks_synchronized(write_csv, monkeypatch):
    customers_path, purchases_path = write_csv()
    # import data
    resp = client.post("/api/import-csv", json={
        "customers_file_path": customers_path,
        "purchased_file_path": purchases_path
    })
    assert resp.status_code == 201

    # patch the shared service external client to a fake that returns success
    called = {"ok": False}

    async def fake_send(payload):
        called["ok"] = True
        return {"ok": True}

    monkeypatch.setattr(services._default_service.external_client, "send", fake_send, raising=True)

    resp2 = client.post("/api/send-customers")
    assert resp2.status_code == 200
    assert resp2.json().get("status") == "success"
    assert called["ok"] is True

    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.customer_id == 1).one()
        assert cust.is_synchronized is True
    finally:
        db.close()


def test_send_customers_failure_persists_to_outbox(write_csv, monkeypatch, tmp_outbox):
    customers_path, purchases_path = write_csv()

    resp = client.post("/api/import-csv", json={
        "customers_file_path": customers_path,
        "purchased_file_path": purchases_path
    })
    assert resp.status_code == 201

    # make external send persist to outbox and then raise network error
    async def fake_send_and_persist_then_raise(payload):
        services._default_client.outbox.persist(payload)
        raise httpx.HTTPError("simulated network failure")

    monkeypatch.setattr(services._default_service.external_client, "send", fake_send_and_persist_then_raise, raising=True)

    resp2 = client.post("/api/send-customers")
    assert resp2.status_code == 200

    files = list(tmp_outbox.glob("failed_*.json"))
    assert len(files) >= 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert any(c.get("email") == "john@example.com" for c in payload)


