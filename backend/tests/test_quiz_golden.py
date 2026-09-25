"""Golden tests del asistente "¿Qué jugamos hoy?".

Codifican expectativas canónicas del tipo "competir + en línea tiene que
rankear un shooter competitivo por encima de un simulador de granja" como
regresión automática: convierten el juicio "a ojo" sobre la calidad de las
sugerencias en asserts que corren con el resto de la suite.

Diseño — por qué un catálogo canónico y no la base de desarrollo:

* La base real está gitignoreada y diverge entre integrantes: un test que
  dependa de ella pasa en una máquina y falla en otra, que es exactamente lo
  contrario de una red de seguridad. El catálogo de acá es un *fixture
  canónico*: 17 juegos reales con los slugs de plataforma tal como los
  produce la tienda (``jcj``, ``cooperativo-en-linea``) y las etiquetas
  comunitarias con votos tal como las trae la ingesta de SteamSpy — de
  hecho, se siembran ejecutando ``steamspy_service.apply_appdetails``, la
  misma ruta de código que la ingesta real, para que estos tests también
  vigilen esa frontera de integración.
* Las expectativas son de sentido común verificable ("relajarme + solo no
  puede devolver Counter-Strike 2"), no de scores exactos: sobreviven a
  cambios de pesos y de catálogo mientras el comportamiento siga siendo el
  correcto. Si un refactor las rompe, rompió algo que un usuario notaría.
* Protegen de regresiones; NO miden si el catálogo real mejoró. Esa pieza es
  el arnés de anotación (pendiente), no esta suite ni el modo live.
* Para correr las mismas expectativas contra la base de desarrollo local:

      GOLDEN_LIVE=1 pytest tests/test_quiz_golden.py -k live -rs

Los payloads usan el contrato nuevo (claves ``mood``/``company`` resueltas
por ``app.ml.quiz_vocab`` en el servidor); un test aparte fija que el
contrato viejo del frontend legado sigue funcionando.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.database import get_db
from app.main import app
from app.ml.recommender import invalidate_engine
from app.models import Aspect, Game, Genre, Review, ReviewAspect, Sentiment, Tag
from app.services import steam_service, steamspy_service

TIME = {"tarde": 15, "finde": 40, None: None}


def build_payload(
    *,
    mood: str,
    company: str | None = None,
    time: str | None = None,
    aspect: str | None = None,
) -> dict:
    """Payload de ``POST /quiz/suggest`` con el contrato nuevo."""
    return {
        "mood": mood,
        "company": company,
        "max_playtime": TIME[time],
        "priority_aspect": aspect,
    }


# ---------------------------------------------------------------------------
# Catálogo canónico
# ---------------------------------------------------------------------------

GENRES = {
    "accion": "Acción",
    "aventura": "Aventura",
    "rol": "Rol",
    "casual": "Casual",
    "simuladores": "Simuladores",
    "estrategia": "Estrategia",
    "deportes": "Deportes",
    "carreras": "Carreras",
    "indie": "Indie",
}

PLATFORM_TAGS = {
    "un-jugador": "Un jugador",
    "multijugador": "Multijugador",
    "jcj": "JcJ",
    "jcj-en-linea": "JcJ en línea",
    "cooperativo": "Cooperativo",
    "cooperativo-en-linea": "Cooperativo en línea",
    "pantalla-partida-compartida": "Pantalla partida/compartida",
    "multijugador-multiplataforma": "Multijugador multiplataforma",
    # Ruido realista de plataforma: presente a propósito. Ya NO forma parte
    # del corpus de contenido (sólo las comunitarias y los géneros), y estos
    # tests lo mantienen para vigilar que siga sin pesar.
    "logros-de-steam": "Logros de Steam",
    "cromos-de-steam": "Cromos de Steam",
    "steam-cloud": "Steam Cloud",
}

_NOISE = ["logros-de-steam", "cromos-de-steam", "steam-cloud"]

# (slug, nombre, géneros, tags de plataforma, tags comunitarias con votos,
#  mediana de horas de reseñadores, avg_rating, ratings_count)
# Mediana None = sin dato (el filtro no excluye) o juego-servicio sin
# concepto de "terminar". Los votos siguen proporciones realistas.
CATALOG = [
    (
        "counter-strike-2", "Counter-Strike 2",
        ["accion"],
        ["multijugador", "jcj", "jcj-en-linea", "multijugador-multiplataforma", *_NOISE[2:]],
        {"FPS": 91172, "Shooter": 65634, "Multiplayer": 62536, "Competitive": 53536,
         "Team-Based": 46549, "e-sports": 43682, "PvP": 34587, "Free to Play": 30000},
        163.9, 4.3, 5000,
    ),
    (
        "dota-2", "Dota 2",
        ["accion", "estrategia"],
        ["multijugador", "jcj", "jcj-en-linea", *_NOISE[2:]],
        {"MOBA": 60000, "Multiplayer": 50000, "Free to Play": 45000, "Competitive": 40000,
         "Team-Based": 38000, "PvP": 35000, "Strategy": 30000, "e-sports": 25000},
        512.0, 4.0, 4000,
    ),
    (
        "apex-legends", "Apex Legends",
        ["accion"],
        ["multijugador", "jcj-en-linea", "cooperativo-en-linea",
         "multijugador-multiplataforma", *_NOISE],
        {"Battle Royale": 70000, "FPS": 60000, "Multiplayer": 55000,
         "Free to Play": 50000, "PvP": 30000, "Team-Based": 25000},
        100.4, 3.9, 3500,
    ),
    (
        "trackmania", "Trackmania",
        ["carreras"],
        ["un-jugador", "multijugador", "jcj-en-linea", *_NOISE[2:]],
        {"Racing": 30000, "Multiplayer": 15000, "Competitive": 12000,
         "Free to Play": 10000, "e-sports": 5000},
        None, 4.1, 700,
    ),
    (
        "age-of-empires-ii-definitive-edition", "Age of Empires II: Definitive Edition",
        ["estrategia"],
        ["un-jugador", "multijugador", "jcj-en-linea", "cooperativo-en-linea", *_NOISE],
        {"Strategy": 40000, "RTS": 30000, "Multiplayer": 20000,
         "Competitive": 15000, "Singleplayer": 12000},
        35.0, 4.7, 2000,
    ),
    (
        "forza-horizon-5", "Forza Horizon 5",
        ["carreras", "deportes"],
        ["un-jugador", "multijugador", "jcj-en-linea", *_NOISE],
        {"Racing": 50000, "Open World": 40000, "Multiplayer": 25000,
         "Driving": 20000, "Singleplayer": 15000},
        None, 4.4, 1800,  # sin mediana: la falta de dato no excluye
    ),
    (
        "the-witcher-3-wild-hunt", "The Witcher 3: Wild Hunt",
        ["rol"],
        ["un-jugador", *_NOISE],
        {"Open World": 45000, "Story Rich": 40000, "RPG": 38000,
         "Singleplayer": 30000, "Atmospheric": 25000, "Choices Matter": 20000},
        53.5, 4.8, 2500,
    ),
    (
        "disco-elysium-the-final-cut", "Disco Elysium - The Final Cut",
        ["rol"],
        ["un-jugador", *_NOISE[:2]],
        {"Story Rich": 30000, "Choices Matter": 25000, "RPG": 20000,
         "Detective": 15000, "Singleplayer": 12000, "Atmospheric": 10000},
        25.0, 4.6, 800,
    ),
    (
        "cyberpunk-2077", "Cyberpunk 2077",
        ["rol"],
        ["un-jugador", *_NOISE],
        {"Open World": 40000, "Story Rich": 35000, "RPG": 30000,
         "Singleplayer": 25000, "Atmospheric": 20000, "Futuristic": 15000},
        30.0, 4.2, 3000,
    ),
    (
        "hades", "Hades",
        ["accion", "indie", "rol"],
        ["un-jugador", *_NOISE],
        {"Roguelike": 40000, "Action Roguelike": 35000, "Singleplayer": 20000,
         "Story Rich": 18000, "Difficult": 15000},
        39.0, 4.7, 1500,
    ),
    (
        "celeste", "Celeste",
        ["accion", "indie"],
        ["un-jugador", *_NOISE],
        {"Difficult": 30000, "Precision Platformer": 28000, "Platformer": 25000,
         "Singleplayer": 15000, "Great Soundtrack": 12000},
        14.2, 4.7, 900,
    ),
    (
        "stardew-valley", "Stardew Valley",
        ["simuladores", "rol", "indie"],
        ["un-jugador", "multijugador", "cooperativo-en-linea", *_NOISE],
        {"Farming Sim": 45000, "Relaxing": 35000, "Pixel Graphics": 30000,
         "Singleplayer": 25000, "RPG": 22000, "Online Co-Op": 20000},
        65.6, 4.8, 3000,
    ),
    (
        "powerwash-simulator", "PowerWash Simulator",
        ["simuladores", "casual"],
        ["un-jugador", "cooperativo-en-linea", *_NOISE],
        {"Relaxing": 25000, "Simulation": 20000, "Casual": 15000,
         "Online Co-Op": 12000, "Singleplayer": 10000},
        12.0, 4.5, 600,
    ),
    (
        "unpacking", "Unpacking",
        ["casual", "indie"],
        ["un-jugador", *_NOISE[:2]],
        {"Relaxing": 15000, "Casual": 14000, "Cozy": 12000,
         "Short": 8000, "Singleplayer": 6000},
        4.0, 4.3, 400,
    ),
    (
        "a-short-hike", "A Short Hike",
        ["aventura", "casual", "indie"],
        ["un-jugador", *_NOISE[:2]],
        {"Relaxing": 12000, "Cozy": 11000, "Exploration": 9000,
         "Short": 7000, "Adventure": 6000, "Singleplayer": 4000},
        5.0, 4.8, 500,
    ),
    (
        "it-takes-two", "It Takes Two",
        ["accion", "aventura"],
        ["cooperativo", "cooperativo-en-linea", "pantalla-partida-compartida", *_NOISE],
        {"Co-op": 40000, "Split Screen": 30000, "Local Co-Op": 25000,
         "Multiplayer": 20000, "Adventure": 15000},
        10.6, 4.8, 1200,
    ),
    (
        "overcooked-2", "Overcooked! 2",
        ["casual", "indie", "simuladores"],
        ["multijugador", "cooperativo", "cooperativo-en-linea",
         "pantalla-partida-compartida", *_NOISE[:2]],
        {"Co-op": 30000, "Local Co-Op": 25000, "Multiplayer": 20000,
         "Casual": 15000, "Funny": 12000},
        15.0, 4.4, 1000,
    ),
]

# Salida ABSA materializada (el léxico tiene su propia suite).
ASPECTS = [
    ("the-witcher-3-wild-hunt", Aspect.STORY, 0.9, Sentiment.POSITIVE,
     "La historia es una obra maestra, hasta la misión secundaria más chica está bien escrita"),
    ("the-witcher-3-wild-hunt", Aspect.PERFORMANCE, 0.2, Sentiment.POSITIVE,
     "Corre estable, con algún bajón puntual en Novigrado"),
    ("disco-elysium-the-final-cut", Aspect.STORY, 0.85, Sentiment.POSITIVE,
     "Nunca leí algo así en un videojuego, los diálogos son literatura"),
    ("cyberpunk-2077", Aspect.STORY, 0.6, Sentiment.POSITIVE,
     "La historia de V y Johnny te atrapa de principio a fin"),
    ("cyberpunk-2077", Aspect.PERFORMANCE, -0.7, Sentiment.NEGATIVE,
     "Se me crashea cada dos horas y los bugs visuales no paran"),
    ("celeste", Aspect.GAMEPLAY, 0.9, Sentiment.POSITIVE,
     "El control es perfecto: cada muerte es culpa tuya y por eso engancha"),
    ("forza-horizon-5", Aspect.GRAPHICS, 0.9, Sentiment.POSITIVE,
     "Visualmente es lo más lindo que corrió mi PC"),
]


def seed_catalog(db: Session) -> None:
    genres = {slug: Genre(slug=slug, name=name) for slug, name in GENRES.items()}
    tags = {
        slug: Tag(slug=slug, name=name, kind="platform")
        for slug, name in PLATFORM_TAGS.items()
    }
    db.add_all([*genres.values(), *tags.values()])

    games: dict[str, Game] = {}
    for slug, name, game_genres, platform, community, hours, avg, count in CATALOG:
        game = Game(
            slug=slug,
            name=name,
            median_review_hours=hours,
            avg_rating=avg,
            ratings_count=count,
        )
        game.genres = [genres[g] for g in game_genres]
        game.tags = [tags[t] for t in platform]
        db.add(game)
        db.flush()
        # Las comunitarias entran por la MISMA ruta que la ingesta real:
        # membresía + votos + kind, tal como las dejaría el worker.
        steamspy_service.apply_appdetails(db, game, {"tags": community})
        games[slug] = game

    for slug, aspect, score, sentiment, evidence in ASPECTS:
        review = Review(
            game_id=games[slug].id, content=evidence, language="es",
            source="steam", is_analyzed=True,
        )
        db.add(review)
        db.flush()
        db.add(
            ReviewAspect(
                review_id=review.id, game_id=games[slug].id, aspect=aspect,
                sentiment=sentiment, score=score, evidence=evidence,
            )
        )
    db.commit()


# Índice slug -> todas sus etiquetas (plataforma + comunidad), para asserts.
GAME_TAGS: dict[str, set[str]] = {
    slug: set(platform) | {steam_service.slugify(name) for name in community}
    for slug, _, _, platform, community, *_ in CATALOG
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def sin_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """El endpoint refresca los tres elegidos contra Steam: acá nunca.

    ``True`` = "el juego sigue existiendo, no hizo falta refrescar": el valor
    que el resto del código interpreta como inocuo (``False`` significa "el
    juego se borró" para ``_require_game``).
    """
    monkeypatch.setattr(steam_service, "maybe_refresh", lambda db, game: True)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    seed_catalog(session)
    yield session
    session.close()
    invalidate_engine()


@pytest.fixture
def client(db: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def suggest(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/quiz/suggest", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def pick_slugs(body: dict) -> list[str]:
    return [pick["game"]["slug"] for pick in body["picks"]]


# ---------------------------------------------------------------------------
# Tabla de casos canónicos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenCase:
    id: str
    mood: str
    company: str | None = None
    time: str | None = None
    aspect: str | None = None
    must_include: tuple[str, ...] = ()
    must_exclude: tuple[str, ...] = ()
    # (a, b): b no puede aparecer sin a, ni por delante de a ("a domina a b").
    rank_above: tuple[tuple[str, str], ...] = ()
    # Todos los elegidos deben tener al menos una de estas etiquetas.
    picks_must_have_tag: tuple[str, ...] = ()
    # None = no se afirma nada; tupla = exactamente esos criterios relajados.
    expect_relaxed: tuple[str, ...] | None = ()


CASES = [
    # --- El caso testigo: competir no es "multijugador" --------------------
    GoldenCase(
        id="competir-en-linea-rankea-cs2-sobre-stardew",
        mood="competir", company="en-linea",
        rank_above=(("counter-strike-2", "stardew-valley"),),
        must_exclude=("stardew-valley", "powerwash-simulator", "unpacking"),
        picks_must_have_tag=("jcj", "jcj-en-linea", "pvp"),
    ),
    GoldenCase(
        id="competir-excluye-coop-tranquilos-aunque-sean-multijugador",
        mood="competir", company="en-linea",
        must_exclude=("stardew-valley", "overcooked-2", "it-takes-two"),
    ),
    GoldenCase(
        id="competir-pesa-los-votos-cs2-domina-a-forza",
        mood="competir", company="en-linea",
        # Forza pasa los filtros (jcj-en-línea) pero su comunidad lo etiquetó
        # Racing/Open World, no Competitive/PvP: el peso de los votos tiene
        # que rankear a CS2 y Dota por encima. Antes de las etiquetas
        # comunitarias esta distinción era literalmente imposible.
        rank_above=(
            ("counter-strike-2", "forza-horizon-5"),
            ("dota-2", "forza-horizon-5"),
        ),
    ),
    GoldenCase(
        id="competir-solo-es-jcj-con-campania-no-cs2",
        mood="competir", company="solo",
        must_exclude=("counter-strike-2", "dota-2", "apex-legends"),
        picks_must_have_tag=("jcj", "jcj-en-linea", "pvp"),
    ),
    # --- Relajarse: la inversa del testigo ---------------------------------
    GoldenCase(
        id="relajarme-solo-jamas-devuelve-cs2",
        mood="relajarme", company="solo",
        must_exclude=("counter-strike-2", "dota-2", "apex-legends"),
        picks_must_have_tag=("un-jugador",),
    ),
    GoldenCase(
        id="relajarme-solo-prefiere-lo-relajante",
        mood="relajarme", company="solo",
        must_include=("stardew-valley",),
        rank_above=(
            ("stardew-valley", "counter-strike-2"),
            ("powerwash-simulator", "dota-2"),
        ),
    ),
    GoldenCase(
        id="relajarme-con-amigos-va-al-coop",
        mood="relajarme", company="amigos",
        must_exclude=("counter-strike-2", "dota-2", "celeste", "the-witcher-3-wild-hunt"),
        picks_must_have_tag=(
            "cooperativo", "cooperativo-en-linea", "pantalla-partida-compartida",
            "co-op", "online-co-op", "local-co-op", "split-screen",
        ),
    ),
    # --- Historia y desafío ------------------------------------------------
    GoldenCase(
        id="historia-solo-trae-rol-narrativo",
        mood="historia", company="solo",
        must_include=("the-witcher-3-wild-hunt",),
        must_exclude=("counter-strike-2", "overcooked-2"),
        picks_must_have_tag=("un-jugador",),
    ),
    GoldenCase(
        id="desafio-solo-trae-exigentes-no-contemplativos",
        mood="desafio", company="solo",
        must_include=("celeste",),
        must_exclude=("unpacking", "powerwash-simulator", "a-short-hike"),
        picks_must_have_tag=("un-jugador",),
    ),
    # --- Duración: compromiso vs. sesión -----------------------------------
    GoldenCase(
        id="una-tarde-solo-excluye-campanias-largas",
        mood="historia", company="solo", time="tarde",
        # Medianas > 15 h en juegos finitos: afuera sin relajación.
        must_exclude=(
            "the-witcher-3-wild-hunt", "stardew-valley",
            "disco-elysium-the-final-cut", "cyberpunk-2077", "hades",
        ),
    ),
    GoldenCase(
        id="una-tarde-no-excluye-a-los-juegos-servicio",
        mood="competir", company="en-linea", time="tarde",
        # EL caso que motivó el rediseño de la duración: CS2 tiene 164 h de
        # mediana ACUMULADA porque cada partida dura 40 minutos — es lo que
        # jugás una tarde. AoE2 en cambio es finito (35 h de campaña) y sí
        # queda afuera de la franja.
        must_include=("counter-strike-2",),
        must_exclude=("age-of-empires-ii-definitive-edition",),
        expect_relaxed=(),
    ),
    # --- Aspecto prioritario (ABSA) ----------------------------------------
    GoldenCase(
        id="prioridad-historia-sube-la-evidencia-de-historia",
        mood="historia", company="solo", aspect="historia",
        must_include=("the-witcher-3-wild-hunt",),
        rank_above=(
            ("the-witcher-3-wild-hunt", "cyberpunk-2077"),
            ("disco-elysium-the-final-cut", "cyberpunk-2077"),
        ),
    ),
    GoldenCase(
        id="prioridad-optimizacion-castiga-al-que-crashea",
        mood="historia", company="solo", aspect="optimizacion",
        # Witcher anda bien (+0.2), Cyberpunk crashea (-0.7): con el boost
        # aditivo la evidencia negativa BAJA al juego (con el reordenamiento
        # lexicográfico anterior, "evidencia mala" ganaba a "sin evidencia").
        rank_above=(("the-witcher-3-wild-hunt", "cyberpunk-2077"),),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_golden(client: TestClient, case: GoldenCase) -> None:
    body = suggest(
        client,
        build_payload(mood=case.mood, company=case.company, time=case.time, aspect=case.aspect),
    )
    assert_expectations(case, pick_slugs(body), body)


def assert_expectations(case: GoldenCase, slugs: list[str], body: dict) -> None:
    assert len(slugs) == 3, f"esperaba 3 sugerencias, vinieron {len(slugs)}: {slugs}"
    assert len(set(slugs)) == 3, f"sugerencias repetidas: {slugs}"

    for slug in case.must_include:
        assert slug in slugs, f"esperaba {slug} en el top 3, vino {slugs}"
    for slug in case.must_exclude:
        assert slug not in slugs, f"{slug} no debería aparecer para este ánimo: {slugs}"
    for above, below in case.rank_above:
        if below in slugs:
            assert above in slugs and slugs.index(above) < slugs.index(below), (
                f"{above} debería dominar a {below}, vino {slugs}"
            )
    if case.picks_must_have_tag:
        allowed = set(case.picks_must_have_tag)
        for slug in slugs:
            tags = GAME_TAGS.get(slug)
            if tags is not None:
                assert tags & allowed, (
                    f"{slug} no tiene ninguna de {sorted(allowed)} y fue sugerido igual"
                )
    if case.expect_relaxed is not None:
        assert body["relaxed"] == list(case.expect_relaxed), (
            f"relajación esperada {list(case.expect_relaxed)}, vino {body['relaxed']}: "
            "ningún criterio puede soltarse en silencio ni relajarse de más"
        )


# ---------------------------------------------------------------------------
# Contratos del vocabulario de duración (unitarios: fijan comportamiento
# decidido que el top-3 del endpoint no permite observar directamente)
# ---------------------------------------------------------------------------

from app.ml import quiz_vocab  # noqa: E402


def test_sin_mediana_conocida_no_se_excluye_por_tiempo() -> None:
    """La falta de dato no excluye. Comportamiento decidido: cambiarlo a
    "sin dato no pasa" obliga a discutirlo acá primero."""
    assert quiz_vocab.passes_time_budget(None, {"un-jugador"}, max_hours=15)


def test_juego_finito_largo_no_entra_en_una_tarde() -> None:
    assert not quiz_vocab.passes_time_budget(53.5, {"un-jugador", "story-rich"}, 15)
    assert quiz_vocab.passes_time_budget(53.5, {"un-jugador"}, None)


def test_juego_servicio_queda_exento_del_presupuesto() -> None:
    """CS2: 164 h de mediana ACUMULADA y partidas de 40 minutos. Dos vías de
    detección: sin modo de un jugador, o marcadores de servicio aun con él
    (Trackmania: campaña + free-to-play/e-sports)."""
    cs2 = {"multijugador", "jcj", "pvp", "competitive", "fps"}
    assert quiz_vocab.is_session_based(cs2)
    assert quiz_vocab.passes_time_budget(163.9, cs2, max_hours=15)

    trackmania = {"un-jugador", "racing", "free-to-play", "e-sports"}
    assert quiz_vocab.is_session_based(trackmania)


def test_coop_finito_no_se_exime_del_tiempo_por_falta_de_un_jugador() -> None:
    """Cooperativo no equivale a servicio: no inferir partidas por ausencia
    de una etiqueta. It Takes Two también tiene compromiso de campaña."""
    it_takes_two = {"cooperativo", "co-op", "split-screen", "local-co-op"}
    assert not quiz_vocab.is_session_based(it_takes_two)
    assert not quiz_vocab.passes_time_budget(20, it_takes_two, 15)


@pytest.mark.parametrize("tags", [set(), {"free-to-play"}, {"multiplayer"}])
def test_sin_evidencia_de_partidas_no_se_exime_del_tiempo(tags: set[str]) -> None:
    assert not quiz_vocab.is_session_based(tags)


def test_finito_con_campania_no_es_servicio() -> None:
    witcher = {"un-jugador", "singleplayer", "story-rich", "open-world"}
    assert not quiz_vocab.is_session_based(witcher)
    stardew = {"un-jugador", "multijugador", "online-co-op", "relaxing", "farming-sim"}
    assert not quiz_vocab.is_session_based(stardew)


# ---------------------------------------------------------------------------
# Contratos que no entran en la tabla
# ---------------------------------------------------------------------------


def test_mismo_payload_mismas_sugerencias(client: TestClient) -> None:
    """Determinismo: sin esto, ninguna métrica offline es comparable."""
    payload = build_payload(mood="historia", company="solo", aspect="historia")
    assert pick_slugs(suggest(client, payload)) == pick_slugs(suggest(client, payload))


def test_animos_opuestos_no_comparten_sugerencias(client: TestClient) -> None:
    """La respuesta del usuario tiene que mover el resultado: si competir y
    relajarme comparten un solo juego del top 3, el ánimo no está pesando."""
    competir = set(pick_slugs(suggest(client, build_payload(mood="competir", company="en-linea"))))
    relajarme = set(pick_slugs(suggest(client, build_payload(mood="relajarme", company="solo"))))
    assert not competir & relajarme, f"comparten {competir & relajarme}"


def test_prioridad_de_aspecto_devuelve_evidencia_textual(client: TestClient) -> None:
    """La feature estrella: la cita textual que justifica la elección."""
    body = suggest(client, build_payload(mood="historia", company="solo", aspect="historia"))
    top = body["picks"][0]
    assert top["game"]["slug"] == "the-witcher-3-wild-hunt"
    assert top["aspect_score"] == pytest.approx(0.9)
    assert top["aspect_evidence"] == (
        "La historia es una obra maestra, hasta la misión secundaria más chica está bien escrita"
    )


def test_sin_prioridad_no_hay_evidencia_ni_reordenamiento(client: TestClient) -> None:
    sin = suggest(client, build_payload(mood="historia", company="solo", aspect=None))
    assert all(pick["aspect_evidence"] is None for pick in sin["picks"])
    assert all(pick["aspect_score"] is None for pick in sin["picks"])


def test_escasez_relaja_en_orden_y_lo_declara(client: TestClient) -> None:
    """Cuando los filtros duros dejan menos de tres candidatos, el sistema
    puede relajar, pero declarando exactamente qué soltó y en qué orden
    (duración primero, compañía después). El requisito del ánimo (JcJ) no
    se suelta nunca en esta rama."""
    body = suggest(client, build_payload(mood="competir", company="amigos", time="tarde"))
    slugs = pick_slugs(body)

    assert body["relaxed"] == ["la duración", "con quién jugás"]
    # Apex cumple todo y AoE2 sólo relaja tiempo: aunque los populares
    # puntúen más, no deben desplazar estas coincidencias más cercanas.
    assert slugs[:2] == ["apex-legends", "age-of-empires-ii-definitive-edition"]
    assert body["picks"][0]["relaxed_criteria"] == []
    assert body["picks"][1]["relaxed_criteria"] == ["la duración"]
    for slug in slugs:
        assert GAME_TAGS[slug] & {"jcj", "jcj-en-linea", "pvp"}, (
            f"{slug} no es JcJ: la relajación soltó el ánimo, que es lo único innegociable"
        )


def test_fallback_a_popularidad_declara_que_solto_el_animo(client: TestClient) -> None:
    """Regresión del bug real de degradación silenciosa: si el perfil del
    ánimo no matchea ningún término del corpus, se cae a popularidad y SE
    DECLARA en `relaxed`."""
    payload = {
        "genres": ["genero-inexistente"],
        "mood_tags": [],
        "max_playtime": None,
        "company_tags": [],
        "priority_aspect": None,
    }
    body = suggest(client, payload)
    assert len(body["picks"]) == 3
    assert "el ánimo" in body["relaxed"]


def test_contrato_legado_del_frontend_sigue_funcionando(client: TestClient) -> None:
    """El payload viejo (genres/mood_tags/company_tags) se traduce al
    mecanismo nuevo con peso uniforme: el frontend sin actualizar no rompe."""
    body = suggest(
        client,
        {
            "genres": ["accion", "deportes", "carreras"],
            "mood_tags": ["jcj", "jcj-en-linea"],
            "max_playtime": None,
            "company_tags": ["jcj-en-linea", "multijugador"],
            "priority_aspect": None,
        },
    )
    slugs = pick_slugs(body)
    assert len(slugs) == 3
    assert "stardew-valley" not in slugs
    for slug in slugs:
        assert GAME_TAGS[slug] & {"jcj", "jcj-en-linea"}


# ---------------------------------------------------------------------------
# Modo live: mismas expectativas contra la base de desarrollo local
# ---------------------------------------------------------------------------

GOLDEN_LIVE = os.environ.get("GOLDEN_LIVE") == "1"


@pytest.mark.skipif(not GOLDEN_LIVE, reason="Correr con GOLDEN_LIVE=1 contra la base local")
@pytest.mark.parametrize("case", CASES, ids=lambda case: f"live-{case.id}")
def test_golden_live(case: GoldenCase) -> None:
    """Las mismas expectativas, contra el catálogo real de la máquina local.

    Foto antes/después de una migración de ingesta. Las expectativas que
    involucren juegos ausentes del catálogo local se saltan (ver con -rs).
    NO es una métrica de calidad: para eso está el arnés de anotación.
    """
    with TestClient(app) as live_client, _live_db() as db:
        present = {
            slug
            for (slug,) in db.execute(
                select(Game.slug).where(Game.slug.in_([slug for slug, *_ in CATALOG]))
            )
        }
        involved = set(case.must_include) | set(case.must_exclude) | {
            slug for pair in case.rank_above for slug in pair
        }
        missing = involved - present
        if missing:
            pytest.skip(f"faltan en el catálogo local: {sorted(missing)}")

        body = suggest(
            live_client,
            build_payload(mood=case.mood, company=case.company, time=case.time, aspect=case.aspect),
        )
        live_case = GoldenCase(
            id=case.id, mood=case.mood, company=case.company, time=case.time,
            aspect=case.aspect, must_include=case.must_include,
            must_exclude=case.must_exclude, rank_above=case.rank_above,
            picks_must_have_tag=(), expect_relaxed=None,
        )
        assert_expectations(live_case, pick_slugs(body), body)


def _live_db():
    from app.db.database import SessionLocal

    return SessionLocal()
