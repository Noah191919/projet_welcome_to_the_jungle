from __future__ import annotations
import csv
import logging
from typing import Iterable
from sqlalchemy.orm import Session
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.repository import Repository
from app.outbox import DbOutbox
from app.schemas import CustomerSyncSchema
from app.config import settings

logger = logging.getLogger(__name__)

class CSVParser:
    def __init__(self, delimiter: str = ";"):
        self.delimiter = delimiter

    def read_rows(self, path: str) -> Iterable[dict]:
        with open(path, encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter=self.delimiter)
            for row in reader:
                yield row

class ExternalApiClient:
    def __init__(self, base_url: str = settings.MOCK_API_URL, timeout: float = settings.EXTERNAL_TIMEOUT_SECONDS):
        self.base_url = base_url
        self.timeout = timeout

    @retry(stop=stop_after_attempt(settings.EXTERNAL_MAX_RETRIES), wait=wait_exponential(multiplier=0.5))
    async def send(self, payload: list[dict]):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.base_url, json=payload)
            resp.raise_for_status()
            return resp.json()

class SyncService:
    def __init__(self, repo: Repository, external_client: ExternalApiClient, csv_parser: CSVParser):
        self.repo = repo
        self.external = external_client
        self.csv = csv_parser

    def _build_customer_payload(self, cust) -> dict:
        """
        Construire le payload customer attendu par l'API externe à partir d'une instance
        de Customer (avec relation purchases). Retourne un dict serializable
        (utilise CustomerSyncSchema pour validation/normalisation).
        """
        return CustomerSyncSchema.model_validate({
            "customer_id": cust.external_id,
            "title": cust.title,
            "firstname": cust.firstname,
            "lastname": cust.lastname,
            "postal_code": cust.postal_code,
            "city": cust.city,
            "email": cust.email,
            "purchases": [
                {
                    "purchase_identifier": p.purchase_identifier,
                    "product_id": int(p.product_id),
                    "quantity": int(p.quantity),
                    "price": float(p.price),
                    "currency": p.currency,
                    "date": p.date
                } for p in (cust.purchases or [])
            ]
        }).model_dump()

    def import_csv_to_db(self, db: Session, customers_path: str, purchases_path: str):
        customer_rows = list(self.csv.read_rows(customers_path))
        purchase_rows = list(self.csv.read_rows(purchases_path))
        try:
            self.repo.add_customers(db, customer_rows)
            self.repo.add_purchases(db, purchase_rows)
            db.commit()
        except Exception:
            logger.exception("import_csv_to_db failed, rolling back")
            db.rollback()
            raise

    async def send_unsynced(self, db: Session, chunk_size: int = 50):
        outbox = DbOutbox(db)
        unsynced = self.repo.get_unsynced_customers(db)
        if not unsynced:
            return {"status": "ok", "message": "nothing to sync"}
        payloads = []
        ids = []
        for cust in unsynced:
            payloads.append(CustomerSyncSchema.model_validate({
                "customer_id": cust.external_id,
                "title": cust.title,
                "firstname": cust.firstname,
                "lastname": cust.lastname,
                "postal_code": cust.postal_code,
                "city": cust.city,
                "email": cust.email,
                "purchases": [
                    {
                        "purchase_identifier": p.purchase_identifier,
                        "product_id": int(p.product_id),
                        "quantity": int(p.quantity),
                        "price": float(p.price),
                        "currency": p.currency,
                        "date": p.date
                    } for p in (cust.purchases or [])
                ]
            }).model_dump())
            ids.append(cust.external_id)
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i:i+chunk_size]
            chunk_ids = ids[i:i+chunk_size]
            try:
                await self.external.send(chunk)
                self.repo.mark_customers_synchronized(db, chunk_ids)
                db.commit()
            except Exception as exc:
                logger.exception("external send failed for chunk")
                outbox.persist(chunk, last_error=str(exc))
                self.repo.increment_attempts(db, chunk_ids)
                db.commit()
        return {"status": "ok"}


_default_repo = Repository()
_default_external = ExternalApiClient()
_default_csv = CSVParser()
_default_service = SyncService(_default_repo, _default_external, _default_csv)


