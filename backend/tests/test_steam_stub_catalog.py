"""Pruebas de scripts/import_steam_appindex.py: sólo la parte de inserción
en la base (import_stub_catalog). No toca la red: eso es responsabilidad
de steam_service.get_app_list, probado por separado.
"""

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models import Game
from scripts.import_steam_appindex import import_stub_catalog


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def test_crea_una_ficha_minima_por_appid(db: Session) -> None:
    apps = [
        {"appid": 620, "name": "Portal 2"},
        {"appid": 730, "name": "Counter-Strike: Global Offensive"},
    ]

    created, skipped = import_stub_catalog(db, apps)

    assert created == 2
    assert skipped == 0
    games = db.scalars(select(Game)).all()
    assert {g.steam_app_id for g in games} == {620, 730}
    assert all(g.is_enriched is False for g in games)
    assert all(g.description is None for g in games)


def test_no_duplica_un_appid_ya_existente(db: Session) -> None:
    db.add(Game(steam_app_id=620, slug="portal-2", name="Portal 2"))
    db.commit()

    created, skipped = import_stub_catalog(db, [{"appid": 620, "name": "Portal 2"}])

    assert created == 0
    assert skipped == 1
    assert db.scalar(select(func.count(Game.id))) == 1


def test_resuelve_colision_de_slug_con_otro_origen(db: Session) -> None:
    """El slug "portal-2" ya está tomado por el dataset curado (u otra
    fuente); el AppID nuevo entra igual, con el appid sumado al slug."""
    db.add(Game(slug="portal-2", name="Portal 2"))
    db.commit()

    created, _ = import_stub_catalog(db, [{"appid": 620, "name": "Portal 2"}])

    assert created == 1
    nuevo = db.scalar(select(Game).where(Game.steam_app_id == 620))
    assert nuevo.slug == "portal-2-620"


def test_saltea_entradas_sin_nombre_o_sin_appid(db: Session) -> None:
    apps = [
        {"appid": None, "name": "Sin AppID"},
        {"appid": 1, "name": ""},
        {"appid": 2, "name": "Válido"},
    ]

    created, skipped = import_stub_catalog(db, apps)

    assert created == 1
    assert skipped == 2


def test_respeta_el_limite_de_fichas_nuevas(db: Session) -> None:
    apps = [{"appid": i, "name": f"Juego {i}"} for i in range(1, 11)]

    created, _ = import_stub_catalog(db, apps, limit=3)

    assert created == 3
    assert db.scalar(select(func.count(Game.id))) == 3
