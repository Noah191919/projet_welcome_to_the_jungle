from __future__ import annotations
from typing import Iterable, List
from sqlalchemy.orm import Session
from app.models import Customer, Purchase
import logging

logger = logging.getLogger(__name__)

class Repository:
    """DB access layer. Methods DO NOT commit; caller manages transaction."""

    def __init__(self, customer_model=Customer, purchase_model=Purchase):
        self.Customer = customer_model
        self.Purchase = purchase_model

    def add_customers(self, db: Session, customer_dicts: Iterable[dict]) -> List[Customer]:
        created: List[Customer] = []
        for row in customer_dicts:
            email = row.get("email")
            if not email:
                continue
            try:
                ext_id = int(row.get("customer_id")) if row.get("customer_id") else None
            except Exception:
                ext_id = None
            cust = self.Customer(
                external_id=ext_id,
                title=(int(row["title"]) if row.get("title") else None),
                firstname=row.get("firstname") or "",
                lastname=row.get("lastname") or "",
                email=email,
                postal_code=row.get("postal_code"),
                city=row.get("city"),
            )
            db.add(cust)
            created.append(cust)
        db.flush()
        return created

    def add_purchases(self, db: Session, purchase_dicts: Iterable[dict]) -> List[Purchase]:
        created: List[Purchase] = []
        db.flush()
        for row in purchase_dicts:
            try:
                ext_cust = int(row["customer_id"])
            except Exception:
                continue
            cust = db.query(self.Customer).filter(self.Customer.external_id == ext_cust).one_or_none()
            if not cust:
                continue
            try:
                purchase = self.Purchase(
                    purchase_identifier=row.get("purchase_identifier"),
                    customer_id=cust.id,
                    external_customer_id=ext_cust,
                    product_id=int(row["product_id"]),
                    quantity=int(float(row["quantity"])),
                    price=float(row["price"]),
                    currency=(row.get("currency") or ""),
                    date=row.get("date"),
                )
            except Exception:
                continue
            db.add(purchase)
            created.append(purchase)
        return created

    def get_unsynced_customers(self, db: Session):
        return db.query(self.Customer).filter(self.Customer.is_synchronized.is_(False)).all()

    def mark_customers_synchronized(self, db: Session, customer_external_ids: List[int]):
        if not customer_external_ids:
            return
        db.query(self.Customer).filter(self.Customer.external_id.in_(customer_external_ids)).update(
            {"is_synchronized": True, "attempt_number": 0}, synchronize_session=False
        )

    def increment_attempts(self, db: Session, customer_external_ids: List[int]):
        if not customer_external_ids:
            return
        db.query(self.Customer).filter(self.Customer.external_id.in_(customer_external_ids)).update(
            {"attempt_number": self.Customer.attempt_number + 1}, synchronize_session=False
        )