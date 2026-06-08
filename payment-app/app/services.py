from __future__ import annotations
import csv
import logging
from typing import Iterable, Any, Optional
from sqlalchemy.orm import Session
import httpx

from app.models import Customer, Purchase
from app.schemas import CustomerSyncSchema
from app.config import settings
from app.outbox import Outbox

logger = logging.getLogger(__name__)


class CSVParser:
    """Simple CSV reader that yields raw rows."""
    def __init__(self, delimiter: str = ";"):
        self.delimiter = delimiter

    def read_rows(self, path: str) -> Iterable[dict[str, str]]:
        with open(path, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file, delimiter=self.delimiter)
            for row in reader:
                yield row


class Repository:
    """
    DB access layer:
      - add_customers: persist customers from CSV
      - add_purchases: persist purchases from CSV
      - get_all_customers 
    """

    def __init__(self, model_customer=Customer, model_purchase=Purchase):
        self.Customer = model_customer
        self.Purchase = model_purchase

    def add_customers(self, db: Session, rows: Iterable[dict[str, str]]) -> None:
        rows_list = list(rows)
        fieldnames = set(rows_list[0].keys()) if rows_list else set()
        required_fields = {"firstname", "lastname", "email"}
        if not required_fields.issubset(fieldnames):
            raise ValueError("Le fichier customers.csv ne contient pas les colonnes obligatoires.")

        valid_rows = [row for row in rows_list if row.get("email") and row.get("firstname") and row.get("lastname")]
        if not valid_rows:
            return

        emails = [row["email"] for row in valid_rows]
        existing_customers = db.query(self.Customer).filter(self.Customer.email.in_(emails)).all() if emails else []
        existing_emails = {customer.email for customer in existing_customers}

        to_create = []
        for row in valid_rows:
            email = row["email"]
            if email in existing_emails:
                continue
            to_create.append(self.Customer(
                customer_id=row.get("customer_id"),
                title=int(row["title"]) if row.get("title") else None,
                firstname=row["firstname"],
                lastname=row["lastname"],
                email=email,
                postal_code=row.get("postal_code"),
                city=row.get("city"),
            ))
        if to_create:
            db.bulk_save_objects(to_create)
            db.commit()

    def add_purchases(self, db: Session, rows: Iterable[dict[str, str]]) -> None:
        rows_list = list(rows)
        fieldnames = set(rows_list[0].keys()) if rows_list else set()
        required_fields = {
            "purchase_identifier", "customer_id", "product_id", "quantity", "price", "currency", "date"
        }
        if not required_fields.issubset(fieldnames):
            raise ValueError("Le fichier purchases.csv ne contient pas les colonnes obligatoires.")

        to_create = []
        for row in rows_list:
            if not (row.get("customer_id") and row.get("product_id") and row.get("quantity") and row.get("price") and row.get("date")):
                continue
            try:
                qty = int(float(row["quantity"]))
                price = float(row["price"])
            except Exception:
                continue
            to_create.append(self.Purchase(
                purchase_identifier=row.get("purchase_identifier"),
                customer_id= row.get("customer_id"),
                product_id=row.get("product_id"),
                quantity=qty,
                price=price,
                currency=row.get("currency"),
                date=row.get("date"),
            ))
        if to_create:
            db.bulk_save_objects(to_create)
            db.commit()

    def get_all_customers(self, db: Session) -> list[Customer]:
        return db.query(self.Customer).all()

    def get_unsynced_customers(self, db: Session) -> list[Customer]:
        return db.query(self.Customer).filter(self.Customer.is_synchronized.is_(False)).all()

    def mark_customers_synchronized(self, db: Session, customer_ids: list[str]) -> None:
        if not customer_ids:
            return
        db.query(self.Customer).filter(self.Customer.customer_id.in_(customer_ids)).update(
            {"is_synchronized": True, "attempt_number": 0},
            synchronize_session=False
        )
        db.commit()

    def increment_attempts(self, db: Session, customer_ids: list[str]) -> None:
        if not customer_ids:
            return
        db.query(self.Customer).filter(self.Customer.customer_id.in_(customer_ids)).update(
            {"attempt_number": self.Customer.attempt_number + 1},
            synchronize_session=False
        )
        db.commit()


class ExternalApiClient:
    """
    HTTP client.
    On failure payload is persisted to Outbox and exception is raised.
    Retries are handled by an external cron worker.
    """

    def __init__(
            self, 
            base_url: str = settings.MOCK_API_URL, 
            timeout: float = 5.0, 
            outbox: Optional[Outbox] = None
        ):
        self.base_url = base_url
        self.timeout = timeout
        self.outbox = outbox or Outbox()

    async def _post(self, payload: list[dict[str, Any]]) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.base_url, 
                json=payload, 
                timeout=self.timeout
            )
            return response

    async def send(self, payload: list[dict[str, Any]]) -> Any:
        try:
            response = await self._post(payload)
            response.raise_for_status()
            return response.json()
        except Exception:
            try:
                self.outbox.persist(payload)
            except Exception:
                logger.exception("ExternalApiClient: failed to persist failed payload to outbox")
            raise


class SyncService:
    """
    Service coordinating CSV parsing, DB persistence and payload building.
    """
    def __init__(
            self, 
            csv_parser: Optional[CSVParser] = None, 
            repository: Optional[Repository] = None, 
            external_client: Optional[ExternalApiClient] = None
        ):
        self.csv_parser = csv_parser or CSVParser()
        self.repository = repository or Repository()
        self.external_client = external_client or ExternalApiClient()

    def import_csv_to_db(
            self, 
            db: Session, 
            customers_path: str, 
            purchases_path: str
        ) -> None:
        customer_rows = self.csv_parser.read_rows(customers_path)
        self.repository.add_customers(db, customer_rows)

        purchase_rows = self.csv_parser.read_rows(purchases_path)
        self.repository.add_purchases(db, purchase_rows)

    def _build_customer_payload(self, customer: Customer) -> dict[str, Any]:
        purchases_payload: list[dict[str, Any]] = []
        for purchase in customer.purchases or []:
            purchases_payload.append({
                "purchase_identifier": purchase.purchase_identifier,
                "product_id": purchase.product_id,
                "quantity": int(purchase.quantity),
                "price": float(purchase.price),
                "currency": purchase.currency,
                "date": purchase.date,
            })
        payload = {
            "customer_id": customer.customer_id,
            "title": customer.title,
            "firstname": customer.firstname,
            "lastname": customer.lastname,
            "postal_code": customer.postal_code,
            "city": customer.city,
            "email": customer.email,
            "purchases": purchases_payload,
        }
        # validate shape with pydantic schema
        validated = CustomerSyncSchema.model_validate(payload).model_dump()
        return validated

    def get_all_sync_data(self, db: Session) -> list[dict[str, Any]]:
        customers = self.repository.get_all_customers(db)
        return [self._build_customer_payload(customer) for customer in customers]



_default_csv_parser = CSVParser()
_default_repo = Repository()
_default_client = ExternalApiClient()
_default_service = SyncService(
    csv_parser=_default_csv_parser, 
    repository=_default_repo, 
    external_client=_default_client
    )


def import_csv_to_db(db: Session, customers_path: str, purchases_path: str) -> None:
    return _default_service.import_csv_to_db(db, customers_path, purchases_path)


async def send_data_to_external_api_async(payload: list[dict[str, Any]]) -> Any:
    return await _default_client.send(payload)


def get_all_sync_data(db: Session) -> list[dict[str, Any]]:
    return _default_service.get_all_sync_data(db)


