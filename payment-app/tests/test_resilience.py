import os
import sys
from pathlib import Path
import json

import pytest
import httpx
from fastapi.testclient import TestClient

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root.resolve()))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.main import app
import app.services as services
from app.outbox import Outbox

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def fake_customer():
    class FakeCustomer:
        def __init__(self):
            # use numeric ids to match DB types in this workspace
            self.customer_id = 1
            self.title = 1
            self.firstname = "Lumi"
            self.lastname = "Gueye"
            self.postal_code = "75000"
            self.city = "Paris"
            self.email = "lumi@example.com"
            self.purchases = []
    return FakeCustomer()

@pytest.fixture
def tmp_outbox(tmp_path, monkeypatch):
    outdir = tmp_path / "outbox"
    outbox = Outbox(outdir)

    monkeypatch.setattr(services._default_client, "outbox", outbox, raising=False)
    monkeypatch.setattr(services._default_service.external_client, "outbox", outbox, raising=False)
    return outdir

def test_import_csv_endpoint_success_and_file_not_checked(client, monkeypatch):
    called = {"ok": False}
    def fake_import_csv_to_db(db, customers_path, purchases_path):
        called["ok"] = True
    monkeypatch.setattr(services, "import_csv_to_db", fake_import_csv_to_db)
    resp = client.post("/api/import-csv", json={
        "customers_file_path": "/does/not/matter.csv",
        "purchased_file_path": "/does/not/matter.csv"
    })
    assert resp.status_code == 201
    assert resp.json().get("status") == "success"
    assert called["ok"] is True

def test_import_csv_endpoint_propagates_file_not_found(client, monkeypatch):
    def fake_import_raise(db, customers_path, purchases_path):
        raise FileNotFoundError("File not found")
    monkeypatch.setattr(services, "import_csv_to_db", fake_import_raise)
    resp = client.post("/api/import-csv", json={
        "customers_file_path": "/non/existent/customers.csv",
        "purchased_file_path": "/non/existent/purchases.csv"
    })
    assert resp.status_code == 404

def test_send_customers_no_data_is_noop(client, monkeypatch):
    # repository returns no unsynced customers -> no-op path
    monkeypatch.setattr(
        services._default_service.repository,
        "get_unsynced_customers",
        lambda db: []
    )
    resp = client.post("/api/send-customers")
    assert resp.status_code == 200
    assert "Cron sync exécuté" in resp.json().get("message", "")

def test_send_customers_success_calls_external_and_returns_ok(client, monkeypatch, fake_customer):
    # repository returns one fake customer
    monkeypatch.setattr(
        services._default_service.repository,
        "get_unsynced_customers",
        lambda db: [fake_customer]
    )
    called = {"ok": False}
    async def fake_send(payload):
        called["ok"] = True
        return {"ok": True}
    monkeypatch.setattr(services._default_service.external_client, "send", fake_send, raising=True)
    resp = client.post("/api/send-customers")
    assert resp.status_code == 200
    assert resp.json().get("status") == "success"
    assert called["ok"] is True

def test_send_customers_failure_persists_to_outbox_and_returns_200_and_persists(client, monkeypatch, fake_customer, tmp_outbox):
    # repository returns one fake customer
    monkeypatch.setattr(
        services._default_service.repository,
        "get_unsynced_customers",
        lambda db: [fake_customer]
    )

    async def fake_send_and_persist_then_raise(payload):
        # simulate client persisting failed payload then raising network error
        services._default_client.outbox.persist(payload)
        raise httpx.HTTPError("simulated network failure")
    monkeypatch.setattr(services._default_service.external_client, "send", fake_send_and_persist_then_raise, raising=True)
    resp = client.post("/api/send-customers")
    assert resp.status_code == 200
    files = list(tmp_outbox.glob("failed_*.json"))
    assert len(files) >= 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert any(c.get("email") == "lumi@example.com" for c in data)
