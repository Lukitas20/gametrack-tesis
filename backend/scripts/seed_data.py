#!/usr/bin/env python
"""Puebla la base de datos de GameTrack con un dataset de demostración.

Genera un catálogo de videojuegos reales, usuarios de prueba con perfiles de
gusto diferenciados, y las interacciones (ratings, listas y reseñas en
español) que necesitan el motor de recomendación y el módulo NLP.

Uso:
    python scripts/seed_data.py                      # dataset local, 60 jugadores
    python scripts/seed_data.py --reset              # borra todo y vuelve a crear
    python scripts/seed_data.py --players 120        # más usuarios
    python scripts/seed_data.py --source rawg        # importa juegos desde RAWG
    python scripts/seed_data.py --seed 7             # otra semilla aleatoria

Las reseñas se insertan **sin analizar** (``is_analyzed = False``): es el
módulo NLP el que las procesa y completa el sentimiento y los aspectos. El
script deja en ``data/generated/reviews_ground_truth.json`` las etiquetas con
las que fueron generadas, para poder medir la precisión de ese módulo.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Permite ejecutar el script directamente desde backend/ sin instalar el paquete.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import DATA_DIR  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import SessionLocal, drop_db, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Aspect,
    Game,
    GameList,
    GameListItem,
    GameStatus,
    Genre,
    ListType,
    Rating,
    Review,
    ReviewAspect,
    Sentiment,
    Tag,
    User,
    UserPreference,
    UserRole,
)
from scripts.review_corpus import ASPECT_PHRASES, CLOSINGS, OPENINGS, TITLES  # noqa: E402

# ---------------------------------------------------------------------------
# Parámetros del dataset generado
# ---------------------------------------------------------------------------

DEFAULT_PLAYERS = 80
DEFAULT_SEED = 42
DEMO_PASSWORD = "demo1234"

MIN_RATINGS_PER_USER = 6
MAX_RATINGS_PER_USER = 24
REVIEW_PROBABILITY = 0.35
# Los juegos con pocas reseñas dejan sin datos al dashboard de desarrollador,
# así que se completan hasta este mínimo.
MIN_REVIEWS_PER_GAME = 6
HISTORY_DAYS = 540

GENRE_NAMES = {
    "accion": "Acción",
    "aventura": "Aventura",
    "rpg": "RPG",
    "indie": "Indie",
    "shooter": "Shooter",
    "plataformas": "Plataformas",
    "estrategia": "Estrategia",
    "simulacion": "Simulación",
    "puzzle": "Puzzle",
    "carreras": "Carreras",
    "deportes": "Deportes",
    "lucha": "Lucha",
    "cartas": "Cartas",
    "casual": "Casual",
    "terror": "Terror",
    "multijugador-masivo": "Multijugador masivo",
}

# Arquetipos de gusto. Generan grupos de usuarios con comportamiento similar,
# que es exactamente la estructura que el filtrado colaborativo debe encontrar.
TASTE_PROFILES: list[dict[str, Any]] = [
    {
        "name": "RPG narrativo",
        "genres": ["rpg", "aventura"],
        "tags": ["narrativo", "historia-rica", "mundo-abierto", "fantasia", "eleccion-moral"],
    },
    {
        "name": "Shooter competitivo",
        "genres": ["shooter", "accion"],
        "tags": ["fps", "competitivo", "multijugador", "esports", "primera-persona"],
    },
    {
        "name": "Indie de autor",
        "genres": ["indie", "plataformas", "puzzle"],
        "tags": ["pixel-art", "arte-hermoso", "banda-sonora", "emocional", "un-jugador"],
    },
    {
        "name": "Estrategia y gestión",
        "genres": ["estrategia", "simulacion"],
        "tags": ["gestion", "construccion", "complejo", "por-turnos", "sandbox"],
    },
    {
        "name": "Desafío y souls",
        "genres": ["accion", "rpg"],
        "tags": ["souls-like", "dificil", "atmosferico", "jefes", "combate-preciso"],
    },
    {
        "name": "Sandbox cooperativo",
        "genres": ["simulacion", "aventura", "indie"],
        "tags": ["sandbox", "cooperativo", "supervivencia", "crafteo", "exploracion"],
    },
    {
        "name": "Terror y atmósfera",
        "genres": ["terror", "accion", "aventura"],
        "tags": ["supervivencia", "atmosferico", "zombies", "tercera-persona", "misterio"],
    },
    {
        "name": "Casual y relax",
        "genres": ["casual", "simulacion", "carreras"],
        "tags": ["relajante", "coches", "facil-de-aprender", "multijugador", "granja"],
    },
]

FIRST_NAMES = [
    "Lucas", "Marco", "Sofía", "Martina", "Julián", "Camila", "Nicolás", "Valentina",
    "Matías", "Agustina", "Federico", "Lucía", "Tomás", "Brenda", "Gonzalo", "Micaela",
    "Ezequiel", "Rocío", "Ignacio", "Florencia", "Facundo", "Antonella", "Joaquín",
    "Delfina", "Santiago", "Pilar", "Bruno", "Malena", "Emiliano", "Josefina",
    "Ramiro", "Carolina", "Alejo", "Victoria", "Damián", "Guadalupe", "Iván", "Paula",
]

LAST_NAMES = [
    "Gómez", "Fernández", "Rodríguez", "López", "Martínez", "Pérez", "Álvarez",
    "Romero", "Sosa", "Torres", "Ruiz", "Ramírez", "Flores", "Benítez", "Acosta",
    "Medina", "Herrera", "Aguirre", "Pereyra", "Gutiérrez", "Molina", "Silva",
    "Castro", "Ortiz", "Núñez", "Vega", "Cabrera", "Rojas", "Ibarra", "Luna",
]

CUSTOM_LIST_TEMPLATES = [
    ("Para maratonear en vacaciones", "mundo-abierto"),
    ("Cortitos y al pie", "un-jugador"),
    ("Para jugar con amigos", "cooperativo"),
    ("Los que me faltan platinar", "dificil"),
    ("Joyas indie", "pixel-art"),
    ("Historias que me marcaron", "narrativo"),
    ("Para desconectar", "relajante"),
]

STUDIO_DEVELOPERS = [
    ("CD Projekt Red", "Ana", "Kowalski"),
    ("FromSoftware", "Kenji", "Tanaka"),
    ("Valve", "Peter", "Hall"),
    ("Rockstar Games", "Diana", "Moore"),
    ("Capcom", "Yuki", "Sato"),
]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return "".join(c if c.isalnum() else "-" for c in ascii_only).strip("-")


def humanize_tag(slug: str) -> str:
    return slug.replace("-", " ").capitalize()


def weighted_sample(
    rng: random.Random, population: list[Any], weights: list[float], k: int
) -> list[Any]:
    """Muestreo sin reposición proporcional a los pesos."""
    pool = [[item, max(w, 0.0)] for item, w in zip(population, weights)]
    chosen: list[Any] = []
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool)
        if total <= 0:
            break
        threshold = rng.uniform(0, total)
        accumulated = 0.0
        for index, (item, weight) in enumerate(pool):
            accumulated += weight
            if accumulated >= threshold:
                chosen.append(item)
                pool.pop(index)
                break
    return chosen


def random_past_datetime(rng: random.Random, max_days: int = HISTORY_DAYS) -> datetime:
    delta = timedelta(
        days=rng.randint(0, max_days),
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59),
    )
    return datetime.now(timezone.utc) - delta


def sentiment_from_score(score: float) -> Sentiment:
    if score >= 3.75:
        return Sentiment.POSITIVE
    if score <= 2.5:
        return Sentiment.NEGATIVE
    return Sentiment.NEUTRAL


# ---------------------------------------------------------------------------
# Carga del catálogo
# ---------------------------------------------------------------------------


def load_games_local() -> list[dict[str, Any]]:
    path = DATA_DIR / "games_seed.json"
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el dataset local en {path}")
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload["games"]


def load_games_rawg(pages: int) -> list[dict[str, Any]]:
    from app.services.rawg_client import fetch_games

    print(f"Importando juegos desde RAWG ({pages} página/s)...")
    return fetch_games(pages=pages)


def parse_released(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Creación de entidades
# ---------------------------------------------------------------------------


def create_taxonomy(
    db: Session, games_data: list[dict[str, Any]]
) -> tuple[dict[str, Genre], dict[str, Tag]]:
    """Crea los géneros y etiquetas que aparecen en el catálogo."""
    genre_slugs: list[str] = []
    tag_slugs: list[str] = []
    for game in games_data:
        for slug in game.get("genres", []):
            if slug not in genre_slugs:
                genre_slugs.append(slug)
        for slug in game.get("tags", []):
            if slug not in tag_slugs:
                tag_slugs.append(slug)

    genres = {
        slug: Genre(slug=slug, name=GENRE_NAMES.get(slug, humanize_tag(slug)))
        for slug in genre_slugs
    }
    tags = {slug: Tag(slug=slug, name=humanize_tag(slug)) for slug in tag_slugs}

    db.add_all(genres.values())
    db.add_all(tags.values())
    db.flush()
    return genres, tags


def create_games(
    db: Session,
    games_data: list[dict[str, Any]],
    genres: dict[str, Genre],
    tags: dict[str, Tag],
) -> list[Game]:
    games = []
    for data in games_data:
        game = Game(
            external_id=data.get("external_id"),
            slug=data["slug"],
            name=data["name"],
            description=data.get("description"),
            released=parse_released(data.get("released")),
            developer=data.get("developer"),
            publisher=data.get("publisher"),
            platforms=data.get("platforms", []),
            background_image=data.get("background_image"),
            metacritic=data.get("metacritic"),
            external_rating=data.get("external_rating"),
            playtime_hours=data.get("playtime_hours"),
        )
        game.genres = [genres[s] for s in data.get("genres", []) if s in genres]
        game.tags = [tags[s] for s in data.get("tags", []) if s in tags]
        games.append(game)

    db.add_all(games)
    db.flush()
    return games


def create_developers(db: Session, password_hash: str) -> list[User]:
    """Usuarios con rol desarrollador, uno por estudio destacado del catálogo."""
    users = []
    for index, (studio, first, last) in enumerate(STUDIO_DEVELOPERS):
        users.append(
            User(
                username=f"dev.{slugify(studio)}",
                email=f"dev{index + 1}@{slugify(studio)}.com",
                hashed_password=password_hash,
                full_name=f"{first} {last}",
                role=UserRole.DEVELOPER,
                studio=studio,
                bio=f"Analista de datos en {studio}.",
                created_at=datetime.now(timezone.utc) - timedelta(days=400 - index * 10),
            )
        )

    # Cuenta fija para la demostración.
    users.append(
        User(
            username="dev.demo",
            email="dev.demo@gametrack.app",
            hashed_password=password_hash,
            full_name="Cuenta Demo Desarrollador",
            role=UserRole.DEVELOPER,
            studio="CD Projekt Red",
            bio="Cuenta de demostración del panel de desarrollador.",
            created_at=datetime.now(timezone.utc) - timedelta(days=200),
        )
    )

    db.add_all(users)
    db.flush()
    return users


def build_player_profiles(rng: random.Random, count: int) -> list[dict[str, Any]]:
    """Arma los perfiles de gusto de los jugadores antes de persistirlos."""
    profiles = []
    used_usernames: set[str] = set()

    # Cuentas fijas para la demo. La primera lleva un historial abundante
    # garantizado; la segunda no tiene ninguna interacción, y es la que permite
    # mostrar el arranque en frío (recomendación por preferencias + popularidad).
    fixed = [
        ("jugador.demo", "Cuenta Demo Jugador", 0, False, 26),
        ("nuevo.demo", "Usuario Recién Registrado", 2, True, 0),
    ]

    for index in range(count):
        ratings_target: int | None = None
        if index < len(fixed):
            username, full_name, profile_index, is_cold_start, ratings_target = fixed[index]
        else:
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            base = f"{slugify(first)}.{slugify(last)}"
            username = base
            suffix = 2
            while username in used_usernames:
                username = f"{base}{suffix}"
                suffix += 1
            full_name = f"{first} {last}"
            profile_index = rng.randrange(len(TASTE_PROFILES))
            is_cold_start = False

        used_usernames.add(username)
        archetype = TASTE_PROFILES[profile_index]

        # Se varía levemente el arquetipo para que los grupos no sean idénticos.
        genres = list(archetype["genres"])
        tags = rng.sample(archetype["tags"], k=max(2, len(archetype["tags"]) - 1))
        if rng.random() < 0.35:
            genres.append(rng.choice(rng.choice(TASTE_PROFILES)["genres"]))

        profiles.append(
            {
                "username": username,
                "full_name": full_name,
                "archetype": archetype["name"],
                "genres": set(genres),
                "tags": set(tags),
                "is_cold_start": is_cold_start,
                "ratings_target": ratings_target,
            }
        )
    return profiles


def create_players(
    db: Session,
    profiles: list[dict[str, Any]],
    genres: dict[str, Genre],
    password_hash: str,
    rng: random.Random,
) -> list[User]:
    users = []
    for profile in profiles:
        user = User(
            username=profile["username"],
            email=f"{profile['username']}@gametrack.app",
            hashed_password=password_hash,
            full_name=profile["full_name"],
            role=UserRole.PLAYER,
            bio=f"Perfil de gusto: {profile['archetype']}.",
            created_at=random_past_datetime(rng),
        )
        user.preferences = [
            UserPreference(genre=genres[slug], weight=round(rng.uniform(0.6, 1.0), 2))
            for slug in profile["genres"]
            if slug in genres
        ]
        users.append(user)
        profile["user"] = user

    db.add_all(users)
    db.flush()
    return users


def game_affinity(profile: dict[str, Any], game: Game) -> float:
    """Afinidad [0, 1] entre el gusto declarado de un usuario y un juego."""
    game_genres = {g.slug for g in game.genres}
    game_tags = {t.slug for t in game.tags}
    genre_overlap = len(profile["genres"] & game_genres) / max(1, len(profile["genres"]))
    tag_overlap = len(profile["tags"] & game_tags) / max(1, len(profile["tags"]))
    return 0.65 * genre_overlap + 0.35 * tag_overlap


def create_ratings(
    db: Session,
    profiles: list[dict[str, Any]],
    games: list[Game],
    rng: random.Random,
) -> list[Rating]:
    """Genera la matriz de interacción usuario-ítem.

    El puntaje combina la afinidad del usuario con el juego y la calidad
    objetiva del título, más ruido gaussiano. Esa mezcla es la que produce
    grupos de usuarios con gustos parecidos sin volver los datos triviales.
    """
    ratings: list[Rating] = []

    for profile in profiles:
        if profile["is_cold_start"]:
            profile["rated"] = {}
            continue

        affinities = [game_affinity(profile, game) for game in games]
        # El término constante deja lugar a descubrimientos fuera del perfil.
        weights = [0.12 + affinity**1.6 for affinity in affinities]
        target = profile.get("ratings_target") or rng.randint(
            MIN_RATINGS_PER_USER, MAX_RATINGS_PER_USER
        )
        selected = weighted_sample(rng, games, weights, target)

        affinity_by_id = {game.id: affinity for game, affinity in zip(games, affinities)}
        rated: dict[int, Rating] = {}

        for game in selected:
            affinity = affinity_by_id[game.id]
            # Metacritic se concentra entre 50 y 95; se reescala a [0, 1] para
            # que la diferencia entre un juego mediocre y uno excelente pese
            # de verdad en el puntaje final.
            quality = min(1.0, max(0.0, ((game.metacritic or 75) - 50) / 45))
            base = 0.45 * affinity + 0.55 * quality
            score = 1.6 + 3.5 * base + rng.gauss(0, 0.42)
            score = min(5.0, max(1.0, round(score * 2) / 2))

            if score >= 4.5 and rng.random() < 0.25:
                status = GameStatus.PLAYING
            elif score <= 2.0 and rng.random() < 0.6:
                status = GameStatus.ABANDONED
            else:
                status = GameStatus.COMPLETED

            hours = (game.playtime_hours or 20) * rng.uniform(0.25, 1.7)
            hours *= 0.55 + score / 6
            if status is GameStatus.ABANDONED:
                hours *= 0.35

            rating = Rating(
                user_id=profile["user"].id,
                game_id=game.id,
                score=score,
                hours_played=round(max(0.5, hours), 1),
                status=status,
                created_at=random_past_datetime(rng),
            )
            ratings.append(rating)
            rated[game.id] = rating

        profile["rated"] = rated

    db.add_all(ratings)
    db.flush()
    return ratings


# ---------------------------------------------------------------------------
# Generación de reseñas
# ---------------------------------------------------------------------------


def pick_aspects(
    rng: random.Random, aspect_profile: dict[str, float]
) -> list[Aspect]:
    """Elige qué aspectos menciona una reseña.

    Los aspectos con valoración extrema (muy buena o muy mala) tienen más
    probabilidad de aparecer, porque son de los que la gente efectivamente
    habla. La historia se omite en juegos que no tienen narrativa.
    """
    candidates: list[Aspect] = []
    weights: list[float] = []
    for aspect in Aspect:
        value = aspect_profile.get(aspect.value, 0.5)
        if aspect is Aspect.STORY and value < 0.35:
            continue
        candidates.append(aspect)
        weights.append(0.35 + abs(value - 0.5) * 1.3)

    count = rng.randint(2, min(4, len(candidates)))
    return weighted_sample(rng, candidates, weights, count)


def build_review(
    rng: random.Random, aspect_profile: dict[str, float], score: float
) -> tuple[str, str, list[tuple[Aspect, Sentiment, str]], Sentiment]:
    """Redacta una reseña coherente con el puntaje y el perfil del juego.

    Returns:
        El título, el cuerpo, la lista de (aspecto, sentimiento, frase) con la
        que fue generada, y la polaridad que el texto realmente expresa.

        Esa última no siempre coincide con la que se deduce del puntaje: una
        reseña de 3 estrellas sobre un juego excelente puede elogiar todos sus
        aspectos. Como el módulo NLP lee texto y no puntajes, es contra esta
        etiqueta que corresponde evaluarlo.
    """
    overall = sentiment_from_score(score)
    user_lean = (score - 1) / 4  # [0, 1]
    # Sesgo propio de quien escribe, constante dentro de la reseña: hay gente
    # más benévola y más dura. Es lo que genera las minorías que discrepan del
    # consenso, incluso en los juegos mejor valorados.
    temperament = rng.gauss(0, 0.13)

    labels: list[tuple[Aspect, Sentiment, str]] = []
    sentences: list[str] = []

    for aspect in pick_aspects(rng, aspect_profile):
        perceived = aspect_profile.get(aspect.value, 0.5)
        # La opinión sobre cada aspecto mezcla la percepción general del juego
        # con el humor particular de quien escribe.
        value = 0.62 * perceived + 0.38 * user_lean + temperament + rng.gauss(0, 0.09)
        if value > 0.60:
            sentiment = Sentiment.POSITIVE
        elif value < 0.42:
            sentiment = Sentiment.NEGATIVE
        else:
            sentiment = Sentiment.NEUTRAL

        phrase = rng.choice(ASPECT_PHRASES[aspect][sentiment])
        sentences.append(phrase)
        labels.append((aspect, sentiment, phrase))

    rng.shuffle(sentences)
    body = " ".join([rng.choice(OPENINGS[overall]), *sentences, rng.choice(CLOSINGS[overall])])

    # Polaridad efectiva del texto: un voto por cada frase de aspecto, más dos
    # votos por la apertura y el cierre, que siguen el ánimo global del autor.
    polarity = {Sentiment.POSITIVE: 1, Sentiment.NEUTRAL: 0, Sentiment.NEGATIVE: -1}
    votes = [polarity[sentiment] for _aspect, sentiment, _phrase in labels]
    votes.extend([polarity[overall]] * 2)
    average = sum(votes) / len(votes)
    if average > 0.34:
        text_sentiment = Sentiment.POSITIVE
    elif average < -0.34:
        text_sentiment = Sentiment.NEGATIVE
    else:
        text_sentiment = Sentiment.NEUTRAL

    return rng.choice(TITLES[overall]), body, labels, text_sentiment


def create_reviews(
    db: Session,
    ratings: list[Rating],
    games_by_id: dict[int, dict[str, Any]],
    rng: random.Random,
) -> tuple[list[Review], list[dict[str, Any]]]:
    """Crea reseñas para una parte de los ratings, sin analizar todavía."""
    selected: list[Rating] = [r for r in ratings if rng.random() < REVIEW_PROBABILITY]

    # Se completa hasta un mínimo por juego para que el panel de desarrollador
    # tenga datos suficientes en todos los títulos.
    chosen_ids = {id(r) for r in selected}
    by_game: dict[int, list[Rating]] = {}
    for rating in ratings:
        by_game.setdefault(rating.game_id, []).append(rating)

    for game_id, game_ratings in by_game.items():
        current = sum(1 for r in game_ratings if id(r) in chosen_ids)
        if current >= MIN_REVIEWS_PER_GAME:
            continue
        remaining = [r for r in game_ratings if id(r) not in chosen_ids]
        rng.shuffle(remaining)
        for rating in remaining[: MIN_REVIEWS_PER_GAME - current]:
            selected.append(rating)
            chosen_ids.add(id(rating))

    reviews: list[Review] = []
    pending_truth: list[
        tuple[Review, list[tuple[Aspect, Sentiment, str]], float, Sentiment]
    ] = []

    for rating in selected:
        aspect_profile = games_by_id[rating.game_id].get("aspect_profile") or {}
        title, body, labels, text_sentiment = build_review(
            rng, aspect_profile, rating.score
        )

        # La reseña se escribe algún tiempo después de registrar el rating.
        created_at = rating.created_at + timedelta(
            days=rng.randint(0, 20), hours=rng.randint(0, 23)
        )
        created_at = min(created_at, datetime.now(timezone.utc))

        review = Review(
            user_id=rating.user_id,
            game_id=rating.game_id,
            title=title,
            content=body,
            language="es",
            is_recommended=rating.score >= 3.5,
            hours_at_review=rating.hours_played,
            helpful_count=rng.randint(0, 180),
            is_analyzed=False,
            created_at=created_at,
        )
        reviews.append(review)
        pending_truth.append((review, labels, rating.score, text_sentiment))

    db.add_all(reviews)
    db.flush()  # asigna los ids que necesita el ground truth

    ground_truth = [
        {
            "review_id": review.id,
            "user_id": review.user_id,
            "game_id": review.game_id,
            "score": score,
            # Polaridad que expresa el texto: la etiqueta contra la que se
            # evalúa el módulo NLP.
            "sentimiento_texto": text_sentiment.value,
            # Banda derivada del puntaje del usuario. Se conserva para poder
            # medir cuánto coincide lo que alguien escribe con lo que puntúa.
            "sentimiento_por_puntaje": sentiment_from_score(score).value,
            "aspectos": [
                {"aspecto": aspect.value, "sentimiento": sentiment.value, "frase": phrase}
                for aspect, sentiment, phrase in labels
            ],
        }
        for review, labels, score, text_sentiment in pending_truth
    ]
    return reviews, ground_truth


# ---------------------------------------------------------------------------
# Listas
# ---------------------------------------------------------------------------


def create_lists(
    db: Session,
    profiles: list[dict[str, Any]],
    games: list[Game],
    rng: random.Random,
) -> list[GameList]:
    lists: list[GameList] = []

    for profile in profiles:
        user: User = profile["user"]
        rated: dict[int, Rating] = profile.get("rated", {})

        if profile["is_cold_start"]:
            # Un usuario recién registrado tiene las listas del sistema creadas
            # pero vacías. Dejarlas con contenido le daría al recomendador una
            # señal de comportamiento que justamente queremos que no exista.
            for list_type, name, description in (
                (ListType.FAVORITES, "Favoritos", "Mis juegos preferidos."),
                (ListType.PLAYING, "Jugando", "Lo que estoy jugando ahora."),
                (ListType.BACKLOG, "Pendientes", "Juegos que quiero jugar."),
            ):
                lists.append(
                    GameList(
                        user_id=user.id,
                        name=name,
                        description=description,
                        list_type=list_type,
                        created_at=user.created_at,
                    )
                )
            continue

        favorites = sorted(
            (r for r in rated.values() if r.score >= 4),
            key=lambda r: r.score,
            reverse=True,
        )[: rng.randint(4, 8)]
        playing = [r for r in rated.values() if r.status is GameStatus.PLAYING]

        # Los pendientes salen de juegos todavía no valorados, ordenados por afinidad.
        unrated = [g for g in games if g.id not in rated]
        affinities = [game_affinity(profile, g) for g in unrated]
        backlog = weighted_sample(
            rng, unrated, [0.1 + a**1.6 for a in affinities], rng.randint(3, 9)
        )

        definitions = [
            (ListType.FAVORITES, "Favoritos", "Mis juegos preferidos.",
             [r.game_id for r in favorites]),
            (ListType.PLAYING, "Jugando", "Lo que estoy jugando ahora.",
             [r.game_id for r in playing]),
            (ListType.BACKLOG, "Pendientes", "Juegos que quiero jugar.",
             [g.id for g in backlog]),
        ]

        if rng.random() < 0.3:
            name, tag_slug = rng.choice(CUSTOM_LIST_TEMPLATES)
            matching = [g for g in games if any(t.slug == tag_slug for t in g.tags)]
            rng.shuffle(matching)
            if matching:
                definitions.append(
                    (ListType.CUSTOM, name, "Lista armada por el usuario.",
                     [g.id for g in matching[: rng.randint(3, 6)]])
                )

        for list_type, name, description, game_ids in definitions:
            game_list = GameList(
                user_id=user.id,
                name=name,
                description=description,
                list_type=list_type,
                is_public=rng.random() < 0.8,
                created_at=user.created_at,
            )
            game_list.items = [
                GameListItem(game_id=game_id, position=position)
                for position, game_id in enumerate(game_ids)
            ]
            lists.append(game_list)

    db.add_all(lists)
    db.flush()
    return lists


# ---------------------------------------------------------------------------
# Agregados
# ---------------------------------------------------------------------------


def update_game_aggregates(db: Session) -> None:
    """Recalcula los contadores desnormalizados de cada juego."""
    rating_stats = db.execute(
        select(Rating.game_id, func.avg(Rating.score), func.count(Rating.id)).group_by(
            Rating.game_id
        )
    ).all()
    review_stats = db.execute(
        select(Review.game_id, func.count(Review.id)).group_by(Review.game_id)
    ).all()

    averages = {game_id: (avg, count) for game_id, avg, count in rating_stats}
    review_counts = dict(review_stats)

    for game in db.scalars(select(Game)):
        avg, count = averages.get(game.id, (0.0, 0))
        game.avg_rating = round(float(avg or 0.0), 2)
        game.ratings_count = int(count)
        game.reviews_count = int(review_counts.get(game.id, 0))


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Puebla la base de datos de GameTrack.")
    parser.add_argument(
        "--reset", action="store_true", help="Elimina todas las tablas antes de sembrar."
    )
    parser.add_argument(
        "--source", choices=["local", "rawg"], default="local",
        help="Origen del catálogo de juegos (por defecto: dataset local curado).",
    )
    parser.add_argument(
        "--rawg-pages", type=int, default=2, help="Páginas a importar desde RAWG."
    )
    parser.add_argument(
        "--players", type=int, default=DEFAULT_PLAYERS, help="Cantidad de jugadores."
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="Semilla del generador aleatorio."
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rng = random.Random(args.seed)

    if args.reset:
        print("Eliminando el esquema existente...")
        drop_db()
    init_db()

    with SessionLocal() as db:
        existing = db.scalar(select(func.count(User.id)))
        if existing:
            print(
                f"La base ya tiene {existing} usuarios. Ejecutá con --reset para "
                "regenerarla desde cero."
            )
            return 1

        games_data = (
            load_games_rawg(args.rawg_pages)
            if args.source == "rawg"
            else load_games_local()
        )
        if not games_data:
            print("No se obtuvo ningún juego. Abortando.")
            return 1

        print(f"Creando catálogo ({len(games_data)} juegos)...")
        genres, tags = create_taxonomy(db, games_data)
        games = create_games(db, games_data, genres, tags)
        games_by_id = {
            game.id: data for game, data in zip(games, games_data)
        }

        # bcrypt es costoso a propósito; como todas las cuentas de prueba
        # comparten contraseña, alcanza con calcular el hash una sola vez.
        print("Creando usuarios...")
        password_hash = hash_password(DEMO_PASSWORD)
        developers = create_developers(db, password_hash)
        profiles = build_player_profiles(rng, args.players)
        players = create_players(db, profiles, genres, password_hash, rng)

        print("Generando interacciones...")
        ratings = create_ratings(db, profiles, games, rng)

        print("Generando reseñas en español...")
        reviews, ground_truth = create_reviews(db, ratings, games_by_id, rng)

        print("Armando listas...")
        lists = create_lists(db, profiles, games, rng)

        update_game_aggregates(db)
        db.commit()

        summary = {
            "juegos": len(games),
            "generos": len(genres),
            "etiquetas": len(tags),
            "jugadores": len(players),
            "desarrolladores": len(developers),
            "ratings": len(ratings),
            "resenas": len(reviews),
            "listas": len(lists),
        }

    output = DATA_DIR / "generated" / "reviews_ground_truth.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(ground_truth, handle, ensure_ascii=False, indent=2)

    print("\nBase de datos poblada:")
    for key, value in summary.items():
        print(f"  {key:>16}: {value}")
    print(f"\n  Ground truth ABSA: {output}")
    print("\nCuentas de demostración (contraseña: " + DEMO_PASSWORD + "):")
    print("  jugador.demo  · jugador con historial completo")
    print("  nuevo.demo    · jugador sin ratings, para probar el cold start")
    print("  dev.demo      · desarrollador (estudio CD Projekt Red)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
