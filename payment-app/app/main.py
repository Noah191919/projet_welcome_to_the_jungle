from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
import logging

from app.database import engine, Base, get_db
from app.schemas import ImportCSVRequest
from app.services import _default_service, CSVParser, ExternalApiClient, SyncService, _default_csv, _default_external
from app.repository import Repository
from app.cron import CronSyncWorker

logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

def get_sync_service():
    repo = Repository()
    client = ExternalApiClient()
    parser = CSVParser()
    return SyncService(repo, client, parser)

app = FastAPI(title="Flight Payment Sync Service")

@app.post("/api/import-csv", status_code=status.HTTP_201_CREATED)
def import_csv(payload: ImportCSVRequest, db: Session = Depends(get_db), sync: SyncService = Depends(get_sync_service)):
    try:
        sync.import_csv_to_db(db, payload.customers_file_path, payload.purchased_file_path)
        return {"status": "success", "message": "Fichiers CSV importés avec succès."}
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        logger.exception("import_csv failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erreur interne")

@app.post("/api/send-customers")
def trigger_cron(db: Session = Depends(get_db), sync: SyncService = Depends(get_sync_service)):
    try:
        worker = CronSyncWorker(sync)
        worker.run_once(db)
        return {"status": "success", "message": "Cron sync exécuté (trigger manuel)."}
    except Exception:
        logger.exception("trigger_cron failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Echec cron")