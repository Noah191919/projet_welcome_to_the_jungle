from __future__ import annotations
import json
import logging
from sqlalchemy.orm import Session
from app.models import Outbox as OutboxModel

logger = logging.getLogger(__name__)

class DbOutbox:
    """DB-backed outbox. Caller manages commit."""

    def __init__(self, db: Session):
        self.db = db

    def persist(self, payload: object, last_error: str | None = None):
        row = OutboxModel(payload=json.dumps(payload, ensure_ascii=False), last_error=last_error)
        self.db.add(row)
        self.db.flush()
        logger.info(f"Outbox persisted payload id={row.id}")
        return row.id

    def list(self):
        return self.db.query(OutboxModel).order_by(OutboxModel.created_at.asc()).all()

    def remove(self, row_id: int):
        row = self.db.get(OutboxModel, row_id)
        if row:
            self.db.delete(row)
            self.db.flush()
            logger.info(f"Outbox removed id={row_id}")