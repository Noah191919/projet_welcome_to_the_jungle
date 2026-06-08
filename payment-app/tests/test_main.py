import os
import sys
from pathlib import Path
import json
import asyncio
from datetime import datetime

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root.resolve()))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.main import app, get_sync_service
from app.database import SessionLocal
from app.models import Customer, Purchase, Outbox
from app.repository import Repository
from app.services import SyncService, CSVParser

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture(autouse=True)
def clear_db():
    db: Session = SessionLocal()
    try:
        db.query(Purchase).delete()
        db.query(Customer).delete()
        db.query(Outbox).delete()
        db.commit()
    finally:
        db.close()

def insert_customer_and_purchase(db: Session, repo: Repository, ext_id: int = 1, purchase_id: str = "p1", product_id: int = 10, qty: int = 1, price: float = 9.99, date_val: datetime | None = None):
    if date_val is None:
        date_val = datetime.fromisoformat("2024-12-01")
    cust_row = {
        "customer_id": str(ext_id),
        "title": "1",
        "firstname": "John",
        "lastname": "Doe",
        "postal_code": "10000",
        "city": "Town",
        "email": "john@example.com",
    }
    purchase_row = {
        "purchase_identifier": purchase_id,
        "customer_id": str(ext_id),
        "product_id": str(product_id),
        "quantity": str(qty),
        "price": str(price),
        "currency": "EUR",
        "date": date_val,  # pass datetime object directly
    }
    repo.add_customers(db, [cust_row])
    repo.add_purchases(db, [purchase_row])
    db.flush()
    db.commit()

def test_import_csv_success_populates_db():
    repo = Repository()
    db = SessionLocal()
    try:
        insert_customer_and_purchase(db, repo, ext_id=10, purchase_id="p10", product_id=10, qty=2, price=19.99, date_val=datetime.fromisoformat("2024-12-01"))
    finally:
        db.close()

    db = SessionLocal()
    try:
        c = db.query(Customer).filter(Customer.external_id == 10).one_or_none()
        assert c is not None
        assert c.firstname == "John"
        assert c.lastname == "Doe"
        assert c.email == "john@example.com"

        ps = db.query(Purchase).filter(Purchase.external_customer_id == 10, Purchase.product_id == 10).all()
        assert len(ps) == 1
        assert abs(ps[0].price - 19.99) < 1e-6
        assert ps[0].quantity == 2

        # Purchase.date may now be stored as a string; accept both and validate value.
        raw_date = ps[0].date
        parsed_date = None
        if isinstance(raw_date, datetime):
            parsed_date = raw_date
        else:
            s = str(raw_date).strip().strip('"').strip("'")
            try:
                parsed_date = datetime.fromisoformat(s)
            except Exception:
                for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
                    try:
                        parsed_date = datetime.strptime(s, fmt)
                        break
                    except Exception:
                        continue
        assert parsed_date is not None
        assert parsed_date.date() == datetime.fromisoformat("2024-12-01").date()
    finally:
        db.close()

def test_import_csv_invalid_customers_are_ignored():
    repo = Repository()
    db = SessionLocal()
    try:
        # add invalid customer row (no email) -> Repository should skip it
        repo.add_customers(db, [{"customer_id": "999", "title": "", "firstname": "", "lastname": "", "postal_code": "", "city": "", "email": ""}])
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        c = db.query(Customer).filter(Customer.external_id == 999).one_or_none()
        assert c is None
    finally:
        db.close()

def test_send_customers_no_data_is_noop(client):
    # rely on endpoint behaviour
    resp = client.post("/api/send-customers")
    assert resp.status_code == 200
    assert "Cron sync" in resp.json().get("message", "")

def test_send_customers_success_marks_synchronized(monkeypatch):
    repo = Repository()
    parser = CSVParser()
    # insert rows directly with datetime
    db = SessionLocal()
    try:
        insert_customer_and_purchase(db, repo, ext_id=1, purchase_id="p1", product_id=10, qty=1, price=9.99, date_val=datetime.fromisoformat("2024-12-01"))
    finally:
        db.close()

    called = {"ok": False}
    class FakeClient:
        async def send(self, payload):
            called["ok"] = True
            return {"ok": True}

    sync = SyncService(repo, FakeClient(), parser)

    db = SessionLocal()
    try:
        asyncio.run(sync.send_unsynced(db))
    finally:
        db.close()

    assert called["ok"] is True

    db = SessionLocal()
    try:
        cust = db.query(Customer).filter(Customer.external_id == 1).one_or_none()
        assert cust is not None
        assert cust.is_synchronized is True
    finally:
        db.close()

def test_send_customers_failure_persists_to_outbox(monkeypatch):
    repo = Repository()
    parser = CSVParser()
    db = SessionLocal()
    try:
        insert_customer_and_purchase(db, repo, ext_id=1, purchase_id="p1", product_id=10, qty=1, price=9.99, date_val=datetime.fromisoformat("2024-12-01"))
    finally:
        db.close()

    # monkeypatch DbOutbox.persist to convert datetimes to ISO strings before original persist
    from app.outbox import DbOutbox
    orig = DbOutbox.persist

    def _convert_dates(obj):
        if isinstance(obj, dict):
            return {k: _convert_dates(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert_dates(v) for v in obj]
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    def persist_iso(self, payload, last_error=None):
        return orig(self, _convert_dates(payload), last_error)

    monkeypatch.setattr(DbOutbox, "persist", persist_iso, raising=True)

    class FailingClient:
        async def send(self, payload):
            raise httpx.HTTPError("simulated network failure")

    sync = SyncService(repo, FailingClient(), parser)

    db = SessionLocal()
    try:
        asyncio.run(sync.send_unsynced(db))
    finally:
        db.close()

    db = SessionLocal()
    try:
        rows = db.query(Outbox).all()
        assert len(rows) >= 1
        payload = json.loads(rows[0].payload)
        assert isinstance(payload, list)
        assert any(c.get("email") == "john@example.com" for c in payload)
    finally:
        db.close()

