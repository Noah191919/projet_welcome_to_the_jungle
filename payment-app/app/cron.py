from __future__ import annotations
import asyncio
import logging
import threading
from typing import Any
from sqlalchemy.orm import Session
from app.services import SyncService

logger = logging.getLogger(__name__)

def _run_async_coroutine_in_thread(coroutine: Any) -> Any:
    result_holder: list[tuple[str, Any]] = []
    def _target() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coroutine)
            result_holder.append(("ok", result))
        except Exception as exc:
            result_holder.append(("err", exc))
        finally:
            loop.close()
    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if not result_holder:
        raise RuntimeError("Cron worker: coroutine did not return a result")
    status, value = result_holder[0]
    if status == "ok":
        return value
    raise value

class CronSyncWorker:
    def __init__(self, sync_service: SyncService | None = None, permanent_failure_threshold: int = 5):
        self.sync_service = sync_service
        self.permanent_failure_threshold = permanent_failure_threshold

    def run_once(self, db: Session, chunk_size: int = 50) -> None:
        repo = self.sync_service.repo
        external = self.sync_service.external
        unsynced = repo.get_unsynced_customers(db)
        if not unsynced:
            logger.info("CronSyncWorker: nothing to synchronize")
            return
        payloads = []
        ids = []
        for c in unsynced:
            payloads.append(self.sync_service._build_customer_payload(c))
            ids.append(c.external_id)
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i:i+chunk_size]
            chunk_ids = ids[i:i+chunk_size]
            try:
                _run_async_coroutine_in_thread(external.send(chunk))
                repo.mark_customers_synchronized(db, chunk_ids)
                logger.info("CronSyncWorker: synchronized %d customers", len(chunk_ids))
            except Exception:
                logger.exception("CronSyncWorker: chunk send failed")
                repo.increment_attempts(db, chunk_ids)
                # log permanent failures
                failed = db.query(self.sync_service.repo.Customer).filter(
                    self.sync_service.repo.Customer.external_id.in_(chunk_ids),
                    self.sync_service.repo.Customer.attempt_number >= self.permanent_failure_threshold
                ).all()
                for f in failed:
                    logger.error("Permanent failure for external_id=%s", f.external_id)

