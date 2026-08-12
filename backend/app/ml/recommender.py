"""Motor de recomendación híbrido.

Combina tres estrategias complementarias:

- **Basada en contenido** — TF-IDF sobre las etiquetas comunitarias de cada
  juego ponderadas por votos (vía SteamSpy) más sus géneros, con similitud
  coseno. La frecuencia del término es la evidencia (proporción de votos
  dentro del juego) y el IDF castiga solo lo ubicuo ("Indie", "Action") y
  premia lo que discrimina ("Souls-like", "Farming Sim"). Funciona desde la
  primera valoración y explica bien sus resultados, pero encierra al usuario
  en lo que ya conoce.
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

import numpy as np
from scipy.sparse import csr_matrix, lil_matrix
from sklearn.feature_extraction.text import TfidfTransformer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Game, Rating, RecommendationSource, Tag, User, game_tags

# Vecinos considerados al predecir con filtrado colaborativo.
NEIGHBOURS = 20
# Castigo por incertidumbre en la popularidad: se resta UNCERTAINTY_WEIGHT /
# sqrt(evidencia). Con dos reseñas cae ~0,7 puntos; con cien, ~0,1.
UNCERTAINTY_WEIGHT = 1.0
# TF fijo de un género en el corpus de contenido. Comparable al sqrt de la
# proporción de votos de una etiqueta destacada (sqrt(0.12) ~ 0.35): el
# género acompaña, no manda. Es la señal de respaldo de los juegos que
# todavía no pasaron por la ingesta de etiquetas de SteamSpy.
GENRE_TF = 0.4


@dataclass(frozen=True)
class EngineConfig:
    """Hiperparámetros del motor, con la producción como default.

    Existe para el arnés de evaluación (``scripts/eval_arnes.py``): cada
    variante de la tabla comparativa de la tesis es una instancia de esto
    corriendo EL MISMO código que sirve al usuario — no una reimplementación
    paralela que pueda divergir de lo evaluado. Construir el motor sin
    argumentos (``RecommenderEngine(db)``, como hace ``get_engine``) es
    idéntico a antes de que esta clase existiera.
    """

    # Corpus de contenido
    use_community_tags: bool = True   # etiquetas comunitarias en el corpus
    use_vote_weights: bool = True     # TF = sqrt(prop. de votos); False = binario
    genre_tf: float = GENRE_TF        # 0.0 = géneros fuera del corpus
    # Combinación del asistente (suggest_by_mood)
    w_content: float = 0.6
    w_quality: float = 0.15
    w_reach: float = 0.25


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

    def __init__(self, db: Session, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        # Universo recomendable: un juego participa si tiene RASGOS con los
        # que compararlo — géneros o etiquetas — sin importar cómo llegaron:
        # ficha rica de la tienda (steam_synced_at), ingesta de SteamSpy
        # (nivel 1) o dataset curado. Las fichas peladas (solo AppID y
        # nombre) quedan afuera: siguen en el catálogo y el buscador, pero
        # sin rasgos no hay nada que recomendar de ellas. Este criterio ES
        # la política de catálogo del motor: "recomendable" = "con rasgos".
        games = list(
            db.scalars(
                select(Game)
                .where(
                    or_(
                        Game.steam_app_id.is_(None),
                        Game.steam_synced_at.is_not(None),
                        Game.genres.any(),
                        Game.tags.any(),
                    )
                )
                .order_by(Game.id)
            )
        )
        self.game_ids: list[int] = [game.id for game in games]
        self.game_names: dict[int, str] = {game.id: game.name for game in games}
        self._game_index: dict[int, int] = {
            game_id: index for index, game_id in enumerate(self.game_ids)
        }

        self._build_content_model(db, games)
        self._build_collaborative_model(db)
        self._build_popularity_model(games)

    # -- Contenido ---------------------------------------------------------

    def _build_content_model(self, db: Session, games: list[Game]) -> None:
        """Matriz TF-IDF construida directo desde los datos, sin "sopa" de texto.

        Términos: etiquetas comunitarias (slug inglés, vía SteamSpy) y
        géneros (slug español). Las categorías de plataforma quedan afuera a
        propósito: son filtros de modalidad, no semántica — que "Logros de
        Steam" pese en la similitud era una de las causas de que dos indies
        cualesquiera se vieran idénticos. La descripción también queda
        afuera: marketing con vocabulario poco discriminante.

        La frecuencia (TF) de una etiqueta es la RAÍZ de su proporción de
        votos dentro del juego (91 mil votos de "FPS" en CS2 y 800 en un
        indie significan lo mismo si la proporción es la misma: la
        popularidad ya entra por otro canal). La raíz amortigua el sesgo de
        la distribución de votos sin el problema del log sobre valores < 1.
        ``TfidfTransformer`` aplica después el IDF y normaliza L2: sigue
        siendo TF-IDF en sentido estricto — cambió qué cuenta como término y
        cómo se mide su frecuencia, no el modelo.
        """
        # slug -> columna. Géneros y etiquetas comparten el espacio: si un
        # concepto existe en ambos ("indie"), se refuerzan en la misma celda.
        genre_terms = (
            {genre.slug for game in games for genre in game.genres}
            if self.config.genre_tf > 0
            else set()
        )
        vote_rows = (
            db.execute(
                select(game_tags.c.game_id, Tag.slug, game_tags.c.votes)
                .join(Tag, Tag.id == game_tags.c.tag_id)
                .where(Tag.kind == "community")
            ).all()
            if self.config.use_community_tags
            else []
        )
        community_terms = {slug for _, slug, _ in vote_rows}
        self._term_index: dict[str, int] = {
            term: column
            for column, term in enumerate(sorted(genre_terms | community_terms))
        }

        matrix = lil_matrix((len(games), max(len(self._term_index), 1)))
        votes_by_game: dict[int, list[tuple[str, int]]] = {}
        for game_id, slug, votes in vote_rows:
            votes_by_game.setdefault(game_id, []).append((slug, votes or 1))

        for row, game in enumerate(games):
            if self.config.genre_tf > 0:
                for genre in game.genres:
                    matrix[row, self._term_index[genre.slug]] = self.config.genre_tf
            tag_votes = votes_by_game.get(game.id, [])
            total_votes = sum(votes for _, votes in tag_votes)
            for slug, votes in tag_votes:
                if self.config.use_vote_weights:
                    share = votes / total_votes if total_votes else 0.0
                    matrix[row, self._term_index[slug]] = float(np.sqrt(share))
                else:
                    # Variante de evaluación: membresía binaria, sin votos.
                    matrix[row, self._term_index[slug]] = 1.0

        self._transformer = TfidfTransformer(norm="l2", smooth_idf=True)
        if games and self._term_index:
            self._tfidf = self._transformer.fit_transform(matrix.tocsr())
        else:
            # Sin juegos con rasgos no hay corpus que entrenar; la matriz
            # vacía hace que todo el camino de contenido devuelva None y el
            # motor degrade a popularidad, en vez de reventar en sklearn.
            self._tfidf = csr_matrix((len(games), 0))

    def _similarity_row(self, index: int) -> np.ndarray:
        """Similitud de un juego contra todo el catálogo, calculada al vuelo.

        Antes se precomputaba la matriz completa de similitud (juegos x
        juegos, densa). Con el catálogo chico funcionaba; con el catálogo
        post-ingesta (decenas de miles de juegos) esa matriz son varios GB y
        construir el motor se vuelve imposible. Como ``_tfidf`` ya está
        normalizada L2, la fila de similitud es un producto matriz-vector
        ralo: milisegundos, sin memoria extra.
        """
        if self._tfidf.shape[1] == 0:
            return np.zeros(len(self.game_ids))
        row = np.asarray((self._tfidf @ self._tfidf[index].T).todense()).ravel()
        row[index] = 0.0
        return row

    def score_terms(self, term_weights: dict[str, float]) -> np.ndarray | None:
        """Afinidad de cada juego con una consulta ponderada término->peso.

        Es el punto de entrada del asistente: el vocabulario del ánimo
        (``app.ml.quiz_vocab``) se convierte acá en un vector de consulta en
        el mismo espacio TF-IDF del catálogo. Los términos que no existen en
        el corpus se ignoran; si no sobrevive ninguno devuelve ``None`` para
        que quien llama degrade declarándolo, no en silencio.
        """
        if not term_weights or not self.game_ids or not self._term_index:
            return None
        query = lil_matrix((1, len(self._term_index)))
        known = 0
        for term, weight in term_weights.items():
            column = self._term_index.get(term)
            if column is not None and weight > 0:
                query[0, column] = float(weight)
                known += 1
        if not known:
            return None
        vector = self._transformer.transform(query.tocsr())
        if vector.nnz == 0:
            return None
        return cosine_similarity(vector, self._tfidf).ravel()

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

        if shape[0] == 0 or shape[1] == 0:
            # cosine_similarity rechaza una matriz de 0 muestras (0 usuarios
            # o, con el filtro de fichas sin enriquecer, 0 juegos).
            self.item_similarity = np.zeros((shape[1], shape[1]))
        else:
            self.item_similarity = cosine_similarity(centered.T)
            np.fill_diagonal(self.item_similarity, 0.0)

    # -- Popularidad -------------------------------------------------------

    def _build_popularity_model(self, games: list[Game]) -> None:
        averages = np.array([game.avg_rating for game in games], dtype=float)
        counts = np.array([game.ratings_count for game in games], dtype=float)

        # Juegos sin evidencia interna ni reseñas importadas, pero con la
        # señal masiva de SteamSpy (nivel 0/1 de la ingesta): se sintetiza la
        # misma escala que usa `recompute_game_aggregates` para las reseñas
        # de Steam (1 + 4·ratio de positivos), con el total como evidencia.
        # Así, un juego que nadie abrió nunca compite en popularidad y
        # alcance con los que sí — el punto de toda la ingesta.
        for index, game in enumerate(games):
            if counts[index] > 0:
                continue
            positive = game.steamspy_positive or 0
            negative = game.steamspy_negative or 0
            total = positive + negative
            if total > 0:
                averages[index] = 1 + 4 * (positive / total)
                counts[index] = total

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

        # Alcance: cuánta gente lo jugó, en escala logarítmica (la diferencia
        # entre 500 y 5.000 reseñas importa mucho más que entre 1 y 1,5
        # millones). Va aparte de `popularity` a propósito: son preguntas
        # distintas — "¿qué tan bueno es?" contra "¿qué tan masivo es?" — y
        # mezclarlas rompería el orden de "mejor valorados" del catálogo.
        # Sin este término, un indie con 500 reseñas 99 % positivas le gana
        # siempre a Counter-Strike con 9,7 millones al 86 %.
        self.reach = _normalize(np.log10(np.maximum(counts, 1.0) + 1.0))

    # -- Consultas ---------------------------------------------------------

    def similar_games(self, game_id: int, limit: int = 10) -> list[Recommendation]:
        """Juegos parecidos por contenido a uno dado."""
        if game_id not in self._game_index:
            return []
        index = self._game_index[game_id]
        similarities = self._similarity_row(index)
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
        profile = (self._tfidf[liked].multiply(weights[:, np.newaxis])).sum(axis=0)
        profile = np.asarray(profile)
        norm = np.linalg.norm(profile)
        if norm < 1e-9:
            return None
        return cosine_similarity(profile / norm, self._tfidf).ravel()

    def _content_scores_from_preferences(self, genre_slugs: list[str]) -> np.ndarray | None:
        """Perfil de contenido a partir de los géneros elegidos en el onboarding.

        Es un caso particular de ``score_terms``: cada género elegido pesa
        igual, y el TF-IDF resuelve el resto.
        """
        if not genre_slugs:
            return None
        return self.score_terms({slug: 1.0 for slug in genre_slugs})

    def suggest_by_mood(
        self,
        term_weights: dict[str, float] | list[str],
        limit: int = 30,
        exclude: set[int] | None = None,
    ) -> list[Recommendation]:
        """Perfil de contenido armado al vuelo a partir de una respuesta
        puntual (el asistente "¿Qué jugamos hoy?"), sin pesar el historial
        del usuario a propósito: la pregunta es por el ánimo de ahora mismo,
        no por el gusto general — usar ``_content_scores_from_history`` acá
        le taparía la respuesta a cualquiera que ya tenga valoraciones.

        Acepta un dict término->peso (el vocabulario del asistente, ver
        ``app.ml.quiz_vocab``) o una lista de slugs con peso uniforme (el
        contrato viejo, que el frontend legado sigue usando).
        """
        if isinstance(term_weights, list):
            term_weights = {slug: 1.0 for slug in term_weights}
        content = self.score_terms(term_weights)
        if content is None:
            return []

        # A diferencia de la estrategia "contenido" del recomendador
        # principal, acá el alcance pesa explícitamente: quien pregunta "qué
        # juego hoy" espera títulos que conozca o pueda jugar con gente, no
        # la joya oculta con quince reseñas. La afinidad sigue mandando.
        combined = (
            self.config.w_content * _normalize(content)
            + self.config.w_quality * _normalize(self.popularity)
            + self.config.w_reach * self.reach
        )
        order = np.argsort(combined)[::-1]
        excluded = exclude or set()

        results: list[Recommendation] = []
        for index in order:
            game_id = self.game_ids[index]
            if game_id in excluded:
                continue
            results.append(
                Recommendation(
                    game_id=game_id,
                    score=round(float(combined[index]), 4),
                    source=RecommendationSource.CONTENT,
                    reason="Coincide con el ánimo que elegiste",
                    components={
                        "contenido": round(float(content[index]), 4),
                        "popularidad": round(float(self.popularity[index]), 4),
                        "alcance": round(float(self.reach[index]), 4),
                    },
                )
            )
            if len(results) >= limit:
                break
        return results

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
        similarities = self._similarity_row(target_index)[rated]
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
