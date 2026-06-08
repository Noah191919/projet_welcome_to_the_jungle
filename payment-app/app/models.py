from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    ForeignKey
)
from sqlalchemy.orm import relationship
from app.database import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, unique=True, index=True, nullable=True)
    title = Column(Integer, nullable=True)
    firstname = Column(String, nullable=False)
    lastname = Column(String, nullable=False)
    postal_code = Column(String, nullable=True)
    city = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    is_synchronized = Column(Boolean, nullable=False, default=False)
    attempt_number = Column(Integer, nullable=False, default=0)
    

    purchases = relationship("Purchase", back_populates="customer", cascade="all, delete-orphan")


class Purchase(Base):
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    purchase_identifier = Column(String, nullable=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    date = Column(String, nullable=False)
    is_synchronized = Column(Boolean, nullable=False, default=False)
    attempt_number = Column(Integer, nullable=False, default=0)

    customer = relationship("Customer", back_populates="purchases")


