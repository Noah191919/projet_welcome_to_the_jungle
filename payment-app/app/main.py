from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
import logging

from app.database import engine, Base, get_db
from app.schemas import ImportCSVRequest
import app.services as services
from app.cron import CronSyncWorker

logger = logging.getLogger(__name__)

# Initialisation de la base SQLite
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Flight Payment Sync Service")


@app.post("/api/import-csv", status_code=status.HTTP_201_CREATED)
def import_csv(payload: ImportCSVRequest, db: Session = Depends(get_db)):
    """
    Endpoint to import customers + purchases CSV files into the DB.
    Validation and persistence are delegated to the service.
    """
    try:
        services.import_csv_to_db(
            db,
            customers_path=payload.customers_file_path,
            purchases_path=payload.purchased_file_path,
        )
        return {"status": "success", "message": "Fichiers CSV importés avec succès."}
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Fichier introuvable : {str(e)}")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception("import_csv: unexpected error")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erreur interne : {str(e)}")


@app.post("/api/send-customers")
def trigger_cron_sync(db: Session = Depends(get_db)):
    """
    Manual trigger for the cron-based synchronization worker (for the tests).
    """
    try:
        worker = CronSyncWorker(sync_service=services._default_service)
        worker.run_once(db)
        return {"status": "success", "message": "Cron sync exécuté (trigger manuel)."}
    except Exception as e:
        logger.exception("trigger_cron_sync: cron worker failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Echec de la synchronisation cron : {str(e)}",
        )
