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
- **Popularidad** — valoración ajustada por cantidad de evidencia. Responde siempre, incluso
  para un usuario del que no se sabe absolutamente nada.

La estrategia se elige según cuánto historial tenga el usuario, de modo que
el arranque en frío degrada de forma gradual en lugar de fallar.
"""

from __future__ import annotations

import threading
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import csr_matrix, lil_matrix
from sklearn.feature_extraction.text import TfidfTransformer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.local_model import load_for_database
from app.models import (
    Game, Rating, RecommendationSource, SteamCatalogEntry, SteamCatalogSync,
    Tag, User, game_tags,
)

# Vecinos considerados al predecir con filtrado colaborativo.
NEIGHBOURS = 20
# Una coincidencia aislada no establece una relación entre dos juegos.
MIN_SHARED_RATERS = 2
SIMILARITY_SHRINKAGE = 5.0
# MMR: peso de la afinidad frente a la repetición de contenido en la tanda.
DISCOVERY_RELEVANCE = {"familiar": 1.0, "balanced": 0.88, "explore": 0.65}
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
    signals: list[str] = field(default_factory=list)


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


def _recommendable_game_filter():
    """Mismo universo para construir el motor y comprobar su caché.

    Indexar un AppID sin rasgos no modifica el modelo. Un elemento confirmado
    como DLC/software queda fuera, conservando sus interacciones en la base.
    """
    non_game = select(SteamCatalogEntry.appid).where(
        SteamCatalogEntry.appid == Game.steam_app_id,
        SteamCatalogEntry.status == "non_game",
    ).exists()
    return and_(
        or_(Game.steam_app_id.is_(None), Game.steam_synced_at.is_not(None),
            Game.genres.any(), Game.tags.any()),
        ~non_game,
    )


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
                .where(_recommendable_game_filter())
                .order_by(Game.id)
            )
        )
        self.game_ids: list[int] = [game.id for game in games]
        self.game_names: dict[int, str] = {game.id: game.name for game in games}
        self._genre_slugs = {
            game.id: {genre.slug for genre in game.genres} for game in games
        }
        self._genre_names = {
            genre.slug: genre.name for game in games for genre in game.genres
        }
        self._game_index: dict[int, int] = {
            game_id: index for index, game_id in enumerate(self.game_ids)
        }

        self._build_content_model(db, games)
        self._build_collaborative_model(db)
        self._build_popularity_model(games)
        self.local_model, self.local_model_status = load_for_database(db)

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
                .join(Game, Game.id == game_tags.c.game_id)
                .where(Tag.kind == "community", _recommendable_game_filter())
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
        user_rows, game_columns, scores = [], [], []
        for user_id, game_id, score in rows:
            if game_id not in self._game_index:
                continue
            user_rows.append(self._user_index[user_id])
            game_columns.append(self._game_index[game_id])
            scores.append(score)

        # Almacenar sólo notas observadas evita reservar usuarios × catálogo.
        # CSR permite leer un historial en O(notas); los ceros ausentes nunca
        # se tratan como valoraciones negativas ni participan de la media.
        self.matrix = csr_matrix((scores, (user_rows, game_columns)), shape=shape, dtype=float)
        self.matrix.sort_indices()
        counts = np.diff(self.matrix.indptr)
        totals = np.asarray(self.matrix.sum(axis=1)).ravel()
        # Media de cada usuario sobre lo que efectivamente valoró.
        self.user_means = np.divide(
            totals, counts, out=np.full(len(self.user_ids), 3.0), where=counts > 0
        )

        # Centrar por usuario neutraliza que unos puntúen alto y otros bajo:
        # lo que importa es cuánto se aparta cada nota de su propia media.
        centered = self.matrix.copy()
        centered.data -= np.repeat(self.user_means, counts)
        centered.eliminate_zeros()
        self._centered_sparse = centered.tocsc()
        # Una nota igual a la media sigue siendo evidencia de co-valoración,
        # aunque desaparezca de la matriz centrada por valer cero.
        observed = self.matrix.astype(np.int32)
        observed.data.fill(1)
        self._observed = observed.tocsc()

    def _history(self, row: int) -> tuple[np.ndarray, np.ndarray]:
        """Columnas y notas observadas de una persona, sin densificar la fila."""
        start, end = self.matrix.indptr[row:row + 2]
        return self.matrix.indices[start:end], self.matrix.data[start:end]

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
        """Perfil firmado: las notas sobre 3 atraen y las inferiores alejan.

        Una nota baja nunca se convierte en un gusto porque falten positivos.
        El coseno firmado conserva la intensidad sin amplificar diferencias
        mínimas con un reescalado relativo al catálogo.
        """
        if user_id not in self._user_index:
            return None
        row = self._user_index[user_id]
        rated, ratings = self._history(row)
        if rated.size == 0 or self._tfidf.shape[1] == 0:
            return None
        weights = (ratings - 3.0) / 2.0
        profile = (self._tfidf[rated].multiply(weights[:, np.newaxis])).sum(axis=0)
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
        contributions = {
            "contenido": self.config.w_content * _normalize(content),
            "popularidad": self.config.w_quality * _normalize(self.popularity),
            "alcance": self.config.w_reach * self.reach,
        }
        combined = sum(contributions.values())
        order = np.argsort(combined)[::-1]
        excluded = exclude or set()

        results: list[Recommendation] = []
        for index in order:
            game_id = self.game_ids[index]
            if game_id in excluded:
                continue
            components = self._rounded_components(contributions, index)
            results.append(
                Recommendation(
                    game_id=game_id,
                    score=round(sum(components.values()), 4),
                    source=RecommendationSource.CONTENT,
                    reason="Coincide con el ánimo que elegiste",
                    components=components,
                )
            )
            if len(results) >= limit:
                break
        return results

    def _collaborative_evidence(
        self, user_id: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Predicciones, fuerza de evidencia y vecinos útiles por candidato.

        La similitud se reduce por cantidad de usuarios que valoraron ambos
        juegos. No se usa min-max sobre predicciones: con poca evidencia no
        debe aparecer una señal extrema sólo por ser la mayor del catálogo.
        La fuerza de evidencia regula el peso del canal; no es probabilidad.
        """
        if user_id not in self._user_index:
            return None
        row = self._user_index[user_id]
        rated, ratings = self._history(row)
        if rated.size == 0:
            return None

        # Si ninguna nota se aparta de la media del propio usuario no hay nada
        # que propagar a los vecinos: todas las predicciones saldrían iguales.
        # Pasa con una sola valoración, o cuando alguien puntuó todo igual.
        # Devolver None acá hace que el caso degrade a popularidad en lugar de
        # a un orden arbitrario entre predicciones empatadas.
        deviations = ratings - self.user_means[row]
        if float(np.abs(deviations).max()) < 1e-9:
            return None

        predictions = np.full(len(self.game_ids), self.user_means[row])
        evidence = np.zeros(len(self.game_ids))
        neighbour_counts = np.zeros(len(self.game_ids), dtype=int)
        rated_set = set(rated)
        centered_history = self._centered_sparse[:, rated].T
        observed_history = self._observed[:, rated]
        # Incluso un historial grande evita materializar catálogo × historial:
        # los temporales densos se limitan a un millón de pares por bloque.
        block_size = min(1024, max(1, 1_000_000 // rated.size))
        for start in range(0, len(self.game_ids), block_size):
            end = min(start + block_size, len(self.game_ids))
            similarities_to_history = cosine_similarity(
                self._centered_sparse[:, start:end].T, centered_history
            )
            shared = (self._observed[:, start:end].T @ observed_history).toarray()
            similarities_to_history *= shared / (shared + SIMILARITY_SHRINKAGE)
            similarities_to_history[shared < MIN_SHARED_RATERS] = 0.0
            for offset, similarities in enumerate(similarities_to_history):
                target = start + offset
                if target in rated_set:
                    continue
                positive = similarities > 0
                if not positive.any():
                    continue

                neighbour_deviations = deviations[positive]
                weights = similarities[positive]
                if weights.size > NEIGHBOURS:
                    top = np.argsort(weights)[::-1][:NEIGHBOURS]
                    neighbour_deviations, weights = neighbour_deviations[top], weights[top]

                denominator = np.abs(weights).sum()
                if denominator < 1e-9:
                    continue
                deviation = float((weights * neighbour_deviations).sum() / denominator)
                predictions[target] = np.clip(self.user_means[row] + deviation, 1.0, 5.0)
                evidence[target] = denominator / (denominator + 1.0)
                neighbour_counts[target] = len(weights)

        if not evidence.any():
            return None
        return predictions, evidence, neighbour_counts

    def _collaborative_scores(self, user_id: int) -> np.ndarray | None:
        """Compatibilidad con consumidores que sólo necesitan las predicciones."""
        result = self._collaborative_evidence(user_id)
        return result[0] if result is not None else None

    def _best_content_match(self, user_id: int, target_index: int) -> str | None:
        """Juego ya valorado que más se parece al recomendado (explicabilidad)."""
        if user_id not in self._user_index:
            return None
        row = self._user_index[user_id]
        observed, ratings = self._history(row)
        rated = observed[ratings > 3.0]
        if rated.size == 0:
            return None
        similarities = self._similarity_row(target_index)[rated]
        if similarities.max() <= 0:
            return None
        return self.game_names[self.game_ids[rated[int(np.argmax(similarities))]]]

    # -- Recomendación -----------------------------------------------------

    def _local_scores(self, user_id: int | None) -> tuple[np.ndarray, np.ndarray] | None:
        if self.local_model is None or user_id not in self._user_index:
            return None
        row = self._user_index[user_id]
        observed, ratings = self._history(row)
        history = [(self.game_ids[i], float(score)) for i, score in zip(observed, ratings)]
        prediction = self.local_model.predict(history)
        if prediction is None:
            return None
        values, support = np.zeros(len(self.game_ids)), np.zeros(len(self.game_ids))
        for pos, gid in enumerate(self.local_model.game_ids):
            index = self._game_index.get(int(gid))
            count = int(self.local_model.counts[pos])
            if index is not None and count >= 2:
                values[index] = (prediction[pos] - 1.0) / 4.0
                support[index] = count / (count + 5.0)
        return (values, support) if support.any() else None

    def personal_scores(self, user_id: int, preferred_genres: list[str]) -> tuple[np.ndarray, str]:
        """Afinidad de TODOS los juegos, incluidos los jugados, para grupos.

        Las notas conocidas tienen prioridad. Un miembro sin perfil aporta
        0.5 neutral, no una supuesta opinión personal derivada de popularidad.
        """
        history = self._content_scores_from_history(user_id)
        preference = self._content_scores_from_preferences(preferred_genres)
        values = (history + 1.0) / 2.0 if history is not None else preference
        if values is None:
            values, basis = np.full(len(self.game_ids), 0.5), "popularidad"
        else:
            values, basis = np.clip(values, 0, 1), "contenido"
        local = self._local_scores(user_id) if self.local_model_status.get("automatic_eligible") else None
        if local is not None:
            learned, support = local
            weight = 0.65 * support
            values = (1 - weight) * values + weight * learned
            basis = "ia_local"
        if user_id in self._user_index:
            row = self._user_index[user_id]
            observed, ratings = self._history(row)
            values[observed] = (ratings - 1.0) / 4.0
            if observed.size and basis == "popularidad":
                basis = "contenido"
        return values, basis

    def recommend(
        self,
        user_id: int | None,
        preferred_genres: list[str] | None = None,
        limit: int = 10,
        strategy: str = "auto",
        exclude: set[int] | None = None,
        discovery: str = "balanced",
    ) -> list[Recommendation]:
        """Devuelve los mejores juegos para un usuario.

        Args:
            strategy: ``auto`` elige según el historial disponible. Forzar
                ``contenido``, ``colaborativo``, ``hibrido`` o ``popularidad``
                permite comparar los enfoques entre sí.
            discovery: controla la diversidad de la tanda. El score conserva
                la afinidad base, incluso si cambia el orden de presentación.
        """
        if discovery not in DISCOVERY_RELEVANCE:
            raise ValueError("Modo de descubrimiento desconocido")
        if limit <= 0 or not self.game_ids:
            return []
        excluded = set(exclude or set())
        if user_id is not None and user_id in self._user_index:
            row = self._user_index[user_id]
            excluded.update(
                self.game_ids[i] for i in self._history(row)[0]
            )

        history_content = (
            self._content_scores_from_history(user_id) if user_id is not None else None
        )
        preferences = self._content_scores_from_preferences(preferred_genres or [])
        # El coseno del historial firmado vive en [-1, 1]; el de preferencias
        # positivas en [0, 1]. Escalas fijas, independientes del candidato líder.
        content = (
            (np.clip(history_content, -1.0, 1.0) + 1.0) / 2.0
            if history_content is not None else None
        )
        if content is None:
            content = preferences
        elif preferences is not None:
            content = 0.8 * content + 0.2 * np.clip(preferences, 0.0, 1.0)
        collaboration = (
            self._collaborative_evidence(user_id)
            if user_id is not None and strategy in {"auto", "hibrido", "colaborativo"}
            else None
        )
        collaborative = collaboration[0] if collaboration is not None else None
        evidence = collaboration[1] if collaboration is not None else np.zeros(len(self.game_ids))
        neighbour_counts = (
            collaboration[2] if collaboration is not None
            else np.zeros(len(self.game_ids), dtype=int)
        )
        popularity = np.clip((self.popularity - 1.0) / 4.0, 0.0, 1.0)
        history_size = (
            self._history(self._user_index[user_id])[0].size
            if user_id in self._user_index
            else 0
        )

        chosen = strategy
        use_local = strategy == "ia_local" or (
            strategy in {"auto", "hibrido"} and self.local_model_status.get("automatic_eligible")
        )
        local = self._local_scores(user_id) if use_local else None
        if chosen == "ia_local":
            # El canal local se mezcla más abajo; el respaldo sigue teniendo
            # motivos reales cuando faltan artefacto, historial o ítems.
            chosen = "contenido" if content is not None else "popularidad"
        if strategy == "auto":
            if collaborative is not None and history_size >= settings.REC_COLD_START_THRESHOLD:
                chosen = "hibrido"
            elif content is not None:
                chosen = "contenido"
            else:
                chosen = "popularidad"

        content_weight = max(0.0, settings.REC_CONTENT_WEIGHT)
        collab_weight = max(0.0, settings.REC_COLLAB_WEIGHT)
        total_weight = content_weight + collab_weight
        collab_weight = collab_weight / total_weight if total_weight else 0.5

        components: dict[str, np.ndarray] = {}
        if chosen == "hibrido" and content is not None and collaborative is not None:
            # Cada candidato cede al contenido la parte colaborativa que sus
            # vecinos no justifican. Sin vecinos no se inventa una predicción.
            effective_collab_weight = collab_weight * evidence
            components = {
                "contenido": (1.0 - effective_collab_weight) * content,
                "colaborativo": effective_collab_weight * ((collaborative - 1.0) / 4.0),
            }
            source = RecommendationSource.HYBRID
        elif chosen in {"colaborativo", "hibrido"} and collaborative is not None:
            components = {
                "colaborativo": evidence * ((collaborative - 1.0) / 4.0),
                "popularidad": (1.0 - evidence) * popularity,
            }
            source = RecommendationSource.COLLABORATIVE
        elif chosen in {"contenido", "hibrido"} and content is not None:
            # Un toque de popularidad desempata entre juegos igual de afines y
            # evita recomendar títulos afines pero muy mal valorados.
            components = {"contenido": 0.8 * content, "popularidad": 0.2 * popularity}
            source = RecommendationSource.CONTENT
        else:
            components = {"popularidad": popularity}
            source = RecommendationSource.POPULARITY

        local_weight = np.zeros(len(self.game_ids))
        if local is not None:
            learned, support = local
            local_weight = support * (0.8 if strategy == "ia_local" else 0.35)
            components = {key: values * (1.0 - local_weight) for key, values in components.items()}
            components["ia_local"] = local_weight * learned

        combined = sum(components.values())
        order = self._diverse_order(combined, excluded, limit, discovery)
        results: list[Recommendation] = []
        for index in order:
            game_id = self.game_ids[index]
            item_source = source
            if evidence[index] == 0 or (
                source is RecommendationSource.HYBRID and collab_weight == 0
            ):
                if source is RecommendationSource.HYBRID:
                    item_source = RecommendationSource.CONTENT
                elif source is RecommendationSource.COLLABORATIVE:
                    item_source = RecommendationSource.POPULARITY
            contributions = self._rounded_components(components, index)
            signals = self._build_signals(
                item_source, user_id, index, preferred_genres or [], int(neighbour_counts[index])
            )
            if local_weight[index] > 0:
                item_source = RecommendationSource.LOCAL if strategy == "ia_local" else RecommendationSource.HYBRID
                signals.insert(0, "Modelo entrenado localmente con valoraciones; ajustado a tus notas actuales")
                if self.local_model_status.get("data_label") == "demo":
                    signals.append("El modelo actual se entrenó con datos de demostración")
            results.append(
                Recommendation(
                    game_id=game_id,
                    score=round(sum(contributions.values()), 4),
                    source=item_source,
                    reason=signals[0],
                    components=contributions,
                    signals=signals,
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _rounded_components(components: dict[str, np.ndarray], index: int) -> dict[str, float]:
        """El score público se obtiene sumando estos aportes ya redondeados."""
        return {name: round(float(values[index]), 4) for name, values in components.items()}

    def _diverse_order(
        self, scores: np.ndarray, excluded: set[int], limit: int, discovery: str
    ) -> list[int]:
        """MMR en un conjunto acotado: evita llenar la tanda con clones.

        La afinidad sigue siendo el criterio principal. Los empates se
        resuelven por ID, así una consulta idéntica siempre es reproducible.
        """
        ranked = [
            int(i) for i in np.argsort(-scores, kind="stable")
            if self.game_ids[i] not in excluded
        ]
        relevance = DISCOVERY_RELEVANCE[discovery]
        if relevance == 1.0 or self._tfidf.shape[1] == 0:
            return ranked[:limit]
        pool = np.array(ranked[:max(100, limit * 10)], dtype=int)
        if pool.size == 0:
            return []
        selected: list[int] = []
        available = np.ones(pool.size, dtype=bool)
        redundancy = np.zeros(pool.size)
        for _ in range(min(limit, pool.size)):
            utility = relevance * scores[pool] - (1.0 - relevance) * redundancy
            utility[~available] = -np.inf
            position = int(np.argmax(utility))
            index = int(pool[position])
            selected.append(index)
            available[position] = False
            similarity = np.asarray((self._tfidf[pool] @ self._tfidf[index].T).todense()).ravel()
            redundancy = np.maximum(redundancy, similarity)
        return selected

    def _build_signals(
        self,
        source: RecommendationSource,
        user_id: int | None,
        index: int,
        preferred_genres: list[str],
        neighbour_count: int,
    ) -> list[str]:
        if source is RecommendationSource.POPULARITY:
            return ["Valoración general ajustada por cantidad de reseñas disponibles"]
        signals = []
        if source in {RecommendationSource.CONTENT, RecommendationSource.HYBRID}:
            similar = self._best_content_match(user_id, index) if user_id is not None else None
            if similar:
                signals.append(f"Se parece a {similar}, que valoraste bien")
            common = sorted(self._genre_slugs[self.game_ids[index]] & set(preferred_genres))
            if common:
                names = ", ".join(self._genre_names[slug] for slug in common[:3])
                signals.append(f"Géneros que elegiste: {names}")
            if user_id in self._user_index:
                row = self._user_index[user_id]
                if np.any(self._history(row)[1] < 3.0):
                    signals.append("Tu perfil también tiene en cuenta las valoraciones negativas")
            if not signals:
                signals.append("Seleccionado con tu perfil de contenido y los datos disponibles")
        if neighbour_count and source in {RecommendationSource.COLLABORATIVE, RecommendationSource.HYBRID}:
            signals.append(
                f"Patrones de valoración compartidos con {neighbour_count} "
                f"{'juego' if neighbour_count == 1 else 'juegos'} de tu historial"
            )
        return signals


# ---------------------------------------------------------------------------
# Caché del motor
# ---------------------------------------------------------------------------

_engine: RecommenderEngine | None = None
_fingerprint: tuple[object, int, int, int, int, int] | None = None
_lock = threading.Lock()


def _current_fingerprint(db: Session) -> tuple[object, int, int, int, int, int]:
    """Detecta cambios útiles, incluso los escritos por un worker separado."""
    try:
        model_modified = Path(settings.LOCAL_MODEL_PATH).stat().st_mtime_ns
    except OSError:
        model_modified = 0
    bind = db.get_bind()
    # Identidad real del motor: dos SQLite en memoria pueden tener idéntica
    # URL y contadores, pero pertenecen a bases y usuarios diferentes.
    database_identity = getattr(bind, "engine", bind)
    return (
        database_identity,
        db.scalar(select(func.count(Game.id)).where(_recommendable_game_filter())) or 0,
        db.scalar(select(func.count(Rating.id))) or 0,
        db.scalar(select(func.max(Rating.id))) or 0,
        db.scalar(select(SteamCatalogSync.revision).where(SteamCatalogSync.id == 1)) or 0,
        model_modified,
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
    db: Session, user: User, limit: int | None = None, strategy: str = "auto",
    discovery: str = "balanced",
) -> list[Recommendation]:
    """Recomendaciones para un usuario, resolviendo sus preferencias."""
    engine = get_engine(db)
    genre_slugs = [preference.genre.slug for preference in user.preferences]
    return engine.recommend(
        user_id=user.id,
        preferred_genres=genre_slugs,
        limit=limit or settings.REC_DEFAULT_LIMIT,
        strategy=strategy,
        discovery=discovery,
    )
