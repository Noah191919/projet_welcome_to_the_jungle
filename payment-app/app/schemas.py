from __future__ import annotations
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class ImportCSVRequest(BaseModel):
    customers_file_path: str
    purchased_file_path: str

class PurchaseSchema(BaseModel):
    purchase_identifier: Optional[str] = None
    product_id: int
    quantity: int
    price: float
    currency: str
    date: str
    is_synchronized: bool = False
    attempt_number: int = 0

class CustomerSyncSchema(BaseModel):
    external_id: Optional[int] = Field(None, alias="customer_id")
    title: Optional[int] = None
    firstname: str
    lastname: str
    postal_code: Optional[str] = None
    city: Optional[str] = None
    email: EmailStr
    is_synchronized: bool = False
    attempt_number: int = 0
    purchases: list[PurchaseSchema] = []