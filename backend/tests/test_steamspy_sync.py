"""Pruebas de la ingesta de SteamSpy (nivel 0: índice, nivel 1: rasgos).

No se toca la red: ``fetch`` y ``sleeper`` son inyectables, así que los
tests describen escenarios (respuestas fijas, caídas, recuperaciones) con la
forma real de la API de SteamSpy.

Lo más importante que se prueba acá es el invariante heredado del incidente
de los 5 juegos borrados: un fallo de transporte JAMÁS puede interpretarse
como "este AppID no existe", ni a mano ni —peor— desatendido a escala de una
noche entera de worker.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import Game, Genre, Tag, game_tags
from app.models.steamspy import SYNC_DONE, SYNC_PENDING, SYNC_SKIPPED, SteamSpySync
from app.services import steamspy_service
from app.services.steamspy_service import (
    SteamSpyUnavailable,
    enrich_next_batch,
    ingest_index_entries,
    is_empty_entry,
    parse_owners,
)

# Forma real de una entrada de ``request=all`` (endpoint masivo).
INDEX_CS2 = {
    "appid": 730,
    "name": "Counter-Strike 2",
    "developer": "Valve",
    "publisher": "Valve",
    "positive": 7_642_084,
    "negative": 1_173_003,
    "owners": "100,000,000 .. 200,000,000",
}
INDEX_STARDEW = {
    "appid": 413150,
    "name": "Stardew Valley",
    "developer": "ConcernedApe",
    "publisher": "ConcernedApe",
    "positive": 600_000,
    "negative": 8_000,
    "owners": "20,000,000 .. 50,000,000",
}

# Forma real de ``request=appdetails`` para un juego con datos.
DETAILS_CS2 = {
    "appid": 730,
    "name": "Counter-Strike 2",
    "developer": "Valve",
    "publisher": "Valve",
    "positive": 7_642_084,
    "negative": 1_173_003,
    "owners": "100,000,000 .. 200,000,000",
    "genre": "Action, Free To Play",
    "languages": "English, Spanish - Spain",
    "tags": {
        "FPS": 91172,
        "Shooter": 65634,
        "Multiplayer": 62536,
        "Competitive": 53536,
    },
}

# Forma real para un AppID sin ficha (DLC, banda sonora, appid inválido).
DETAILS_EMPTY = {
    "appid": 999999,
    "name": None,
    "developer": "",
    "publisher": "",
    "positive": 0,
    "negative": 0,
    "owners": "0 .. 0",
    "tags": [],
}

# SteamSpy devuelve ``"tags": []`` (lista, no dict) cuando no hay votos.
DETAILS_SIN_TAGS = {
    "appid": 111,
    "name": "Juego Sin Votos",
    "developer": "Estudio",
    "publisher": "Estudio",
    "positive": 12,
    "negative": 3,
    "owners": "0 .. 20,000",
    "genre": "Indie",
    "tags": [],
}


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def no_sleep(_seconds: float) -> None:
    """Los tests no esperan la cadencia real de 1 pedido/segundo."""


def make_fetch(responses: dict[int, object]):
    """``fetch`` falso: dict AppID -> payload, excepción, o lista de ambos.

    Una lista modela una secuencia por AppID (primero falla, después
    responde), que es como se ve una caída transitoria de SteamSpy.
    """
    def fetch(appid: int) -> dict:
        outcome = responses[appid]
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return fetch


def queue_row(db: Session, appid: int) -> SteamSpySync:
    return db.get(SteamSpySync, appid)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100,000,000 .. 200,000,000", 150_000_000),
        ("0 .. 20,000", 10_000),
        (50_000, 50_000),
        ("", 0),
        (None, 0),
        ("no-numerico", 0),
    ],
)
def test_parse_owners(raw, expected) -> None:
    assert parse_owners(raw) == expected


def test_entrada_vacia_exige_todo_vacio() -> None:
    assert is_empty_entry(DETAILS_EMPTY)
    # Con nombre, con developer o con reseñas ya no es "sin datos": ante una
    # respuesta parcial se prefiere reintentar antes que descartar.
    assert not is_empty_entry({**DETAILS_EMPTY, "name": "Algo"})
    assert not is_empty_entry({**DETAILS_EMPTY, "developer": "Alguien"})
    assert not is_empty_entry({**DETAILS_EMPTY, "positive": 1})


# ---------------------------------------------------------------------------
# Nivel 0: índice
# ---------------------------------------------------------------------------


def test_indice_crea_stubs_con_senal_y_cola_priorizada(db: Session) -> None:
    report = ingest_index_entries(db, [INDEX_CS2, INDEX_STARDEW])

    assert report.created == 2 and report.queued == 2 and report.invalid == 0

    cs2 = db.scalar(select(Game).where(Game.steam_app_id == 730))
    assert cs2.name == "Counter-Strike 2"
    assert cs2.steamspy_positive == 7_642_084
    assert cs2.steamspy_negative == 1_173_003
    assert cs2.steamspy_owners == 150_000_000
    assert cs2.developer == "Valve"

    fila = queue_row(db, 730)
    assert fila.status == SYNC_PENDING
    assert fila.priority == 150_000_000
    assert queue_row(db, 413150).priority == 35_000_000


def test_indice_es_idempotente_y_no_resetea_la_cola(db: Session) -> None:
    ingest_index_entries(db, [INDEX_CS2])
    fila = queue_row(db, 730)
    fila.status = SYNC_DONE
    fila.attempts = 3
    db.commit()

    report = ingest_index_entries(db, [{**INDEX_CS2, "owners": "150,000,000 .. 150,000,000"}])

    assert report.created == 0 and report.updated == 1 and report.queued == 0
    assert report.reprioritized == 1
    assert db.scalar(select(select(Game.id).where(Game.steam_app_id == 730).exists()))
    fila = queue_row(db, 730)
    # La prioridad se refresca; el estado y los intentos no se pierden.
    assert fila.priority == 150_000_000
    assert fila.status == SYNC_DONE
    assert fila.attempts == 3


def test_indice_actualiza_senal_de_juegos_ya_enriquecidos(db: Session) -> None:
    existente = Game(steam_app_id=730, slug="counter-strike-2", name="Counter-Strike 2")
    db.add(existente)
    db.commit()

    ingest_index_entries(db, [INDEX_CS2])

    db.refresh(existente)
    assert existente.steamspy_owners == 150_000_000
    # No se creó un duplicado.
    assert db.scalar(select(Game).where(Game.steam_app_id == 730)).id == existente.id


def test_indice_salta_entradas_invalidas_y_desambigua_slugs(db: Session) -> None:
    entries = [
        {"appid": None, "name": "Sin AppID"},
        {"appid": 1, "name": "   "},
        {"appid": 2, "name": "Mismo Nombre", "owners": "0 .. 0"},
        {"appid": 3, "name": "Mismo Nombre", "owners": "0 .. 0"},
    ]
    report = ingest_index_entries(db, entries)

    assert report.invalid == 2 and report.created == 2
    slugs = {row[0] for row in db.execute(select(Game.slug))}
    assert slugs == {"mismo-nombre", "mismo-nombre-3"}


# ---------------------------------------------------------------------------
# Nivel 1: enriquecimiento
# ---------------------------------------------------------------------------


def test_enriquecer_aplica_etiquetas_votos_genero_y_crudo(db: Session) -> None:
    ingest_index_entries(db, [INDEX_CS2])
    report = enrich_next_batch(
        db, limit=10, sleeper=no_sleep, fetch=make_fetch({730: DETAILS_CS2})
    )

    assert report.enriched == 1 and report.processed == 1 and not report.aborted

    cs2 = db.scalar(select(Game).where(Game.steam_app_id == 730))
    slugs = {tag.slug: tag for tag in cs2.tags}
    assert set(slugs) == {"fps", "shooter", "multiplayer", "competitive"}
    assert all(tag.kind == "community" for tag in cs2.tags)
    assert [genre.name for genre in cs2.genres] == ["Acción", "Free to play"]

    votos = {
        (db.get(Tag, tag_id).slug): votes
        for tag_id, votes in db.execute(
            select(game_tags.c.tag_id, game_tags.c.votes).where(
                game_tags.c.game_id == cs2.id
            )
        )
    }
    assert votos["fps"] == 91172 and votos["competitive"] == 53536

    fila = queue_row(db, 730)
    assert fila.status == SYNC_DONE
    assert fila.raw == DETAILS_CS2  # el crudo queda para reprocesar sin red
    assert fila.synced_at is not None


def test_enriquecer_suma_sin_pisar_plataforma_ni_generos(db: Session) -> None:
    """Las categorías de la tienda y los géneros ricos ya cargados quedan."""
    plataforma = Tag(slug="un-jugador", name="Un jugador", kind="platform")
    genero = Genre(slug="accion", name="Acción")
    juego = Game(steam_app_id=730, slug="counter-strike-2", name="Counter-Strike 2")
    juego.tags = [plataforma]
    juego.genres = [genero]
    db.add_all([plataforma, genero, juego])
    db.commit()

    ingest_index_entries(db, [INDEX_CS2])
    enrich_next_batch(db, limit=10, sleeper=no_sleep, fetch=make_fetch({730: DETAILS_CS2}))

    db.refresh(juego)
    slugs = {tag.slug for tag in juego.tags}
    assert "un-jugador" in slugs and "fps" in slugs
    assert db.scalar(select(Tag).where(Tag.slug == "un-jugador")).kind == "platform"
    # Género que vino de la ficha rica: no se reemplaza por el de SteamSpy.
    assert [genre.slug for genre in juego.genres] == ["accion"]


def test_tags_lista_vacia_no_es_error(db: Session) -> None:
    ingest_index_entries(db, [{"appid": 111, "name": "Juego Sin Votos", "owners": "0 .. 0"}])
    report = enrich_next_batch(
        db, limit=10, sleeper=no_sleep, fetch=make_fetch({111: DETAILS_SIN_TAGS})
    )

    assert report.enriched == 1
    juego = db.scalar(select(Game).where(Game.steam_app_id == 111))
    assert juego.tags == []
    assert [genre.slug for genre in juego.genres] == ["indie"]
    assert queue_row(db, 111).status == SYNC_DONE


def test_reenriquecer_actualiza_los_votos(db: Session) -> None:
    ingest_index_entries(db, [INDEX_CS2])
    enrich_next_batch(db, limit=10, sleeper=no_sleep, fetch=make_fetch({730: DETAILS_CS2}))

    fila = queue_row(db, 730)
    fila.status = SYNC_PENDING  # p. ej. un refresco periódico lo reencola
    db.commit()

    actualizados = {**DETAILS_CS2, "tags": {**DETAILS_CS2["tags"], "FPS": 100_000}}
    enrich_next_batch(db, limit=10, sleeper=no_sleep, fetch=make_fetch({730: actualizados}))

    cs2 = db.scalar(select(Game).where(Game.steam_app_id == 730))
    fps = db.scalar(select(Tag).where(Tag.slug == "fps"))
    votos = db.scalar(
        select(game_tags.c.votes).where(
            game_tags.c.game_id == cs2.id, game_tags.c.tag_id == fps.id
        )
    )
    assert votos == 100_000
    # La membresía no se duplicó.
    assert len([tag for tag in cs2.tags if tag.slug == "fps"]) == 1


# ---------------------------------------------------------------------------
# El invariante y el corte por fallos consecutivos
# ---------------------------------------------------------------------------


def test_caida_de_steamspy_no_marca_skipped_ni_toca_el_juego(db: Session) -> None:
    """El invariante del incidente de los 5 juegos borrados, ahora en lote."""
    ingest_index_entries(db, [INDEX_CS2])
    juego_antes = db.scalar(select(Game).where(Game.steam_app_id == 730))
    nombre_antes = juego_antes.name

    report = enrich_next_batch(
        db,
        limit=10,
        sleeper=no_sleep,
        fetch=make_fetch({730: SteamSpyUnavailable("timeout")}),
    )

    assert report.unavailable == 1 and report.enriched == 0 and report.skipped == 0

    fila = queue_row(db, 730)
    assert fila.status == SYNC_PENDING  # NO skipped: no saber no es saber que no está
    assert fila.attempts == 1
    assert "timeout" in fila.last_error

    juego = db.scalar(select(Game).where(Game.steam_app_id == 730))
    assert juego is not None and juego.name == nombre_antes
    assert juego.tags == []  # intacto: ni enriquecido ni degradado


def test_appid_sin_datos_se_marca_skipped_pero_el_juego_no_se_borra(db: Session) -> None:
    ingest_index_entries(db, [{"appid": 999999, "name": "Banda Sonora X", "owners": "0 .. 0"}])

    report = enrich_next_batch(
        db, limit=10, sleeper=no_sleep, fetch=make_fetch({999999: DETAILS_EMPTY})
    )

    assert report.skipped == 1
    assert queue_row(db, 999999).status == SYNC_SKIPPED
    # Retirar la ficha del catálogo es política de catálogo, no del worker.
    assert db.scalar(select(Game).where(Game.steam_app_id == 999999)) is not None


def test_n_fallos_consecutivos_cortan_el_lote(db: Session) -> None:
    entries = [
        {"appid": appid, "name": f"Juego {appid}", "owners": f"{1000 - appid} .. {1000 - appid}"}
        for appid in range(1, 8)
    ]
    ingest_index_entries(db, entries)

    report = enrich_next_batch(
        db,
        limit=10,
        max_consecutive_failures=3,
        sleeper=no_sleep,
        fetch=make_fetch({appid: SteamSpyUnavailable("caida") for appid in range(1, 8)}),
    )

    assert report.aborted is True
    assert report.processed == 3 and report.unavailable == 3
    # Los tres intentados quedan pendientes con su intento anotado; el resto
    # ni se tocó: nada se pierde, la próxima corrida retoma.
    assert [
        row.attempts
        for row in db.scalars(select(SteamSpySync).order_by(SteamSpySync.steam_app_id))
    ] == [1, 1, 1, 0, 0, 0, 0]
    assert all(row.status == SYNC_PENDING for row in db.scalars(select(SteamSpySync)))


def test_un_exito_resetea_el_contador_de_fallos(db: Session) -> None:
    entries = [
        {"appid": 1, "name": "Falla Uno", "owners": "400 .. 400"},
        {"appid": 2, "name": "Falla Dos", "owners": "300 .. 300"},
        {"appid": 3, "name": "Anda", "owners": "200 .. 200"},
        {"appid": 4, "name": "Falla Tres", "owners": "100 .. 100"},
    ]
    ingest_index_entries(db, entries)

    detalle_ok = {**DETAILS_SIN_TAGS, "appid": 3, "name": "Anda"}
    report = enrich_next_batch(
        db,
        limit=10,
        max_consecutive_failures=3,
        sleeper=no_sleep,
        fetch=make_fetch(
            {
                1: SteamSpyUnavailable("x"),
                2: SteamSpyUnavailable("x"),
                3: detalle_ok,
                4: SteamSpyUnavailable("x"),
            }
        ),
    )

    # 2 fallos + 1 éxito + 1 fallo: nunca hubo 3 consecutivos, no se aborta.
    assert report.aborted is False
    assert report.processed == 4 and report.enriched == 1 and report.unavailable == 3


def test_la_cola_se_reanuda_por_prioridad(db: Session) -> None:
    ingest_index_entries(db, [INDEX_STARDEW, INDEX_CS2])  # CS2 tiene más dueños

    fetch = make_fetch({730: DETAILS_CS2, 413150: [SteamSpyUnavailable("caida")]})
    primera = enrich_next_batch(db, limit=1, sleeper=no_sleep, fetch=fetch)
    assert primera.enriched == 1
    assert queue_row(db, 730).status == SYNC_DONE  # primero el de mayor prioridad

    # Segunda corrida: sólo queda Stardew, que ahora falla y queda pendiente.
    segunda = enrich_next_batch(db, limit=5, sleeper=no_sleep, fetch=fetch)
    assert segunda.processed == 1 and segunda.unavailable == 1
    assert queue_row(db, 413150).status == SYNC_PENDING
    assert steamspy_service.pending_count(db) == 1


def test_respeta_la_cadencia_entre_pedidos(db: Session) -> None:
    ingest_index_entries(db, [INDEX_CS2, INDEX_STARDEW])
    pausas: list[float] = []

    detalle_stardew = {**DETAILS_SIN_TAGS, "appid": 413150, "name": "Stardew Valley"}
    enrich_next_batch(
        db,
        limit=10,
        sleep_seconds=1.0,
        sleeper=pausas.append,
        fetch=make_fetch({730: DETAILS_CS2, 413150: detalle_stardew}),
    )

    # N pedidos => N-1 pausas: se duerme ENTRE pedidos, no antes del primero.
    assert pausas == [1.0]
