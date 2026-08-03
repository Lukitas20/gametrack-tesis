"""Módulo NLP: clasificación de sentimiento y ABSA sobre reseñas en español.

El enfoque es **basado en léxico y reglas**, no supervisado. Se eligió así
porque la plataforma no dispone de reseñas etiquetadas por humanos, y porque
cada decisión queda explicada por la cláusula concreta que la produjo, algo
que un clasificador denso no ofrece. Esa evidencia se persiste en
``ReviewAspect.evidence``.

El pipeline sobre cada reseña es:

1. **Segmentación** en oraciones y, dentro de ellas, en cláusulas separadas
   por conectores adversativos ("pero", "aunque"). Así "los gráficos son
   lindos pero corre mal" no promedia las dos opiniones en una sola.
2. **Detección de aspectos** por cláusula, con vocabularios ponderados y
   términos fuertes/débiles para resolver ambigüedades.
3. **Polaridad** de cada cláusula con manejo de negación, intensificadores y
   atenuadores.
4. **Agregación** por aspecto. Una cláusula que no menciona ningún aspecto se
   atribuye al de la cláusula anterior de la misma oración, porque la opinión
   sigue siendo sobre el mismo tema.

Se emite una opinión sobre un aspecto **sólo si hay carga sentimental**:
mencionar un aspecto no es opinar sobre él.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ml.lexicon import (
    ASPECT_TERMS,
    CONTRASTIVE_MARKERS,
    DIMINISHERS,
    INTENSIFIERS,
    NEGATION_EXCEPTIONS,
    NEGATION_WINDOW,
    NEGATORS,
    SENTIMENT_LEXICON,
    STRONG_TERM_THRESHOLD,
)
from app.models import Aspect, Game, Review, ReviewAspect, Sentiment

# Umbral de la banda neutra sobre la polaridad [-1, 1].
NEUTRAL_BAND = 0.25
# Cuando una cláusula menciona varios aspectos, se conservan los que llegan a
# esta fracción del aspecto dominante. Un valor alto favorece la precisión,
# que es lo que importa en un panel de analítica.
ASPECT_TIE_RATIO = 0.9
# La negación invierte la polaridad pero la atenúa: "no es bueno" es menos
# intenso que "es malo".
NEGATION_FACTOR = 0.7

_MAX_NGRAM = 3
_SENTENCE_SPLIT = re.compile(r"[.!?¡¿]+")
_CLAUSE_SPLIT = re.compile(r"[;:]")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CONTRASTIVE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in CONTRASTIVE_MARKERS) + r")\b"
)


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AspectOpinion:
    aspect: Aspect
    sentiment: Sentiment
    score: float
    confidence: float
    evidence: str


@dataclass(frozen=True)
class TextAnalysis:
    sentiment: Sentiment
    score: float
    confidence: float
    aspects: list[AspectOpinion] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Normalización y segmentación
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Pasa a minúsculas y quita acentos, para poder buscar en el léxico."""
    lowered = text.lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_text(text))


def split_into_clauses(text: str) -> list[list[str]]:
    """Divide el texto en oraciones y cada oración en cláusulas.

    Returns:
        Una lista por oración, con las cláusulas originales de esa oración.
        Mantener el agrupamiento por oración es lo que permite que una
        cláusula sin aspecto herede el de la anterior sin cruzar oraciones.
    """
    sentences = []
    for raw_sentence in _SENTENCE_SPLIT.split(text):
        sentence = raw_sentence.strip()
        if not sentence:
            continue

        clauses: list[str] = []
        for chunk in _CLAUSE_SPLIT.split(sentence):
            # El conector adversativo se conserva al inicio de la cláusula
            # siguiente; no aporta polaridad propia pero facilita la lectura
            # de la evidencia guardada.
            parts = _CONTRASTIVE_RE.split(normalize_text(chunk))
            if len(parts) == 1:
                if chunk.strip():
                    clauses.append(chunk.strip())
                continue

            # Se reparte el texto original usando las posiciones de los cortes
            # detectados sobre la versión normalizada (misma longitud).
            offsets = [m.start() for m in _CONTRASTIVE_RE.finditer(normalize_text(chunk))]
            previous = 0
            for offset in offsets:
                fragment = chunk[previous:offset].strip(" ,")
                if fragment:
                    clauses.append(fragment)
                previous = offset
            tail = chunk[previous:].strip(" ,")
            if tail:
                clauses.append(tail)

        if clauses:
            sentences.append(clauses)
    return sentences


# ---------------------------------------------------------------------------
# Emparejamiento con los vocabularios
# ---------------------------------------------------------------------------


def _lookup(key: str, vocabulary: dict[str, float]) -> float | None:
    """Busca un término, reduciendo el plural si no hay coincidencia exacta.

    Evita tener que duplicar cada entrada del léxico en singular y plural
    ("pobre"/"pobres", "genial"/"geniales"). Sólo se aplica a términos de una
    palabra: las claves multipalabra se emparejan tal cual.
    """
    if key in vocabulary:
        return vocabulary[key]
    if " " in key:
        return None
    for suffix in ("es", "s"):
        if key.endswith(suffix):
            singular = key[: -len(suffix)]
            if singular in vocabulary:
                return vocabulary[singular]
    return None


def _match_vocabulary(
    tokens: list[str], vocabulary: dict[str, float]
) -> list[tuple[int, str, float]]:
    """Empareja el vocabulario contra los tokens, con coincidencia más larga.

    Consumir los tokens de una coincidencia evita el doble conteo: en
    "sin peso" no se suma además el término "peso".
    """
    matches: list[tuple[int, str, float]] = []
    index = 0
    while index < len(tokens):
        for size in range(min(_MAX_NGRAM, len(tokens) - index), 0, -1):
            key = " ".join(tokens[index : index + size])
            value = _lookup(key, vocabulary)
            if value is not None:
                matches.append((index, key, value))
                index += size
                break
        else:
            index += 1
    return matches


def score_polarity(tokens: list[str]) -> tuple[float, int]:
    """Polaridad de una cláusula en [-1, 1] y cantidad de términos usados."""
    matches = _match_vocabulary(tokens, SENTIMENT_LEXICON)
    if not matches:
        return 0.0, 0

    weights: list[float] = []
    for start, key, value in matches:
        weight = value

        window = tokens[max(0, start - NEGATION_WINDOW) : start]
        joined_window = " ".join(window)
        is_exception = any(exc in joined_window for exc in NEGATION_EXCEPTIONS)
        if any(token in NEGATORS for token in window) and not is_exception:
            weight = -weight * NEGATION_FACTOR

        # Los modificadores se buscan a ambos lados: en español el adjetivo
        # suele ir detrás del sustantivo ("decepción enorme", "tirones
        # constantes"), así que mirar sólo hacia atrás pierde la mitad.
        span = len(key.split())
        neighbourhood = (
            tokens[max(0, start - 2) : start] + tokens[start + span : start + span + 2]
        )
        joined_neighbourhood = " ".join(neighbourhood)
        multiplier = 1.0
        for phrase, factor in DIMINISHERS.items():
            if " " in phrase and phrase in joined_neighbourhood:
                multiplier *= factor
        for token in neighbourhood:
            if token in INTENSIFIERS:
                multiplier *= INTENSIFIERS[token]
            elif token in DIMINISHERS:
                multiplier *= DIMINISHERS[token]

        weights.append(max(-1.0, min(1.0, weight * multiplier)))

    score = sum(weights) / len(weights)
    return max(-1.0, min(1.0, score)), len(weights)


def detect_aspects(tokens: list[str]) -> list[Aspect]:
    """Aspectos mencionados en una cláusula.

    Los términos débiles (``corre``, ``funciona``) sólo se consideran si en la
    cláusula no apareció ningún término fuerte de ningún aspecto: eso evita
    que "las mecánicas funcionan bien" se clasifique como optimización.
    """
    strong_totals: dict[Aspect, float] = {}
    weak_totals: dict[Aspect, float] = {}

    for aspect, vocabulary in ASPECT_TERMS.items():
        for _start, _key, value in _match_vocabulary(tokens, vocabulary):
            target = strong_totals if value >= STRONG_TERM_THRESHOLD else weak_totals
            target[aspect] = target.get(aspect, 0.0) + value

    totals = strong_totals or weak_totals
    if not totals:
        return []

    top = max(totals.values())
    return [aspect for aspect, value in totals.items() if value >= ASPECT_TIE_RATIO * top]


def label_from_score(score: float) -> Sentiment:
    if score > NEUTRAL_BAND:
        return Sentiment.POSITIVE
    if score < -NEUTRAL_BAND:
        return Sentiment.NEGATIVE
    return Sentiment.NEUTRAL


def _confidence(score: float, matches: int) -> float:
    """Confianza heurística: crece con la intensidad y con la evidencia."""
    if matches == 0:
        return 0.3
    return round(min(1.0, 0.4 + 0.12 * matches + 0.4 * abs(score)), 3)


# ---------------------------------------------------------------------------
# Análisis completo
# ---------------------------------------------------------------------------


def analyze_text(text: str) -> TextAnalysis:
    """Clasifica el sentimiento global y por aspecto de un texto."""
    if not text or not text.strip():
        return TextAnalysis(sentiment=Sentiment.NEUTRAL, score=0.0, confidence=0.0)

    # aspecto -> (suma de polaridad, cantidad de cláusulas, términos, evidencia)
    per_aspect: dict[Aspect, list[tuple[float, int, str]]] = {}
    global_scores: list[float] = []
    global_matches = 0

    for clauses in split_into_clauses(text):
        previous_aspects: list[Aspect] = []
        for clause in clauses:
            tokens = tokenize(clause)
            if not tokens:
                continue

            polarity, matches = score_polarity(tokens)
            if matches:
                global_scores.append(polarity)
                global_matches += matches

            aspects = detect_aspects(tokens)
            if aspects:
                previous_aspects = aspects
            else:
                # La opinión continúa sobre el tema de la cláusula anterior:
                # "el gameplay es correcto aunque le falta profundidad".
                aspects = previous_aspects

            for aspect in aspects:
                per_aspect.setdefault(aspect, []).append((polarity, matches, clause))

    overall_score = sum(global_scores) / len(global_scores) if global_scores else 0.0

    opinions: list[AspectOpinion] = []
    for aspect, entries in per_aspect.items():
        total_matches = sum(matches for _score, matches, _clause in entries)
        if total_matches == 0:
            # El aspecto se mencionó pero no se opinó sobre él.
            continue
        score = sum(score for score, _m, _c in entries) / len(entries)
        # Como evidencia se usa la cláusula de opinión más intensa.
        evidence = max(
            (entry for entry in entries if entry[1] > 0),
            key=lambda entry: abs(entry[0]),
        )[2]
        opinions.append(
            AspectOpinion(
                aspect=aspect,
                sentiment=label_from_score(score),
                score=round(score, 3),
                confidence=_confidence(score, total_matches),
                evidence=evidence,
            )
        )

    opinions.sort(key=lambda opinion: opinion.aspect.value)
    return TextAnalysis(
        sentiment=label_from_score(overall_score),
        score=round(overall_score, 3),
        confidence=_confidence(overall_score, global_matches),
        aspects=opinions,
    )


def analyze_review(review: Review) -> TextAnalysis:
    """Analiza una reseña combinando su título y su cuerpo."""
    text = f"{review.title}. {review.content}" if review.title else review.content
    return analyze_text(text)


def apply_analysis(db: Session, review: Review, analysis: TextAnalysis) -> None:
    """Vuelca el resultado del análisis sobre la entidad ``Review``.

    Los aspectos previos se borran y se descargan a la base *antes* de insertar
    los nuevos: si se dejara que ``delete-orphan`` lo resolviera solo, en un
    reanálisis SQLAlchemy podría emitir los INSERT antes que los DELETE y
    violar la constraint única ``(review_id, aspect)``.
    """
    if review.aspects:
        review.aspects.clear()
        db.flush()

    review.sentiment = analysis.sentiment
    review.sentiment_score = analysis.score
    review.sentiment_confidence = analysis.confidence
    review.is_analyzed = True
    review.analyzed_at = datetime.now(timezone.utc)
    review.aspects = [
        ReviewAspect(
            game_id=review.game_id,
            aspect=opinion.aspect,
            sentiment=opinion.sentiment,
            score=opinion.score,
            confidence=opinion.confidence,
            evidence=opinion.evidence,
        )
        for opinion in analysis.aspects
    ]


def analyze_pending_reviews(
    db: Session, limit: int | None = None, reanalyze: bool = False
) -> int:
    """Procesa las reseñas sin analizar y persiste el resultado.

    Args:
        limit: máximo de reseñas a procesar; ``None`` procesa todas.
        reanalyze: si es ``True`` reprocesa también las ya analizadas, algo
            necesario después de modificar el léxico.

    Returns:
        La cantidad de reseñas procesadas.
    """
    query = select(Review)
    if not reanalyze:
        query = query.where(Review.is_analyzed.is_(False))
    if limit:
        query = query.limit(limit)

    processed = 0
    for review in db.scalars(query).all():
        apply_analysis(db, review, analyze_review(review))
        processed += 1

    db.commit()
    return processed


# ---------------------------------------------------------------------------
# Agregación para el panel de desarrollador
# ---------------------------------------------------------------------------


def _distribution(rows: list[tuple[Sentiment, int]]) -> dict[str, int]:
    counts = {sentiment.value: 0 for sentiment in Sentiment}
    for sentiment, count in rows:
        counts[sentiment.value] = count
    return counts


def _net_score(counts: dict[str, int]) -> float:
    """Sentimiento neto en [-1, 1]: proporción de positivas menos negativas."""
    total = sum(counts.values())
    if not total:
        return 0.0
    return round((counts["positivo"] - counts["negativo"]) / total, 3)


def aspect_breakdown(db: Session, game_ids: list[int]) -> list[dict]:
    """Desglose por aspecto para un conjunto de juegos."""
    if not game_ids:
        return []

    rows = db.execute(
        select(
            ReviewAspect.aspect,
            ReviewAspect.sentiment,
            func.count(ReviewAspect.id),
            func.avg(ReviewAspect.score),
        )
        .where(ReviewAspect.game_id.in_(game_ids))
        .group_by(ReviewAspect.aspect, ReviewAspect.sentiment)
    ).all()

    grouped: dict[Aspect, dict] = {}
    for aspect, sentiment, count, avg_score in rows:
        entry = grouped.setdefault(
            aspect,
            {"counts": {s.value: 0 for s in Sentiment}, "weighted": 0.0, "total": 0},
        )
        entry["counts"][sentiment.value] = count
        entry["weighted"] += float(avg_score or 0.0) * count
        entry["total"] += count

    breakdown = []
    for aspect, entry in grouped.items():
        total = entry["total"]
        breakdown.append(
            {
                "aspecto": aspect.value,
                "menciones": total,
                "distribucion": entry["counts"],
                "sentimiento_neto": _net_score(entry["counts"]),
                "score_promedio": round(entry["weighted"] / total, 3) if total else 0.0,
            }
        )

    breakdown.sort(key=lambda item: item["sentimiento_neto"])
    return breakdown


def _evidence_samples(db: Session, game_ids: list[int], limit: int = 3) -> dict[str, list[str]]:
    """Citas textuales que respaldan la valoración negativa de cada aspecto.

    Se deduplica el texto: varias reseñas pueden coincidir en la frase exacta y
    repetir la misma cita no agrega evidencia, sólo ocupa lugar en el panel.
    """
    samples: dict[str, list[str]] = {}
    for aspect in Aspect:
        rows = db.scalars(
            select(ReviewAspect.evidence)
            .where(
                ReviewAspect.game_id.in_(game_ids),
                ReviewAspect.aspect == aspect,
                ReviewAspect.sentiment == Sentiment.NEGATIVE,
                ReviewAspect.evidence.is_not(None),
            )
            .order_by(ReviewAspect.score)
        ).all()

        unique: list[str] = []
        for row in rows:
            text = row.strip()
            if text and text not in unique:
                unique.append(text)
            if len(unique) == limit:
                break
        if unique:
            samples[aspect.value] = unique
    return samples


def game_analytics(db: Session, game: Game) -> dict:
    """Informe completo de un juego para el panel de desarrollador."""
    sentiment_rows = db.execute(
        select(Review.sentiment, func.count(Review.id))
        .where(Review.game_id == game.id, Review.is_analyzed.is_(True))
        .group_by(Review.sentiment)
    ).all()
    distribution = _distribution([(s, c) for s, c in sentiment_rows if s is not None])

    analyzed = sum(distribution.values())
    pending = db.scalar(
        select(func.count(Review.id)).where(
            Review.game_id == game.id, Review.is_analyzed.is_(False)
        )
    )

    breakdown = aspect_breakdown(db, [game.id])

    return {
        "juego": {
            "id": game.id,
            "nombre": game.name,
            "slug": game.slug,
            "desarrollador": game.developer,
            "rating_promedio": game.avg_rating,
            "cantidad_ratings": game.ratings_count,
        },
        "resenas": {
            "analizadas": analyzed,
            "pendientes": pending or 0,
            "distribucion": distribution,
            "sentimiento_neto": _net_score(distribution),
        },
        "aspectos": breakdown,
        "punto_debil": breakdown[0]["aspecto"] if breakdown else None,
        "punto_fuerte": breakdown[-1]["aspecto"] if breakdown else None,
        "citas_negativas": _evidence_samples(db, [game.id]),
    }


def studio_analytics(db: Session, studio: str) -> dict:
    """Informe agregado de todos los juegos de un estudio."""
    games = db.scalars(select(Game).where(Game.developer == studio)).all()
    game_ids = [game.id for game in games]

    if not game_ids:
        return {
            "estudio": studio,
            "juegos": [],
            "resenas": {"analizadas": 0, "distribucion": _distribution([]),
                        "sentimiento_neto": 0.0},
            "aspectos": [],
        }

    sentiment_rows = db.execute(
        select(Review.sentiment, func.count(Review.id))
        .where(Review.game_id.in_(game_ids), Review.is_analyzed.is_(True))
        .group_by(Review.sentiment)
    ).all()
    distribution = _distribution([(s, c) for s, c in sentiment_rows if s is not None])

    per_game = []
    for game in sorted(games, key=lambda g: g.avg_rating, reverse=True):
        game_distribution = _distribution(
            [
                (s, c)
                for s, c in db.execute(
                    select(Review.sentiment, func.count(Review.id))
                    .where(Review.game_id == game.id, Review.is_analyzed.is_(True))
                    .group_by(Review.sentiment)
                ).all()
                if s is not None
            ]
        )
        per_game.append(
            {
                "id": game.id,
                "nombre": game.name,
                "rating_promedio": game.avg_rating,
                "cantidad_resenas": game.reviews_count,
                # Se devuelve el reparto completo, no sólo el neto: con el neto
                # solo, el frontend tendría que reconstruirlo y el resultado
                # sería una aproximación presentada como dato real.
                "distribucion": game_distribution,
                "resenas_analizadas": sum(game_distribution.values()),
                "sentimiento_neto": _net_score(game_distribution),
            }
        )

    return {
        "estudio": studio,
        "juegos": per_game,
        "resenas": {
            "analizadas": sum(distribution.values()),
            "distribucion": distribution,
            "sentimiento_neto": _net_score(distribution),
        },
        "aspectos": aspect_breakdown(db, game_ids),
        "citas_negativas": _evidence_samples(db, game_ids, limit=5),
    }


def platform_overview(db: Session) -> dict:
    """Referencia global del catálogo, para comparar contra un juego o estudio."""
    distribution = _distribution(
        [
            (s, c)
            for s, c in db.execute(
                select(Review.sentiment, func.count(Review.id))
                .where(Review.is_analyzed.is_(True))
                .group_by(Review.sentiment)
            ).all()
            if s is not None
        ]
    )
    all_ids = list(db.scalars(select(Game.id)))
    return {
        "resenas_analizadas": sum(distribution.values()),
        "distribucion": distribution,
        "sentimiento_neto": _net_score(distribution),
        "aspectos": aspect_breakdown(db, all_ids),
    }
