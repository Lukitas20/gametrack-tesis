"""Golden tests del asistente "¿Qué jugamos hoy?".

Codifican expectativas canónicas del tipo "competir + en línea tiene que
rankear un shooter competitivo por encima de un simulador de granja" como
regresión automática: convierten el juicio "a ojo" sobre la calidad de las
sugerencias en asserts que corren con el resto de la suite.

Diseño — por qué un catálogo canónico y no la base de desarrollo:

* La base real está gitignoreada y diverge entre integrantes: un test que
  dependa de ella pasa en una máquina y falla en otra, que es exactamente lo
  contrario de una red de seguridad. El catálogo de acá es un *fixture
  canónico*: 17 juegos reales con los slugs de género y categoría tal como
  los produce ``steam_service`` al importar de Steam (``jcj``,
  ``cooperativo-en-linea``, ``un-jugador``...), congelado en el repo.
* Las expectativas son de sentido común verificable ("relajarme + solo no
  puede devolver Counter-Strike 2"), no de scores exactos: sobreviven a
  cambios de pesos, de vectorizador y de catálogo mientras el comportamiento
  siga siendo el correcto. Si un refactor las rompe, rompió algo que un
  usuario notaría.
* Para correr las mismas expectativas contra la base de desarrollo local
  (útil antes/después de una migración de ingesta):

      GOLDEN_LIVE=1 pytest tests/test_quiz_golden.py -k live -rs

  Ese modo salta las expectativas cuyos juegos no estén en el catálogo
  local, y queda excluido de CI (sin la variable, se skipea entero).

Los payloads se arman con los mismos mapeos que ``frontend/js/views/quiz.js``
(MOODS / COMPANY / TIME): si el frontend y estos tests divergen, el test está
probando otra cosa — mantenerlos a la par.
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
from app.services import steam_service

# ---------------------------------------------------------------------------
# Mapeos espejo del frontend (frontend/js/views/quiz.js)
# ---------------------------------------------------------------------------

TIME = {"tarde": 15, "finde": 40, None: None}

MOODS = {
    "historia": {"genres": ["rol", "aventura"], "mood_tags": []},
    "desafio": {"genres": ["accion", "estrategia"], "mood_tags": []},
    "relajarme": {"genres": ["casual", "simuladores"], "mood_tags": []},
    "competir": {
        "genres": ["accion", "deportes", "carreras"],
        "mood_tags": ["jcj", "jcj-en-linea"],
    },
}

COMPANY = {
    "solo": ["un-jugador"],
    "amigos": [
        "cooperativo",
        "cooperativo-en-linea",
        "pantalla-partida-compartida",
        "coop-a-pantalla-com-partida",
    ],
    "en-linea": [
        "jcj-en-linea",
        "cooperativo-en-linea",
        "multijugador",
        "multijugador-multiplataforma",
    ],
    None: [],
}


def build_payload(
    *,
    mood: str,
    company: str | None = None,
    time: str | None = None,
    aspect: str | None = None,
) -> dict:
    """Payload de ``POST /quiz/suggest`` tal como lo enviaría el frontend."""
    return {
        "genres": MOODS[mood]["genres"],
        "mood_tags": MOODS[mood]["mood_tags"],
        "max_playtime": TIME[time],
        "company_tags": COMPANY[company],
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

TAGS = {
    # Categorías de modalidad (las que filtran compañía y "competir").
    "un-jugador": "Un jugador",
    "multijugador": "Multijugador",
    "jcj": "JcJ",
    "jcj-en-linea": "JcJ en línea",
    "cooperativo": "Cooperativo",
    "cooperativo-en-linea": "Cooperativo en línea",
    "pantalla-partida-compartida": "Pantalla partida/compartida",
    "multijugador-multiplataforma": "Multijugador multiplataforma",
    # Ruido realista: metadata de plataforma que Steam pone en casi todo.
    # Está a propósito, para que el modelo de contenido conviva con ella.
    "logros-de-steam": "Logros de Steam",
    "cromos-de-steam": "Cromos de Steam",
    "steam-cloud": "Steam Cloud",
}

_NOISE = ["logros-de-steam", "cromos-de-steam", "steam-cloud"]

# (slug, nombre, géneros, etiquetas, horas típicas, avg_rating, ratings_count)
# Horas en None = juego-servicio sin "duración": el filtro no debe excluirlo.
CATALOG = [
    (
        "counter-strike-2", "Counter-Strike 2",
        ["accion"],
        ["multijugador", "jcj", "jcj-en-linea", "multijugador-multiplataforma", *_NOISE[2:]],
        None, 4.3, 5000,
        "Shooter táctico competitivo por equipos. Partidas clasificatorias, "
        "economía por rondas y torneos de esports.",
    ),
    (
        "dota-2", "Dota 2",
        ["accion", "estrategia"],
        ["multijugador", "jcj", "jcj-en-linea", *_NOISE[2:]],
        None, 4.0, 4000,
        "MOBA competitivo de cinco contra cinco. Estrategia por equipos, "
        "ranking y una escena profesional enorme.",
    ),
    (
        "apex-legends", "Apex Legends",
        ["accion"],
        ["multijugador", "jcj-en-linea", "cooperativo-en-linea",
         "multijugador-multiplataforma", *_NOISE],
        None, 3.9, 3500,
        "Battle royale por escuadrones con héroes y movilidad frenética. "
        "Competitivo en línea con temporadas clasificatorias.",
    ),
    (
        "age-of-empires-ii-definitive-edition", "Age of Empires II: Definitive Edition",
        ["estrategia"],
        ["un-jugador", "multijugador", "jcj-en-linea", "cooperativo-en-linea", *_NOISE],
        35, 4.7, 2000,
        "Estrategia en tiempo real histórica. Campañas para un jugador y "
        "partidas clasificatorias en línea.",
    ),
    (
        "forza-horizon-5", "Forza Horizon 5",
        ["carreras", "deportes"],
        ["un-jugador", "multijugador", "jcj-en-linea", *_NOISE],
        20, 4.4, 1800,
        "Carreras de mundo abierto en México. Pruebas, campeonatos y "
        "competencia en línea.",
    ),
    (
        "trackmania", "Trackmania",
        ["carreras"],
        ["un-jugador", "multijugador", "jcj-en-linea", *_NOISE[2:]],
        None, 4.1, 700,
        "Carreras arcade de tiempos: campañas para un jugador y ranking "
        "competitivo en línea contra los récords de todos.",
    ),
    (
        "the-witcher-3-wild-hunt", "The Witcher 3: Wild Hunt",
        ["rol"],
        ["un-jugador", *_NOISE],
        46, 4.8, 2500,
        "Rol de mundo abierto con una narrativa profunda. Decisiones con "
        "consecuencias y misiones escritas con oficio.",
    ),
    (
        "disco-elysium-the-final-cut", "Disco Elysium - The Final Cut",
        ["rol"],
        ["un-jugador", *_NOISE[:2]],
        25, 4.6, 800,
        "Rol de investigación puramente narrativo. Diálogos, personajes y "
        "una historia que se recuerda por años.",
    ),
    (
        "cyberpunk-2077", "Cyberpunk 2077",
        ["rol"],
        ["un-jugador", *_NOISE],
        30, 4.2, 3000,
        "Rol de acción en una megaciudad futurista. Historia cinematográfica "
        "y personajes memorables.",
    ),
    (
        "hades", "Hades",
        ["accion", "indie", "rol"],
        ["un-jugador", *_NOISE],
        22, 4.7, 1500,
        "Roguelike de acción exigente. Combate rápido, intentos que suman y "
        "una dificultad que invita a mejorar.",
    ),
    (
        "celeste", "Celeste",
        ["accion", "indie"],
        ["un-jugador", *_NOISE],
        8, 4.7, 900,
        "Plataformas de precisión, difícil y justo. Controles exactos y un "
        "desafío que crece pantalla a pantalla.",
    ),
    (
        "stardew-valley", "Stardew Valley",
        ["simuladores", "rol", "indie"],
        ["un-jugador", "multijugador", "cooperativo-en-linea", *_NOISE],
        55, 4.8, 3000,
        "Simulador de granja tranquilo y sin apuro. Cultivar, pescar y "
        "construir una vida en el pueblo, solo o en cooperativo.",
    ),
    (
        "powerwash-simulator", "PowerWash Simulator",
        ["simuladores", "casual"],
        ["un-jugador", "cooperativo-en-linea", *_NOISE],
        12, 4.5, 600,
        "Simulador relajante de limpieza a presión. Sin presión ni tiempo: "
        "satisfacción visual y calma.",
    ),
    (
        "unpacking", "Unpacking",
        ["casual", "indie"],
        ["un-jugador", *_NOISE[:2]],
        4, 4.3, 400,
        "Casual contemplativo: desempacar cajas y ordenar una vida. Corto, "
        "tranquilo y sin ninguna prisa.",
    ),
    (
        "a-short-hike", "A Short Hike",
        ["aventura", "casual", "indie"],
        ["un-jugador", *_NOISE[:2]],
        5, 4.8, 500,
        "Aventura breve y amable: explorar una montaña a tu ritmo, charlar "
        "con quien te cruces y llegar a la cima.",
    ),
    (
        "it-takes-two", "It Takes Two",
        ["accion", "aventura"],
        ["cooperativo", "cooperativo-en-linea", "pantalla-partida-compartida", *_NOISE],
        10, 4.8, 1200,
        "Aventura cooperativa obligatoria de a dos. Mecánicas nuevas en cada "
        "capítulo, pensada para jugar con alguien.",
    ),
    (
        "overcooked-2", "Overcooked! 2",
        ["casual", "indie", "simuladores"],
        ["multijugador", "cooperativo", "cooperativo-en-linea",
         "pantalla-partida-compartida", *_NOISE[:2]],
        15, 4.4, 1000,
        "Caos cooperativo de cocina para jugar con amigos en el sillón o en "
        "línea. Gritos garantizados.",
    ),
]

# Reseñas con salida ABSA ya materializada: estos tests prueban el ranking
# por aspecto del asistente, no el léxico (que tiene su propia suite en
# test_analytics.py), así que las filas de ReviewAspect se siembran directo.
# (slug, aspecto, score, sentimiento, evidencia)
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
    tags = {slug: Tag(slug=slug, name=name) for slug, name in TAGS.items()}
    db.add_all([*genres.values(), *tags.values()])

    games: dict[str, Game] = {}
    for slug, name, game_genres, game_tags, hours, avg, count, description in CATALOG:
        game = Game(
            slug=slug,
            name=name,
            description=description,
            playtime_hours=hours,
            avg_rating=avg,
            ratings_count=count,
        )
        game.genres = [genres[g] for g in game_genres]
        game.tags = [tags[t] for t in game_tags]
        db.add(game)
        games[slug] = game
    db.flush()

    for slug, aspect, score, sentiment, evidence in ASPECTS:
        review = Review(
            game_id=games[slug].id,
            content=evidence,
            language="es",
            source="steam",
            is_analyzed=True,
        )
        db.add(review)
        db.flush()
        db.add(
            ReviewAspect(
                review_id=review.id,
                game_id=games[slug].id,
                aspect=aspect,
                sentiment=sentiment,
                score=score,
                evidence=evidence,
            )
        )
    db.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def sin_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """El endpoint refresca los tres elegidos contra Steam: acá nunca.

    Preserva además el invariante de ``SteamUnavailable``: un test que
    tocara la red sería no determinístico, que es lo mismo que estos golden
    tests existen para impedir.
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
    # Slugs que tienen que estar en el top 3.
    must_include: tuple[str, ...] = ()
    # Slugs que no pueden aparecer en el top 3.
    must_exclude: tuple[str, ...] = ()
    # (a, b): b no puede aparecer sin a, ni por delante de a ("a domina a b").
    rank_above: tuple[tuple[str, str], ...] = ()
    # Todos los elegidos deben tener al menos una de estas etiquetas.
    picks_must_have_tag: tuple[str, ...] = ()
    # None = no se afirma nada; tupla = exactamente esos criterios relajados.
    expect_relaxed: tuple[str, ...] | None = ()


CASES = [
    # --- El caso testigo de la tesis: competir no es "multijugador" --------
    GoldenCase(
        id="competir-en-linea-rankea-cs2-sobre-stardew",
        mood="competir", company="en-linea",
        rank_above=(("counter-strike-2", "stardew-valley"),),
        must_exclude=("stardew-valley", "powerwash-simulator", "unpacking"),
        picks_must_have_tag=("jcj", "jcj-en-linea"),
    ),
    GoldenCase(
        id="competir-excluye-coop-tranquilos-aunque-sean-multijugador",
        mood="competir", company="en-linea",
        # Stardew y Overcooked son multijugador (pasarían "en línea") pero no
        # JcJ: el requisito del ánimo tiene que seguir filtrándolos.
        must_exclude=("stardew-valley", "overcooked-2", "it-takes-two"),
    ),
    GoldenCase(
        id="competir-solo-es-jcj-con-campania-no-cs2",
        mood="competir", company="solo",
        # CS2 y Dota no tienen "un-jugador": competir + solo debe caer en
        # títulos JcJ que sí se juegan solos (AoE2, Forza), no en CS2.
        must_exclude=("counter-strike-2", "dota-2", "apex-legends"),
        picks_must_have_tag=("jcj", "jcj-en-linea"),
    ),
    # --- Relajarse: la inversa del testigo ---------------------------------
    GoldenCase(
        id="relajarme-solo-jamas-devuelve-cs2",
        mood="relajarme", company="solo",
        must_exclude=("counter-strike-2", "dota-2", "apex-legends"),
        picks_must_have_tag=("un-jugador",),
    ),
    GoldenCase(
        id="relajarme-solo-prefiere-simuladores-tranquilos",
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
        id="desafio-solo-trae-accion-exigente-no-contemplativos",
        mood="desafio", company="solo",
        must_exclude=("unpacking", "powerwash-simulator", "a-short-hike"),
        picks_must_have_tag=("un-jugador",),
    ),
    # --- Duración ----------------------------------------------------------
    GoldenCase(
        id="una-tarde-solo-excluye-campanias-largas",
        mood="historia", company="solo", time="tarde",
        # Con ≤15 h quedan Celeste, Unpacking, A Short Hike y PowerWash: los
        # juegos largos no pueden colarse sin declarar la relajación.
        must_exclude=(
            "the-witcher-3-wild-hunt", "stardew-valley",
            "disco-elysium-the-final-cut", "cyberpunk-2077",
        ),
    ),
    GoldenCase(
        id="sin-duracion-conocida-no-se-excluye-por-tiempo",
        mood="competir", company="en-linea", time="tarde",
        # CS2/Dota/Apex no tienen horas cargadas (juegos-servicio): el
        # filtro de duración no puede descartarlos por falta de dato.
        # Comportamiento decidido: "sin dato" pasa el filtro; si algún día
        # se cambia a "sin dato no pasa", este test obliga a discutirlo.
        must_include=("counter-strike-2",),
    ),
    # --- Aspecto prioritario (ABSA) ----------------------------------------
    GoldenCase(
        id="prioridad-historia-ordena-por-absa",
        mood="historia", company="solo", aspect="historia",
        # Witcher (0.90) > Disco (0.85) > Cyberpunk (0.60); el resto no tiene
        # evidencia de historia y va detrás.
        must_include=(
            "the-witcher-3-wild-hunt",
            "disco-elysium-the-final-cut",
            "cyberpunk-2077",
        ),
        rank_above=(
            ("the-witcher-3-wild-hunt", "disco-elysium-the-final-cut"),
            ("disco-elysium-the-final-cut", "cyberpunk-2077"),
        ),
    ),
    GoldenCase(
        id="prioridad-optimizacion-castiga-al-que-crashea",
        mood="historia", company="solo", aspect="optimizacion",
        # Las reseñas dicen que Witcher anda bien (+0.2) y Cyberpunk mal
        # (-0.7): con esa prioridad, Cyberpunk no puede ir por delante.
        rank_above=(("the-witcher-3-wild-hunt", "cyberpunk-2077"),),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_golden(client: TestClient, case: GoldenCase) -> None:
    body = suggest(
        client,
        build_payload(mood=case.mood, company=case.company, time=case.time, aspect=case.aspect),
    )
    slugs = pick_slugs(body)
    assert_expectations(case, slugs, body)


def assert_expectations(case: GoldenCase, slugs: list[str], body: dict) -> None:
    # Contrato base: siempre tres sugerencias distintas.
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
            if slug in GAME_TAGS:
                tags = set(GAME_TAGS[slug])
                assert tags & allowed, (
                    f"{slug} no tiene ninguna de {sorted(allowed)} y fue sugerido igual"
                )
    if case.expect_relaxed is not None:
        assert body["relaxed"] == list(case.expect_relaxed), (
            f"relajación esperada {list(case.expect_relaxed)}, vino {body['relaxed']}: "
            "ningún criterio puede soltarse en silencio ni relajarse de más"
        )


# Índice slug -> etiquetas del catálogo canónico, para los asserts.
GAME_TAGS = {slug: set(tags) for slug, _, _, tags, *_ in CATALOG}


# ---------------------------------------------------------------------------
# Contratos que no entran en la tabla
# ---------------------------------------------------------------------------


def test_mismo_payload_mismas_sugerencias(client: TestClient) -> None:
    """Determinismo: dos corridas idénticas devuelven exactamente lo mismo.

    Sin esto, ninguna métrica offline es comparable entre corridas.
    """
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
    (duración primero, compañía después). El ánimo nunca se suelta acá."""
    payload = build_payload(mood="competir", company="amigos", time="tarde")
    body = suggest(client, payload)
    slugs = pick_slugs(body)

    assert body["relaxed"] == ["la duración", "con quién jugás"]
    # Aun relajando, el requisito del ánimo (JcJ) se mantiene siempre.
    for slug in slugs:
        tags = GAME_TAGS[slug]
        assert tags & {"jcj", "jcj-en-linea"}, (
            f"{slug} no es JcJ: la relajación soltó el ánimo, que es lo único innegociable"
        )


def test_fallback_a_popularidad_declara_que_solto_el_animo(client: TestClient) -> None:
    """Regresión: el perfil de contenido vacío tiene que declararse.

    Cuando ningún término del ánimo existe en el corpus, ``suggest_by_mood``
    devuelve vacío y el endpoint cae a popularidad. Eso está bien; lo que no
    puede pasar es que la respuesta diga que no se relajó nada mientras
    devuelve lo popular del catálogo.
    """
    payload = {
        "genres": ["genero-inexistente"],
        "mood_tags": [],
        "max_playtime": None,
        "company_tags": [],
        "priority_aspect": None,
    }
    body = suggest(client, payload)
    assert len(body["picks"]) == 3
    assert "el ánimo" in body["relaxed"], (
        "cayó a popularidad pura sin declararlo: degradación silenciosa"
    )


# ---------------------------------------------------------------------------
# Modo live: mismas expectativas contra la base de desarrollo local
# ---------------------------------------------------------------------------

GOLDEN_LIVE = os.environ.get("GOLDEN_LIVE") == "1"


@pytest.mark.skipif(not GOLDEN_LIVE, reason="Correr con GOLDEN_LIVE=1 contra la base local")
@pytest.mark.parametrize("case", CASES, ids=lambda case: f"live-{case.id}")
def test_golden_live(case: GoldenCase) -> None:
    """Las mismas expectativas, contra el catálogo real de la máquina local.

    Sirve como foto antes/después de una migración de ingesta. Cada
    expectativa que involucre un juego ausente del catálogo local se salta
    (reportado con -rs), en vez de fallar por un catálogo distinto.
    """
    with TestClient(app) as live_client, _live_db() as db:
        present = {
            slug for (slug,) in db.execute(
                select(Game.slug).where(
                    Game.slug.in_([slug for slug, *_ in CATALOG])
                )
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
            id=case.id,
            mood=case.mood,
            company=case.company,
            time=case.time,
            aspect=case.aspect,
            must_include=case.must_include,
            must_exclude=case.must_exclude,
            rank_above=case.rank_above,
            # Etiquetas y relajación dependen del catálogo local: en vivo
            # sólo se afirman inclusiones, exclusiones y dominancias.
            picks_must_have_tag=(),
            expect_relaxed=None,
        )
        assert_expectations(live_case, pick_slugs(body), body)


def _live_db():
    from app.db.database import SessionLocal

    return SessionLocal()
