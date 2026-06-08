import os
import sys
from pathlib import Path
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root.resolve()))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.main import app, get_sync_service
from app.cron import CronSyncWorker
from app.models import Outbox, Customer
from app.database import SessionLocal


@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def fake_customer():
    class FakeCustomer:
        def __init__(self):
            self.customer_id = 1
            self.external_id = 1
            self.title = 1
            self.firstname = "Lumi"
            self.lastname = "Gueye"
            self.postal_code = "75000"
            self.city = "Paris"
            self.email = "lumi@example.com"
            self.purchases = []
    return FakeCustomer()

@pytest.fixture(autouse=True)
def clear_outbox_and_db():
    db: Session = SessionLocal()
    try:
        db.query(Outbox).delete()
        db.commit()
    finally:
        db.close()

def test_import_csv_endpoint_success_and_file_not_checked(client, monkeypatch):
    called = {"ok": False}
    class FakeService:
        def import_csv_to_db(self, db, customers_path, purchases_path):
            called["ok"] = True

    monkeypatch.setitem(app.dependency_overrides, get_sync_service, lambda: FakeService())
    try:
        resp = client.post("/api/import-csv", json={
            "customers_file_path": "/does/not/matter.csv",
            "purchased_file_path": "/does/not/matter.csv"
        })
        assert resp.status_code == 201
        assert resp.json().get("status") == "success"
        assert called["ok"] is True
    finally:
        app.dependency_overrides.pop(get_sync_service, None)

def test_import_csv_endpoint_propagates_file_not_found(client, monkeypatch):
    class FakeService:
        def import_csv_to_db(self, db, customers_path, purchases_path):
            raise FileNotFoundError("File not found")

    monkeypatch.setitem(app.dependency_overrides, get_sync_service, lambda: FakeService())
    try:
        resp = client.post("/api/import-csv", json={
            "customers_file_path": "/non/existent/customers.csv",
            "purchased_file_path": "/non/existent/purchases.csv"
        })
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_sync_service, None)

def test_send_customers_no_data_is_noop(client, monkeypatch):
    class FakeRepo:
        def get_unsynced_customers(self, db):
            return []
        def mark_customers_synchronized(self, db, ids):
            return
        def increment_attempts(self, db, ids):
            return
        Customer = Customer

    class FakeService:
        def __init__(self):
            self.repo = FakeRepo()
            async def dummy_send(payload):
                return {"ok": True}
            self.external = type("E", (), {"send": dummy_send})
            self._build_customer_payload = lambda c: {}

    monkeypatch.setitem(app.dependency_overrides, get_sync_service, lambda: FakeService())
    try:
        resp = client.post("/api/send-customers")
        assert resp.status_code == 200
        assert "Cron sync exécuté" in resp.json().get("message", "")
    finally:
        app.dependency_overrides.pop(get_sync_service, None)

def test_send_customers_success_calls_external_and_returns_ok(monkeypatch, fake_customer):
    called = {"ok": False}
    class FakeRepo:
        def __init__(self):
            self.synchronized_ids = []
        def get_unsynced_customers(self, db):
            return [fake_customer]
        def mark_customers_synchronized(self, db, ids):
            self.synchronized_ids.extend(ids or [])
            return
        def increment_attempts(self, db, ids):
            return
        Customer = Customer

    class FakeExternal:
        async def send(self, payload):
            called["ok"] = True
            return {"ok": True}

    class FakeService:
        def __init__(self, repo):
            self.repo = repo
            self.external = FakeExternal()
        def _build_customer_payload(self, c):
            if c.external_id is not None:
                cid = c.external_id
            else:
                cid = c.customer_id
            return {
                "customer_id": cid,
                "title": c.title,
                "firstname": c.firstname,
                "lastname": c.lastname,
                "postal_code": c.postal_code,
                "city": c.city,
                "email": c.email,
                "purchases": c.purchases or []
            }

    repo = FakeRepo()
    svc = FakeService(repo)
    db = SessionLocal()
    try:
        worker = CronSyncWorker(svc)
        worker.run_once(db)
    finally:
        db.close()

    assert called["ok"] is True

def test_send_customers_failure_increments_attempts_and_does_not_persist_outbox(monkeypatch, fake_customer):
    class FakeRepo:
        def __init__(self):
            self.attempted = []
        def get_unsynced_customers(self, db):
            return [fake_customer]
        def mark_customers_synchronized(self, db, ids):
            return
        def increment_attempts(self, db, ids):
            self.attempted.extend(ids or [])
            return
        Customer = Customer

    class FailingExternal:
        async def send(self, payload):
            raise httpx.HTTPError("simulated network failure")

    class FakeService:
        def __init__(self, repo):
            self.repo = repo
            self.external = FailingExternal()
        def _build_customer_payload(self, c):
            if c.external_id is not None:
                cid = c.external_id
            else:
                cid = c.customer_id
            return {
                "customer_id": cid,
                "title": c.title,
                "firstname": c.firstname,
                "lastname": c.lastname,
                "postal_code": c.postal_code,
                "city": c.city,
                "email": c.email,
                "purchases": c.purchases or []
            }

    repo = FakeRepo()
    svc = FakeService(repo)
    db = SessionLocal()
    try:
        worker = CronSyncWorker(svc)
        worker.run_once(db)
    finally:
        db.close()

    assert repo.attempted and repo.attempted[0] == 1

    db2 = SessionLocal()
    try:
        rows = db2.query(Outbox).all()
        assert len(rows) == 0
    finally:
        db2.close()


