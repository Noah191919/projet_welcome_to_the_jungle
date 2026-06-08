from __future__ import annotations
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class Outbox:
    """File-backed outbox to persist failed payloads for later cron reprocessing."""

    def __init__(self, path: Path | None = None):
        self.dir = Path(path or Path(__file__).resolve().parent.parent / "failed_sends")
        self.dir.mkdir(parents=True, exist_ok=True)

    def persist(self, payload: Any) -> Path:
        timestamp = int(time.time() * 1000)
        filename = self.dir / f"failed_{timestamp}_{random.randint(0, 9999)}.json"
        filename.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Outbox: persisted failed payload to {filename}")
        return filename

    def list_files(self) -> Iterable[Path]:
        return sorted(self.dir.glob("failed_*.json"))

    def read(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def remove(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
            logger.info(f"Outbox: removed {path}")
        except Exception:
            logger.exception(f"Outbox: failed to remove {path}")

