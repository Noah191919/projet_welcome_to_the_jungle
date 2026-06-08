from __future__ import annotations
import asyncio
import logging
from sqlalchemy.orm import Session

from app.services import SyncService
from app.models import Customer

logger = logging.getLogger(__name__)


class CronSyncWorker:
    """
    Cron worker responsible for periodic synchronization attempts.
    Responsibilities:
      - select unsynchronized customers
      - send them in chunks to external API
      - on success mark customers synchronized
      - on failure increment attempt counter and log permanent failures after threshold(5 attemps)
    """

    def __init__(self, sync_service: SyncService | None = None, permanent_failure_threshold: int = 5):
        self.sync_service = sync_service or SyncService()
        self.permanent_failure_threshold = permanent_failure_threshold

    def run_once(self, db: Session, chunk_size: int = 50) -> None:
        repository = self.sync_service.repository
        external_client = self.sync_service.external_client

        unsynced_customers = repository.get_unsynced_customers(db)
        if not unsynced_customers:
            logger.info("CronSyncWorker: nothing to synchronize")
            return

        payloads: list[dict[str, any]] = []
        customer_ids: list[str] = []
        for customer in unsynced_customers:
            try:
                payload = self.sync_service._build_customer_payload(customer)
                payloads.append(payload)
                customer_ids.append(customer.customer_id)
            except Exception as exc:
                logger.exception(f"CronSyncWorker: failed to build payload for customer_id={customer.customer_id}: {exc}")
                if customer.customer_id:
                    repository.increment_attempts(db, [customer.customer_id])

        # send payloads in chunks
        for start in range(0, len(payloads), chunk_size):
            chunk_payloads = payloads[start:start + chunk_size]
            chunk_customer_ids = customer_ids[start:start + chunk_size]
            try:
                asyncio.run(external_client.send(chunk_payloads))
                repository.mark_customers_synchronized(db, chunk_customer_ids)
                logger.info(f"CronSyncWorker: synchronized {len(chunk_customer_ids)} customers")
            except Exception as exc:
                logger.exception(f"CronSyncWorker: failed to send chunk ids={chunk_customer_ids}: {exc}")
                repository.increment_attempts(db, chunk_customer_ids)

                # find permanent failures and log them
                failed_customers = db.query(Customer).filter(
                    Customer.customer_id.in_(chunk_customer_ids),
                    Customer.attempt_number >= self.permanent_failure_threshold
                ).all()
                for fc in failed_customers:
                    logger.error(f"CronSyncWorker: permanent failure for customer_id={fc.customer_id} after {fc.attempt_number} attempts")


def run_cron_once(db: Session, chunk_size: int = 50) -> None:
    CronSyncWorker().run_once(db, chunk_size)
