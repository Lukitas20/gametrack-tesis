"""Servidor local de carga, sólo salud y listado real, sin startup operativo.

GAMETRACK_TEST_SQLITE_SNAPSHOT debe señalar una copia local existente.
uvicorn scripts.test_readonly_server:app --host 127.0.0.1 --port 8008
No importa app.main ni inicia workers, migraciones o tareas de escritura.
"""
import os
from pathlib import Path
from fastapi import FastAPI
from app.api.v1.endpoints.games import router as games_router
from app.api.v1.endpoints.health import router as health_router
from app.db.database import get_db
from sqlalchemy.orm import Session
from scripts.copy_sqlite_to_postgres import readonly_sqlite_engine

engine=readonly_sqlite_engine(Path(os.environ["GAMETRACK_TEST_SQLITE_SNAPSHOT"]),check_same_thread=False)
app=FastAPI(title="GameTrack · prueba local de sólo lectura")


def readonly_session():
    with Session(engine,autoflush=False) as db:
        try: yield db
        finally: db.rollback()


app.dependency_overrides[get_db]=readonly_session
for route in [*games_router.routes,*health_router.routes]:
    if route.path in {"/games","/health"} and route.methods=={"GET"}:
        app.add_api_route("/api/v1"+route.path,route.endpoint,methods=["GET"],response_model=route.response_model)
