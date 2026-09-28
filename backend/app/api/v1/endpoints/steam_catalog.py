"""Estado público del catálogo, sin claves ni acciones de importación remota."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.steam_catalog_service import get_catalog_status
from app.services.steam_catalog_worker import worker_status

router = APIRouter(prefix="/steam/catalog", tags=["steam"])


@router.get("/status")
def catalog_status(db: Session = Depends(get_db)) -> dict:
    worker = worker_status(db)
    return {**get_catalog_status(db, key_configured=worker["key_configured"]), **worker}
