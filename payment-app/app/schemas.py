from pydantic import BaseModel, EmailStr
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
    
    model_config = {"from_attributes": True}


class CustomerSyncSchema(BaseModel):
    customer_id: Optional[int] = None  
    title: Optional[int] = None   
    firstname: str
    lastname: str
    postal_code: Optional[str] = None
    city: Optional[str] = None
    email: EmailStr
    is_synchronized: bool = False
    attempt_number: int = 0
    purchases: list[PurchaseSchema]

    model_config = {"from_attributes": True}