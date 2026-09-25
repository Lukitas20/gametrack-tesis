"""Pruebas del motor de recomendación sobre un catálogo mínimo controlado.

Se arma a mano una base en memoria con dos grupos de usuarios de gustos
opuestos, de modo que cada estrategia tenga una respuesta verificable.
"""

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.ml.recommender import RecommenderEngine, invalidate_engine
from app.models import Game, Genre, Rating, RecommendationSource, Tag, User, UserRole

# Tres RPG narrativos y tres estrategias, dos mundos sin superposición.
CATALOG = [
    ("rpg-uno", "RPG Uno", "rpg", "narrativo", 90),
    ("rpg-dos", "RPG Dos", "rpg", "narrativo", 88),
    ("rpg-tres", "RPG Tres", "rpg", "narrativo", 85),
    ("estrategia-uno", "Estrategia Uno", "estrategia", "gestion", 89),
    ("estrategia-dos", "Estrategia Dos", "estrategia", "gestion", 87),
    ("estrategia-tres", "Estrategia Tres", "estrategia", "gestion", 60),
]


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    invalidate_engine()


@pytest.fixture
def catalog(db: Session) -> dict[str, Game]:
    genres = {slug: Genre(slug=slug, name=slug) for slug in ("rpg", "estrategia")}
    tags = {slug: Tag(slug=slug, name=slug) for slug in ("narrativo", "gestion")}
    db.add_all([*genres.values(), *tags.values()])

    games = {}
    for slug, name, genre, tag, metacritic in CATALOG:
        game = Game(
            slug=slug,
            name=name,
            description=f"Un juego de {genre}.",
            metacritic=metacritic,
            developer="Estudio",
        )
        game.genres = [genres[genre]]
        game.tags = [tags[tag]]
        db.add(game)
        games[slug] = game

    db.commit()
    return games


def _rate(db: Session, user: User, game: Game, score: float) -> None:
    db.add(Rating(user_id=user.id, game_id=game.id, score=score))


@pytest.fixture
def populated(db: Session, catalog: dict[str, Game]) -> dict:
    """Cinco fans del RPG y cinco de la estrategia, con gustos invertidos."""
    users = {}
    for index in range(5):
        fan_rpg = User(username=f"rpg{index}", role=UserRole.PLAYER)
        fan_strategy = User(username=f"estrategia{index}", role=UserRole.PLAYER)
        db.add_all([fan_rpg, fan_strategy])
        db.flush()
        users[f"rpg{index}"] = fan_rpg
        users[f"estrategia{index}"] = fan_strategy

        for slug in ("rpg-uno", "rpg-dos", "rpg-tres"):
            _rate(db, fan_rpg, catalog[slug], 5.0)
            _rate(db, fan_strategy, catalog[slug], 2.0)
        for slug in ("estrategia-uno", "estrategia-dos", "estrategia-tres"):
            _rate(db, fan_rpg, catalog[slug], 2.0)
            _rate(db, fan_strategy, catalog[slug], 5.0)

    for game in catalog.values():
        game.ratings_count = 10
        game.avg_rating = 3.5
    db.commit()
    return {"users": users, "games": catalog}


# --- Contenido -------------------------------------------------------------


def test_similares_comparte_genero(db: Session, catalog: dict[str, Game]) -> None:
    engine = RecommenderEngine(db)
    similar = engine.similar_games(catalog["rpg-uno"].id, limit=2)
    nombres = {engine.game_names[r.game_id] for r in similar}
    assert nombres == {"RPG Dos", "RPG Tres"}


def test_similares_de_juego_inexistente_devuelve_vacio(db: Session, catalog) -> None:
    assert RecommenderEngine(db).similar_games(9999) == []


def test_preferencias_sin_historial_usan_el_genero(db: Session, catalog) -> None:
    engine = RecommenderEngine(db)
    recomendaciones = engine.recommend(user_id=None, preferred_genres=["estrategia"], limit=3)
    assert recomendaciones
    assert all("Estrategia" in engine.game_names[r.game_id] for r in recomendaciones)


# --- Colaborativo ----------------------------------------------------------


def test_colaborativo_capta_el_grupo_de_gusto(db: Session, populated: dict) -> None:
    """Un usuario que puntúa alto un RPG y bajo una estrategia recibe RPG."""
    nuevo = User(username="nuevo", role=UserRole.PLAYER)
    db.add(nuevo)
    db.flush()
    _rate(db, nuevo, populated["games"]["rpg-uno"], 5.0)
    _rate(db, nuevo, populated["games"]["estrategia-uno"], 2.0)
    db.commit()

    engine = RecommenderEngine(db)
    recomendaciones = engine.recommend(user_id=nuevo.id, limit=2, strategy="colaborativo")
    nombres = [engine.game_names[r.game_id] for r in recomendaciones]
    assert all(nombre.startswith("RPG") for nombre in nombres), nombres
    assert recomendaciones[0].source is RecommendationSource.COLLABORATIVE


def test_una_sola_valoracion_no_da_senal_colaborativa(db: Session, populated: dict) -> None:
    """Con un único rating, centrar por la media anula toda la desviación.

    El motor debe reconocer que no hay nada que ordenar y caer en popularidad,
    en lugar de devolver un orden arbitrario entre predicciones empatadas.
    """
    nuevo = User(username="unico", role=UserRole.PLAYER)
    db.add(nuevo)
    db.flush()
    _rate(db, nuevo, populated["games"]["rpg-uno"], 5.0)
    db.commit()

    engine = RecommenderEngine(db)
    assert engine._collaborative_scores(nuevo.id) is None
    recomendaciones = engine.recommend(user_id=nuevo.id, limit=2, strategy="colaborativo")
    assert recomendaciones[0].source is RecommendationSource.POPULARITY


def test_no_recomienda_lo_ya_valorado(db: Session, populated: dict) -> None:
    engine = RecommenderEngine(db)
    fan = populated["users"]["rpg0"]
    recomendados = {r.game_id for r in engine.recommend(user_id=fan.id, limit=10)}
    assert not recomendados & {game.id for game in populated["games"].values()}


# --- Arranque en frío ------------------------------------------------------


def test_usuario_sin_datos_cae_en_popularidad(db: Session, populated: dict) -> None:
    engine = RecommenderEngine(db)
    recomendaciones = engine.recommend(user_id=None, preferred_genres=[], limit=3)
    assert recomendaciones
    assert all(r.source is RecommendationSource.POPULARITY for r in recomendaciones)


def test_forzar_colaborativo_sin_historial_degrada_a_popularidad(
    db: Session, populated: dict
) -> None:
    engine = RecommenderEngine(db)
    huerfano = User(username="huerfano", role=UserRole.PLAYER)
    db.add(huerfano)
    db.commit()

    recomendaciones = engine.recommend(user_id=huerfano.id, limit=3, strategy="colaborativo")
    assert recomendaciones
    assert recomendaciones[0].source is RecommendationSource.POPULARITY


def test_popularidad_penaliza_la_poca_evidencia(db: Session, catalog: dict) -> None:
    """Un 5.0 con dos votos no debe superar a un 4.6 con doscientos.

    El resto del catálogo necesita votos para que la media global del prior
    sea realista: si sólo hubiera dos juegos valorados y ambos por encima de
    4,5, el prior quedaría tan alto que no penalizaría nada.
    """
    for game in catalog.values():
        game.avg_rating, game.ratings_count = 3.4, 50

    poco_votado = catalog["rpg-uno"]
    poco_votado.avg_rating, poco_votado.ratings_count = 5.0, 2
    muy_votado = catalog["rpg-dos"]
    muy_votado.avg_rating, muy_votado.ratings_count = 4.6, 200
    db.commit()

    engine = RecommenderEngine(db)
    indices = {game_id: i for i, game_id in enumerate(engine.game_ids)}
    assert engine.popularity[indices[muy_votado.id]] > engine.popularity[indices[poco_votado.id]]


# --- Híbrido ---------------------------------------------------------------


def test_hibrido_combina_ambas_senales(db: Session, populated: dict) -> None:
    engine = RecommenderEngine(db)
    fan = populated["users"]["rpg0"]
    # Se le quita una valoración para que quede algo por recomendar.
    rating = db.query(Rating).filter_by(
        user_id=fan.id, game_id=populated["games"]["rpg-tres"].id
    ).one()
    db.delete(rating)
    db.commit()

    engine = RecommenderEngine(db)
    recomendaciones = engine.recommend(user_id=fan.id, limit=1, strategy="hibrido")
    assert recomendaciones[0].source is RecommendationSource.HYBRID
    assert set(recomendaciones[0].components) == {"contenido", "colaborativo"}
    assert engine.game_names[recomendaciones[0].game_id] == "RPG Tres"


def test_toda_recomendacion_trae_explicacion(db: Session, populated: dict) -> None:
    engine = RecommenderEngine(db)
    fan = populated["users"]["estrategia0"]
    for strategy in ("auto", "contenido", "colaborativo", "popularidad"):
        for recommendation in engine.recommend(user_id=fan.id, limit=3, strategy=strategy):
            assert recommendation.reason.strip()
            assert 0.0 <= recommendation.score <= 1.0


# --- Fichas pendientes de Steam ---------------------------------------------


def test_ficha_pendiente_no_entra_al_motor(db: Session, catalog: dict[str, Game]) -> None:
    pendiente = Game(steam_app_id=730, slug="pendiente", name="Ficha Pendiente")
    db.add(pendiente)
    db.commit()

    engine = RecommenderEngine(db)

    assert pendiente.id not in engine.game_ids
    assert len(engine.game_ids) == len(catalog)


def test_similares_de_una_ficha_pendiente_devuelve_vacio(
    db: Session, catalog: dict[str, Game]
) -> None:
    pendiente = Game(steam_app_id=730, slug="pendiente", name="Ficha Pendiente")
    db.add(pendiente)
    db.commit()

    engine = RecommenderEngine(db)

    assert engine.similar_games(pendiente.id) == []


def test_catalogo_sin_ningun_juego_enriquecido_no_rompe(db: Session) -> None:
    """Puede pasar de verdad: correr import_steam_appindex.py antes de
    sembrar cualquier catálogo real deja la base entera en fichas
    pendientes. TfidfVectorizer y cosine_similarity revientan con cero
    muestras si no se los esquiva a propósito."""
    db.add_all(
        [
            Game(steam_app_id=730, slug="uno", name="Uno"),
            Game(steam_app_id=620, slug="dos", name="Dos"),
        ]
    )
    db.commit()

    engine = RecommenderEngine(db)

    assert engine.game_ids == []
    assert engine.recommend(user_id=None, preferred_genres=["accion"], limit=5) == []


def test_valoracion_negativa_no_se_convierte_en_preferencia(db: Session, catalog) -> None:
    user = User(username="no-me-gusto", role=UserRole.PLAYER)
    db.add(user)
    db.flush()
    _rate(db, user, catalog["rpg-uno"], 1.0)
    db.commit()

    engine = RecommenderEngine(db)
    results = engine.recommend(user.id, limit=3, discovery="familiar")
    assert all(engine.game_names[item.game_id].startswith("Estrategia") for item in results)
    assert all("valoraste bien" not in item.reason for item in results)
    assert all(any("negativas" in signal for signal in item.signals) for item in results)


def test_explicacion_solo_cita_juegos_que_gustaron(db: Session, catalog) -> None:
    user = User(username="gustos-mixtos", role=UserRole.PLAYER)
    db.add(user)
    db.flush()
    _rate(db, user, catalog["rpg-uno"], 1.0)
    _rate(db, user, catalog["estrategia-uno"], 5.0)
    db.commit()

    engine = RecommenderEngine(db)
    results = engine.recommend(user.id, limit=5, discovery="familiar")
    assert results[0].reason == "Se parece a Estrategia Uno, que valoraste bien"
    assert all("RPG Uno, que valoraste bien" not in item.reason for item in results)


def test_una_sola_coincidencia_no_justifica_colaborativo(db: Session, catalog) -> None:
    current = User(username="actual", role=UserRole.PLAYER)
    other = User(username="otra-persona", role=UserRole.PLAYER)
    db.add_all([current, other])
    db.flush()
    for user in (current, other):
        _rate(db, user, catalog["rpg-uno"], 5.0)
        _rate(db, user, catalog["estrategia-uno"], 1.0)
    _rate(db, other, catalog["rpg-dos"], 5.0)
    db.commit()

    engine = RecommenderEngine(db)
    assert engine._collaborative_scores(current.id) is None
    results = engine.recommend(current.id, strategy="hibrido", limit=2)
    assert all(item.source is RecommendationSource.CONTENT for item in results)
    assert all("compartidos" not in item.reason for item in results)


def test_evidencia_colaborativa_crece_con_usuarios_independientes(db: Session, catalog) -> None:
    current = User(username="evidencia", role=UserRole.PLAYER)
    db.add(current)
    db.flush()
    _rate(db, current, catalog["rpg-uno"], 5.0)
    _rate(db, current, catalog["estrategia-uno"], 1.0)
    strengths = []
    for index in range(10):
        other = User(username=f"evidencia-{index}", role=UserRole.PLAYER)
        db.add(other)
        db.flush()
        _rate(db, other, catalog["rpg-uno"], 5.0)
        _rate(db, other, catalog["rpg-dos"], 5.0)
        _rate(db, other, catalog["estrategia-uno"], 1.0)
        if index in (1, 9):
            db.commit()
            engine = RecommenderEngine(db)
            _, evidence, _ = engine._collaborative_evidence(current.id)
            strengths.append(evidence[engine._game_index[catalog["rpg-dos"].id]])
    assert 0.0 < strengths[0] < strengths[1] < 1.0


def test_descubrir_aporta_variedad_sin_alterar_el_indice(db: Session, catalog) -> None:
    for slug, game in catalog.items():
        game.avg_rating = 4.6 if slug.startswith("rpg") else 4.3
        game.ratings_count = 100
    db.commit()
    engine = RecommenderEngine(db)

    familiar = engine.recommend(None, limit=3, discovery="familiar")
    explore = engine.recommend(None, limit=3, discovery="explore")
    assert all(engine.game_names[item.game_id].startswith("RPG") for item in familiar)
    assert engine.game_names[explore[1].game_id].startswith("Estrategia")
    all_scores = {item.game_id: item.score for item in engine.recommend(None, limit=6, discovery="familiar")}
    assert all(item.score == all_scores[item.game_id] for item in explore)
    assert explore == engine.recommend(None, limit=3, discovery="explore")
    excluded = {explore[0].game_id}
    assert not excluded & {item.game_id for item in engine.recommend(None, exclude=excluded)}


@pytest.mark.parametrize("strategy", ["auto", "contenido", "colaborativo", "hibrido", "popularidad"])
def test_aportes_ponderados_suman_el_indice(db: Session, populated, strategy) -> None:
    user = User(username="aportes", role=UserRole.PLAYER)
    db.add(user)
    db.flush()
    _rate(db, user, populated["games"]["rpg-uno"], 5.0)
    _rate(db, user, populated["games"]["estrategia-uno"], 1.0)
    db.commit()
    results = RecommenderEngine(db).recommend(user.id, strategy=strategy)
    assert results
    for item in results:
        assert item.score == pytest.approx(sum(item.components.values()), abs=1e-8)
        assert 0.0 <= item.score <= 1.0
        assert all(0.0 <= value <= 1.0 for value in item.components.values())
        assert item.reason == item.signals[0]


def test_asistente_conserva_compatibilidad_y_aportes(db: Session, catalog) -> None:
    engine = RecommenderEngine(db)
    results = engine.suggest_by_mood(["rpg"], limit=3, exclude={catalog["rpg-uno"].id})
    assert results
    assert all(item.game_id != catalog["rpg-uno"].id for item in results)
    for item in results:
        assert item.score == pytest.approx(sum(item.components.values()), abs=1e-8)


def test_hibrido_sin_peso_colaborativo_no_atribuye_patrones(
    db: Session, populated: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La explicación debe describir los canales que realmente aportaron."""
    monkeypatch.setattr(settings, "REC_COLLAB_WEIGHT", 0.0)
    user = User(username="solo-contenido", role=UserRole.PLAYER)
    db.add(user)
    db.flush()
    _rate(db, user, populated["games"]["rpg-uno"], 5.0)
    _rate(db, user, populated["games"]["estrategia-uno"], 1.0)
    db.commit()

    engine = RecommenderEngine(db)
    assert engine._collaborative_evidence(user.id) is not None
    results = engine.recommend(user.id, strategy="hibrido", discovery="familiar")
    assert results
    for item in results:
        assert item.source is RecommendationSource.CONTENT
        assert item.components["colaborativo"] == 0.0
        assert all("Patrones de valoración" not in signal for signal in item.signals)
