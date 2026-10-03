"""Cruza perfiles autorizados sin entrenar un modelo distinto por grupo.

Los puntajes individuales se calculan localmente con el mismo motor personal.
La agregación es una regla de decisión explícita: en modo equilibrado pesa
la afinidad media (60 %) y la menor afinidad (40 %). Una valoración negativa
explícita reduce a la mitad ese índice. El modo promedio conserva la media.
Estos índices no son probabilidades ni métricas de confianza del modelo.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml.recommender import RecommenderEngine
from app.models import Game, Rating, User

GroupStrategy = Literal["balanced", "average"]

# Para grupos la modalidad es innegociable. "Multiplayer" a secas puede ser
# local; "split-screen" puede ser competitivo. Ninguno verifica online/coop.
GROUP_PLAY_MODES = {
    "amigos": frozenset({
        "cooperativo", "cooperativo-en-linea", "coop-a-pantalla-com-partida",
        "cooperativo-a-pantalla-compartida", "co-op", "online-co-op", "local-co-op",
    }),
    "en-linea": frozenset({
        "jcj-en-linea", "cooperativo-en-linea", "online-co-op", "online-pvp",
        "multijugador-masivo", "massively-multiplayer", "mmorpg",
    }),
}

GROUP_NOTICE = (
    "Cruzamos los perfiles de los participantes; los índices de afinidad no son probabilidades. "
    "Sin historial ni preferencias utilizamos un valor neutro, sin atribuir gustos. "
    "La modalidad se verifica con las etiquetas disponibles, pero la capacidad del grupo "
    "no está verificada: revisen el máximo de jugadores y los requisitos para jugar juntos."
)


def is_group_playable(game: Game, company: str | None) -> bool:
    return bool({tag.slug for tag in game.tags} & GROUP_PLAY_MODES.get(company, frozenset()))


def group_scores(
    db: Session, engine: RecommenderEngine, members: list[User], strategy: GroupStrategy,
) -> dict[int, dict]:
    """Devuelve evidencia mínima por juego, sin notas ni historial individual.

    ``members`` debe provenir de resolve_group_members, nunca de ids sin
    autorizar enviados por el cliente. Los juegos ya jugados siguen siendo
    candidatos: volver a jugar con amigos es una opción válida.
    """
    if not members or not engine.game_ids:
        return {}
    matrix, methods = [], []
    for member in members:
        scores, method = engine.personal_scores(
            member.id, preferred_genres=[genre.slug for genre in member.genres],
        )
        scores = np.asarray(scores, dtype=float)
        from app.services.play_service import learning_context
        learned=learning_context(db,member,engine)
        if learned and learned['personal']:
            scores=np.asarray([learned['scores'].get(gid,.5) for gid in engine.game_ids],dtype=float)
            method='experiencias_personales'
        if scores.shape != (len(engine.game_ids),):
            raise ValueError("Los puntajes personales no coinciden con el catálogo del motor")
        if method == "popularidad":
            # La popularidad general no prueba que a esta persona le guste.
            scores = np.full(len(engine.game_ids), 0.5)
            method = "sin_datos"
        matrix.append(np.clip(np.nan_to_num(scores, nan=0.5, posinf=1.0, neginf=0.0), 0.0, 1.0))
        methods.append(method)

    affinities = np.vstack(matrix)
    means, minimums = affinities.mean(axis=0), affinities.min(axis=0)
    aggregates = means.copy() if strategy == "average" else 0.6 * means + 0.4 * minimums
    if strategy == "balanced":
        disliked = set(db.scalars(select(Rating.game_id).where(
            Rating.user_id.in_([member.id for member in members]), Rating.score <= 2,
        )))
        for index, game_id in enumerate(engine.game_ids):
            if game_id in disliked:
                aggregates[index] *= 0.5

    return {
        game_id: {
            "score": round(float(aggregates[index]), 4),
            "mean_score": round(float(means[index]), 4),
            "min_score": round(float(minimums[index]), 4),
            "participants": [
                {"id": member.id, "username": member.username,
                 "score": round(float(affinities[row, index]), 4), "basis": methods[row]}
                for row, member in enumerate(members)
            ],
        }
        for index, game_id in enumerate(engine.game_ids)
    }
