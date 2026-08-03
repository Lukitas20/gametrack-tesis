"""Punto de entrada de la API de GameTrack.

La misma aplicación sirve la API y el frontend, así que la demo se levanta con
un solo proceso: no hay servidor de desarrollo aparte ni configuración de CORS
que mantener sincronizada.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import FRONTEND_DIR, settings
from app.db.base import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # El prototipo crea el esquema al arrancar en lugar de usar migraciones.
    init_db()
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "Plataforma inteligente de recomendación y análisis de videojuegos "
        "basada en comportamiento de usuarios y reseñas."
    ),
    version=settings.VERSION,
    lifespan=lifespan,
    # La documentación se mueve para dejar la raíz al frontend.
    docs_url="/docs",
    redoc_url=None,
)

# Sólo hace falta para acceder a la API desde otro origen (por ejemplo un
# frontend servido aparte durante el desarrollo).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/api", tags=["meta"])
def api_root() -> dict[str, str]:
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "docs": "/docs",
    }


if FRONTEND_DIR.is_dir():
    # `html=True` hace que la raíz sirva index.html.
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:  # pragma: no cover - sólo si se borró la carpeta del frontend

    @app.get("/", tags=["meta"])
    def missing_frontend() -> dict[str, str]:
        return {
            "detail": f"No se encontró el frontend en {FRONTEND_DIR}",
            "api": "/api",
            "docs": "/docs",
        }
