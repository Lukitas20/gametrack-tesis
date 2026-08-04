"""Motor de recomendación híbrido.

Combina tres estrategias complementarias:

- **Basada en contenido** — embeddings semánticos (modelo preentrenado
  multilingüe) sobre géneros, etiquetas, desarrollador y descripción de cada
  juego, con similitud coseno. Funciona desde la primera valoración y explica
  bien sus resultados, pero encierra al usuario en lo que ya conoce.
- **Colaborativa** — filtrado ítem-ítem sobre la matriz usuario-ítem centrada
  por usuario. Descubre afinidades que el contenido no captura, pero necesita
  historial. Se eligió ítem-ítem sobre usuario-usuario porque las similitudes
  entre juegos son mucho más estables que entre personas cuando el catálogo
  es chico y los usuarios entran y salen.
- **Popularidad** — media bayesiana. Es el piso: responde siempre, incluso
  para un usuario del que no se sabe absolutamente nada.

La estrategia se elige según cuánto historial tenga el usuario, de modo que
el arranque en frío degrada de forma gradual en lugar de fallar.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Game, Rating, RecommendationSource, User

# Vecinos considerados al predecir con filtrado colaborativo.
NEIGHBOURS = 20
# Castigo por incertidumbre en la popularidad: se resta UNCERTAINTY_WEIGHT /
# sqrt(evidencia). Con dos reseñas cae ~0,7 puntos; con cien, ~0,1.
UNCERTAINTY_WEIGHT = 1.0
# Modelo de embeddings para la similitud de contenido: multilingüe (cubre el
# catálogo en español) y liviano, apto para correr en CPU sin GPU.
CONTENT_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def _content_model() -> SentenceTransformer:
    """Carga el modelo una sola vez por proceso: instanciarlo es lo costoso,
    no codificar texto con él."""
    return SentenceTransformer(CONTENT_MODEL_NAME)


@dataclass(frozen=True)
class Recommendation:
    game_id: int
    score: float
    source: RecommendationSource
    reason: str
    components: dict[str, float] = field(default_factory=dict)


def _normalize(values: np.ndarray) -> np.ndarray:
    """Lleva un vector de puntajes a [0, 1] para poder combinar estrategias.

    Sin esto no tendría sentido sumar una similitud coseno (0 a 1) con una
    predicción de rating (1 a 5).
    """
    if values.size == 0:
        return values
    low, high = float(values.min()), float(values.max())
    if high - low < 1e-9:
        return np.full_like(values, 0.5)
    return (values - low) / (high - low)


class RecommenderEngine:
    """Modelos entrenados sobre una foto del catálogo y las interacciones.

    Construir el motor recorre toda la base, así que se cachea a nivel de
    módulo y se reconstruye sólo cuando los datos cambian (ver ``get_engine``).
    """

    def __init__(self, db: Session) -> None:
        games = list(db.scalars(select(Game).order_by(Game.id)))
        self.game_ids: list[int] = [game.id for game in games]
        self.game_names: dict[int, str] = {game.id: game.name for game in games}
        self._game_index: dict[int, int] = {
            game_id: index for index, game_id in enumerate(self.game_ids)
        }

        self._build_content_model(games)
        self._build_collaborative_model(db)
        self._build_popularity_model(games)

    # -- Contenido ---------------------------------------------------------

    def _build_content_model(self, games: list[Game]) -> None:
        corpus = [game.content_soup for game in games]
        model = _content_model()
        embeddings = (
            model.encode(corpus, normalize_embeddings=True, show_progress_bar=False)
            if corpus
            else np.zeros((0, model.get_sentence_embedding_dimension()))
        )
        self._embeddings = np.asarray(embeddings)
        self.content_similarity = cosine_similarity(self._embeddings)
        np.fill_diagonal(self.content_similarity, 0.0)

    # -- Colaborativo ------------------------------------------------------

    def _build_collaborative_model(self, db: Session) -> None:
        rows = db.execute(select(Rating.user_id, Rating.game_id, Rating.score)).all()
        self.user_ids: list[int] = sorted({user_id for user_id, _, _ in rows})
        self._user_index = {user_id: i for i, user_id in enumerate(self.user_ids)}

        shape = (len(self.user_ids), len(self.game_ids))
        self.matrix = np.zeros(shape, dtype=float)
        self._rated_mask = np.zeros(shape, dtype=bool)

        for user_id, game_id, score in rows:
            if game_id not in self._game_index:
                continue
            row, column = self._user_index[user_id], self._game_index[game_id]
            self.matrix[row, column] = score
            self._rated_mask[row, column] = True

        counts = self._rated_mask.sum(axis=1)
        totals = self.matrix.sum(axis=1)
        # Media de cada usuario sobre lo que efectivamente valoró.
        self.user_means = np.divide(
            totals, counts, out=np.full(len(self.user_ids), 3.0), where=counts > 0
        )

        # Centrar por usuario neutraliza que unos puntúen alto y otros bajo:
        # lo que importa es cuánto se aparta cada nota de su propia media.
        centered = np.where(
            self._rated_mask, self.matrix - self.user_means[:, np.newaxis], 0.0
        )
        self._centered = centered

        if shape[0] == 0:
            self.item_similarity = np.zeros((shape[1], shape[1]))
        else:
            self.item_similarity = cosine_similarity(centered.T)
            np.fill_diagonal(self.item_similarity, 0.0)

    # -- Popularidad -------------------------------------------------------

    def _build_popularity_model(self, games: list[Game]) -> None:
        averages = np.array([game.avg_rating for game in games], dtype=float)
        counts = np.array([game.ratings_count for game in games], dtype=float)

        rated = counts > 0
        global_mean = float(averages[rated].mean()) if rated.any() else 3.0

        # Un juego con dos notas de 5 no debe superar a uno con doscientas
        # notas de 4,6. Achicar hacia la media global (media bayesiana) no
        # alcanza acá: buena parte de la evidencia real viene de reseñas de
        # Steam agregadas como "recomendado/no recomendado", que se agrupan
        # cerca del techo (la media global termina siendo ~4,9), así que
        # "achicar hacia la media" apenas mueve a un juego con pocas reseñas
        # perfectas. En cambio, castigar directamente por incertidumbre
        # (1/sqrt(evidencia)) sí discrimina: cae fuerte con poca evidencia y
        # cada vez menos a medida que se acumulan reseñas reales.
        self.popularity = averages - UNCERTAINTY_WEIGHT / np.sqrt(np.maximum(counts, 1.0))
        self.global_mean = global_mean

    # -- Consultas ---------------------------------------------------------

    def similar_games(self, game_id: int, limit: int = 10) -> list[Recommendation]:
        """Juegos parecidos por contenido a uno dado."""
        if game_id not in self._game_index:
            return []
        index = self._game_index[game_id]
        similarities = self.content_similarity[index]
        order = np.argsort(similarities)[::-1][:limit]
        return [
            Recommendation(
                game_id=self.game_ids[i],
                score=round(float(similarities[i]), 4),
                source=RecommendationSource.CONTENT,
                reason=f"Se parece a {self.game_names[game_id]}",
                components={"contenido": round(float(similarities[i]), 4)},
            )
            for i in order
            if similarities[i] > 0
        ]

    def _content_scores_from_history(self, user_id: int) -> np.ndarray | None:
        """Perfil de contenido a partir de los juegos que el usuario valoró alto."""
        if user_id not in self._user_index:
            return None
        row = self._user_index[user_id]
        rated = np.flatnonzero(self._rated_mask[row])
        if rated.size == 0:
            return None

        threshold = max(3.5, float(self.user_means[row]))
        liked = [i for i in rated if self.matrix[row, i] >= threshold]
        if not liked:
            # Nadie le gustó nada lo suficiente: se usa todo el historial
            # ponderado por la nota, para no quedarse sin señal.
            liked = list(rated)

        weights = np.array([self.matrix[row, i] for i in liked], dtype=float)
        profile = (self._embeddings[liked] * weights[:, np.newaxis]).sum(axis=0)
        norm = np.linalg.norm(profile)
        if norm < 1e-9:
            return None
        return cosine_similarity((profile / norm).reshape(1, -1), self._embeddings).ravel()

    def _content_scores_from_preferences(self, genre_slugs: list[str]) -> np.ndarray | None:
        """Perfil de contenido a partir de los géneros elegidos en el onboarding.

        Los slugs se repiten tres veces para reproducir la ponderación que
        ``Game.content_soup`` le da a los géneros dentro del corpus.
        """
        if not genre_slugs:
            return None
        pseudo_document = " ".join(slug for slug in genre_slugs for _ in range(3))
        vector = _content_model().encode(
            [pseudo_document], normalize_embeddings=True, show_progress_bar=False
        )
        return cosine_similarity(vector, self._embeddings).ravel()

    def _collaborative_scores(self, user_id: int) -> np.ndarray | None:
        """Predice la nota de cada juego con filtrado colaborativo ítem-ítem."""
        if user_id not in self._user_index:
            return None
        row = self._user_index[user_id]
        rated = np.flatnonzero(self._rated_mask[row])
        if rated.size == 0:
            return None

        # Si ninguna nota se aparta de la media del propio usuario no hay nada
        # que propagar a los vecinos: todas las predicciones saldrían iguales.
        # Pasa con una sola valoración, o cuando alguien puntuó todo igual.
        # Devolver None acá hace que el caso degrade a popularidad en lugar de
        # a un orden arbitrario entre predicciones empatadas.
        if float(np.abs(self._centered[row, rated]).max()) < 1e-9:
            return None

        predictions = np.full(len(self.game_ids), np.nan)
        for target in range(len(self.game_ids)):
            if self._rated_mask[row, target]:
                continue
            similarities = self.item_similarity[target, rated]
            positive = similarities > 0
            if not positive.any():
                continue

            neighbours = rated[positive]
            weights = similarities[positive]
            if weights.size > NEIGHBOURS:
                top = np.argsort(weights)[::-1][:NEIGHBOURS]
                neighbours, weights = neighbours[top], weights[top]

            denominator = np.abs(weights).sum()
            if denominator < 1e-9:
                continue
            deviation = float((weights * self._centered[row, neighbours]).sum() / denominator)
            predictions[target] = self.user_means[row] + deviation

        if np.all(np.isnan(predictions)):
            return None
        # Los juegos sin vecinos útiles caen a la media del usuario.
        return np.where(np.isnan(predictions), self.user_means[row], predictions)

    def _best_content_match(self, user_id: int, target_index: int) -> str | None:
        """Juego ya valorado que más se parece al recomendado (explicabilidad)."""
        if user_id not in self._user_index:
            return None
        row = self._user_index[user_id]
        rated = np.flatnonzero(self._rated_mask[row])
        if rated.size == 0:
            return None
        similarities = self.content_similarity[target_index, rated]
        if similarities.max() <= 0:
            return None
        return self.game_names[self.game_ids[rated[int(np.argmax(similarities))]]]

    # -- Recomendación -----------------------------------------------------

    def recommend(
        self,
        user_id: int | None,
        preferred_genres: list[str] | None = None,
        limit: int = 10,
        strategy: str = "auto",
        exclude: set[int] | None = None,
    ) -> list[Recommendation]:
        """Devuelve los mejores juegos para un usuario.

        Args:
            strategy: ``auto`` elige según el historial disponible. Forzar
                ``contenido``, ``colaborativo``, ``hibrido`` o ``popularidad``
                permite comparar los enfoques entre sí.
        """
        excluded = set(exclude or set())
        if user_id is not None and user_id in self._user_index:
            row = self._user_index[user_id]
            excluded.update(
                self.game_ids[i] for i in np.flatnonzero(self._rated_mask[row])
            )

        content = self._content_scores_from_history(user_id) if user_id else None
        if content is None:
            content = self._content_scores_from_preferences(preferred_genres or [])
        collaborative = self._collaborative_scores(user_id) if user_id else None

        popularity = self.popularity
        history_size = (
            int(self._rated_mask[self._user_index[user_id]].sum())
            if user_id in self._user_index
            else 0
        )

        chosen = strategy
        if strategy == "auto":
            if collaborative is not None and history_size >= settings.REC_COLD_START_THRESHOLD:
                chosen = "hibrido"
            elif content is not None:
                chosen = "contenido"
            else:
                chosen = "popularidad"

        content_weight = settings.REC_CONTENT_WEIGHT
        collab_weight = settings.REC_COLLAB_WEIGHT
        total_weight = content_weight + collab_weight
        content_weight, collab_weight = (
            content_weight / total_weight,
            collab_weight / total_weight,
        )

        components: dict[str, np.ndarray] = {}
        if chosen == "hibrido" and content is not None and collaborative is not None:
            combined = content_weight * _normalize(content) + collab_weight * _normalize(
                collaborative
            )
            components = {"contenido": content, "colaborativo": collaborative}
            source = RecommendationSource.HYBRID
        elif chosen == "colaborativo" and collaborative is not None:
            combined = _normalize(collaborative)
            components = {"colaborativo": collaborative}
            source = RecommendationSource.COLLABORATIVE
        elif chosen == "contenido" and content is not None:
            # Un toque de popularidad desempata entre juegos igual de afines y
            # evita recomendar títulos afines pero muy mal valorados.
            combined = 0.8 * _normalize(content) + 0.2 * _normalize(popularity)
            components = {"contenido": content, "popularidad": popularity}
            source = RecommendationSource.CONTENT
        else:
            combined = _normalize(popularity)
            components = {"popularidad": popularity}
            source = RecommendationSource.POPULARITY

        order = np.argsort(combined)[::-1]
        results: list[Recommendation] = []
        for index in order:
            game_id = self.game_ids[index]
            if game_id in excluded:
                continue

            results.append(
                Recommendation(
                    game_id=game_id,
                    score=round(float(combined[index]), 4),
                    source=source,
                    reason=self._build_reason(source, user_id, index, components),
                    components={
                        name: round(float(values[index]), 4)
                        for name, values in components.items()
                    },
                )
            )
            if len(results) >= limit:
                break
        return results

    def _build_reason(
        self,
        source: RecommendationSource,
        user_id: int | None,
        index: int,
        components: dict[str, np.ndarray],
    ) -> str:
        if source is RecommendationSource.POPULARITY:
            return "Entre los mejor valorados del catálogo"

        if source is RecommendationSource.COLLABORATIVE:
            return "A jugadores con un historial parecido al tuyo les gustó"

        similar = self._best_content_match(user_id, index) if user_id else None
        if source is RecommendationSource.HYBRID:
            if similar:
                return f"Se parece a {similar} y gustó a jugadores como vos"
            return "Coincide con tus gustos y con los de jugadores parecidos"

        if similar:
            return f"Se parece a {similar}, que valoraste bien"
        return "Coincide con los géneros que elegiste"


# ---------------------------------------------------------------------------
# Caché del motor
# ---------------------------------------------------------------------------

_engine: RecommenderEngine | None = None
_fingerprint: tuple[int, int, int] | None = None
_lock = threading.Lock()


def _current_fingerprint(db: Session) -> tuple[int, int, int]:
    """Huella barata de los datos: cambia cuando hay que reentrenar."""
    return (
        db.scalar(select(func.count(Game.id))) or 0,
        db.scalar(select(func.count(Rating.id))) or 0,
        db.scalar(select(func.max(Rating.id))) or 0,
    )


def get_engine(db: Session, force_rebuild: bool = False) -> RecommenderEngine:
    """Devuelve el motor entrenado, reconstruyéndolo si los datos cambiaron."""
    global _engine, _fingerprint

    fingerprint = _current_fingerprint(db)
    with _lock:
        if force_rebuild or _engine is None or _fingerprint != fingerprint:
            _engine = RecommenderEngine(db)
            _fingerprint = fingerprint
        return _engine


def invalidate_engine() -> None:
    """Fuerza el reentrenamiento en la próxima consulta."""
    global _engine, _fingerprint
    with _lock:
        _engine = None
        _fingerprint = None


def recommend_for_user(
    db: Session, user: User, limit: int | None = None, strategy: str = "auto"
) -> list[Recommendation]:
    """Recomendaciones para un usuario, resolviendo sus preferencias."""
    engine = get_engine(db)
    genre_slugs = [preference.genre.slug for preference in user.preferences]
    return engine.recommend(
        user_id=user.id,
        preferred_genres=genre_slugs,
        limit=limit or settings.REC_DEFAULT_LIMIT,
        strategy=strategy,
    )
