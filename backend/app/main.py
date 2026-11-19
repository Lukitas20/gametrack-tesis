"""Punto de entrada de la API de GameTrack.

La misma aplicación sirve la API y el frontend, así que la demo se levanta con
un solo proceso: no hay servidor de desarrollo aparte ni configuración de CORS
que mantener sincronizada.
"""

import truststore

truststore.inject_into_ssl()

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import FRONTEND_DIR, settings
from app.db.base import SessionLocal, init_db
from app.ml.recommender import get_engine
from app.services.steam_catalog_worker import start_worker, stop_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.DB_AUTO_CREATE:
        init_db()
    if "pytest" in sys.modules:
        yield
        return

    db = SessionLocal()
    try:
        from app.db.real_data import refresh_legacy_aggregates
        refresh_legacy_aggregates(db)
        get_engine(db)
    finally:
        db.close()
    start_worker()
    try:
        yield
    finally:
        stop_worker()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "Plataforma inteligente de recomendación y análisis de videojuegos "
        "basada en comportamiento de usuarios y reseñas."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/healthz", include_in_schema=False)
def healthcheck() -> dict[str, str]:
    """Disponibilidad del servicio y su conexión, sin revelar configuración."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Base de datos no disponible") from None
    return {"status": "ok"}


@app.get("/api", tags=["meta"])
def api_root() -> dict[str, str]:
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "docs": "/docs",
    }


class FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path in {".", "", "index.html"} or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        return response


if FRONTEND_DIR.is_dir():
    app.mount("/", FrontendFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:  # pragma: no cover - sólo si se borró la carpeta del frontend

    @app.get("/", tags=["meta"])
    def missing_frontend() -> dict[str, str]:
        return {
            "detail": f"No se encontró el frontend en {FRONTEND_DIR}",
            "api": "/api",
            "docs": "/docs",
        }
