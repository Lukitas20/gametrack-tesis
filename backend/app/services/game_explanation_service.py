"""Explicaciones locales del recomendador: evidencia, límites y preguntas.

No es un LLM ni llama a una IA externa. Interpreta el mismo perfil del
GameTrackScore, usa similitudes del motor local y responde por intención con
hechos verificables. No modifica las preferencias ni la puntuación.
"""
import re
import unicodedata

from fastapi import HTTPException
from sqlalchemy import select

from app.ml.recommender import get_engine
from app.models import Game
from app.schemas.gametrack_score import GameExplanation, ExplanationPoint, ExplanationReference
from app.services.friendship_service import require_player
from app.services.gametrack_score_service import public_evidence, score_context, score_game


def _plain(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def explain_game(db, user, game_id, question=None):
    require_player(user)
    if question is not None and not question.strip():
        raise HTTPException(422, "Escribí una pregunta sobre el juego")
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404, "El juego no existe")
    context = score_context(db, user)
    score = score_game(game, context)
    public = public_evidence(game)
    references = [ExplanationReference(id="game", title="Ficha de GameTrack", url=f"#/juego/{game.id}",
        detail="Géneros, etiquetas y datos importados del catálogo; no es una reseña crítica completa."),
        ExplanationReference(id="profile", title="Tus gustos y valoraciones", url="#/perfil",
        detail=f"Tu perfil actual: {context['history_size']} valoraciones. Sólo se consulta tu propia cuenta.")]
    review_ref = "game"
    if game.steam_app_id:
        references.append(ExplanationReference(id="steam", title="Ficha oficial en Steam",
            url=f"https://store.steampowered.com/app/{game.steam_app_id}/",
            detail="Descripción, modalidades y requisitos publicados por el desarrollador."))
        review_ref = "reviews"
        if public["source"] == "Steam":
            url = f"https://store.steampowered.com/app/{game.steam_app_id}/#app_reviews_hash"
        else:
            url = f"https://steamspy.com/app/{game.steam_app_id}"
        references.append(ExplanationReference(id=review_ref, title=public["source"] + ": reseñas públicas",
            url=url, detail="Totales agregados importados. La valoración pública no garantiza tu experiencia personal."))
    positives, cautions = [], []

    def point(text, *ids):
        return ExplanationPoint(text=text, reference_ids=list(ids))

    common = [g.name for g in game.genres if g.slug in context["genres"]]
    if common:
        positives.append(point("Encaja con géneros que elegiste: " + ", ".join(common) + ". Es una señal a favor, aunque dos juegos del mismo género pueden sentirse distintos.", "profile", "game"))
    elif context["genres"]:
        cautions.append(point("No coincide directamente con los géneros que elegiste. Si hay afinidad, viene de otros rasgos y de tus valoraciones.", "profile", "game"))

    # Referencias contrastables a juegos realmente valorados por ESTA persona.
    history = db.scalars(select(Game).where(Game.id.in_(context["ratings"]))).all() if context["ratings"] else []
    engine = get_engine(db)
    index = engine._game_index.get(game.id)
    similarities = engine._similarity_row(index) if index is not None else None
    compared = []
    if similarities is not None:
        for previous in history:
            position = engine._game_index.get(previous.id)
            if position is None or previous.id == game.id:
                continue
            similarity = float(similarities[position])
            rating = context["ratings"][previous.id]
            if similarity < .12 or (2 < rating < 4):
                continue
            shared = [g.name for g in previous.genres if g.slug in {v.slug for v in game.genres}]
            shared += [t.name for t in previous.tags if t.slug in {v.slug for v in game.tags} and t.kind == "community"]
            if not shared:
                continue
            compared.append((similarity, previous, rating, shared[:3]))
    compared.sort(key=lambda item: (-item[0], item[1].id))
    used = {"positive": 0, "negative": 0}
    for _, previous, rating, shared in compared:
        direction = "positive" if rating >= 4 else "negative"
        if used[direction] >= 2:
            continue
        used[direction] += 1
        ref = f"history-{previous.id}"
        references.append(ExplanationReference(id=ref, title=f"Tu valoración de {previous.name}",
            url=f"#/juego/{previous.id}", detail=f"Lo valoraste con {rating:g}/5. La comparación es por rasgos de contenido, no por una experiencia idéntica."))
        text = f"Comparte {', '.join(shared)} con {previous.name}, que valoraste {rating:g}/5. "
        text += "Esa similitud suma evidencia a favor." if rating >= 4 else "Puede ser una señal de desencaje; compartir rasgos no demuestra que vayas a rechazarlo."
        (positives if rating >= 4 else cautions).append(point(text, ref, "game"))

    own_rating = context["ratings"].get(game.id)
    if own_rating is not None:
        (positives if own_rating >= 4 else cautions).insert(0, point(
            f"Ya lo valoraste {own_rating:g}/5. Tu GameTrackScore de {score.value}/100 refleja esa opinión; no es una nueva predicción.", "profile"))
    elif score.affinity is not None and score.affinity < 45:
        cautions.append(point(f"La afinidad con tu perfil es baja ({score.affinity}/100). Una buena crítica no alcanza para convertirlo en una recomendación personal.", "profile", "game"))

    if public["meta"] is not None:
        target = positives if public["meta"] >= 70 else cautions
        target.append(point(f"El catálogo registra un Metascore de {public['meta']}/100. Resume recepción crítica, no cuánto te va a gustar a vos. No tenemos el texto completo de esas críticas.", "game"))
    else:
        cautions.append(point("No hay Metascore disponible en esta ficha. No se inventa una nota ni una opinión de Metacritic.", "game"))
    if public["community"] is not None:
        percent = round(public["community"] * 100)
        count = f"{public['total']:,}".replace(",", ".")
        text = f"Hay {count} reseñas públicas. El indicador conservador de recepción positiva es {percent}/100; incluye un ajuste por tamaño de muestra, no es el porcentaje bruto de Steam."
        (positives if public["community"] >= .80 and public["total"] >= 1000 else cautions).append(point(text, review_ref))
    else:
        cautions.append(point("Faltan reseñas públicas verificables para contrastar la recepción del juego.", "game"))
    if score.evidence in {"sin_datos", "inicial", "en_desarrollo"}:
        cautions.append(point("La explicación es provisional: con pocas valoraciones sabemos menos sobre tus gustos. Valorar juegos que te gustaron y que no te gustaron mejora el perfil.", "profile"))
    if not game.is_enriched:
        cautions.append(point("La ficha todavía está incompleta. No alcanza para describir con confianza su experiencia de juego.", "game"))
    if not cautions:
        cautions.append(point("No encontramos señales claras en contra con los datos actuales. Eso no garantiza que te guste: la coincidencia de rasgos y la buena recepción no reemplazan tu experiencia al jugar.", "profile", "game"))

    if score.value is None:
        summary = "Todavía no hay datos suficientes de tu perfil para estimar si te va a gustar. Podemos contrastar la ficha y su recepción pública."
    elif own_rating is not None:
        summary = f"Tu GameTrackScore es {score.value}/100 porque ya diste tu opinión sobre este juego."
    else:
        fit = "buena" if score.affinity is not None and score.affinity >= 65 else "moderada" if score.affinity is not None and score.affinity >= 45 else "baja"
        summary = f"El modelo encuentra una afinidad {fit} con tu perfil. El GameTrackScore es {score.value}/100 al combinar tus gustos y el respaldo público disponible; no representa una probabilidad de que te guste."

    answer = [point(summary, "profile", "game")]
    if question:
        q = _plain(question)
        selected = []
        # Consultas acotadas al dominio. El texto de la pregunta no se ejecuta,
        # no cambia el perfil ni puede introducir nuevas fuentes o instrucciones.
        if re.search(r"no.*gust|contra|riesgo|duda|negativ|rechaz|desventaj", q):
            selected.extend(cautions)
        elif re.search(r"gust|afinidad|recomend|encaj|favor", q):
            selected.extend(positives + cautions[:2])
        if re.search(r"critica|metacritic|metascore|resena|referencia|fuente|opina|comunidad", q):
            selected.extend(p for p in positives + cautions if "Metascore" in p.text or "reseñas" in p.text)
        if re.search(r"puntaje|score|calcula|peso|desglos|numero|nota", q):
            labels = {"affinity": "tus gustos", "metacritic": "Metacritic", "community": "reseñas públicas", "reach": "respaldo público", "own_rating": "tu propia valoración"}
            for key, weight in score.weights.items():
                selected.append(point(f"{labels.get(key, key).capitalize()}: {round(score.components[key]*100)}/100, con un peso de {round(weight*100)}% del índice.", "profile" if key in {"affinity", "own_rating"} else "game" if key == "metacritic" else review_ref))
            if not score.weights:
                selected.append(point("Sin gustos reconocibles o valoraciones no calculamos un puntaje personal.", "profile"))
        if re.search(r"compara|parecid|similar|historial|jugue|valore", q):
            comparisons = [p for p in positives + cautions if any(ref.startswith("history-") for ref in p.reference_ids)]
            selected.extend(comparisons or [point("No hay juegos valorados con suficientes rasgos compartidos para una comparación fiable. No tomo las valoraciones de otras cuentas como si fueran tuyas.", "profile", "game")])
        if re.search(r"genero|modalidad|multijugador|cooperativ|amigo|solo|mecanica|juega", q):
            features = [g.name for g in game.genres] + [t.name for t in game.tags[:12]]
            selected.append(point("La ficha registra: " + (", ".join(features) if features else "todavía no hay géneros o modalidades suficientes") + ". Las etiquetas describen rasgos; no garantizan que puedas compartir partida con cualquier plataforma o amigo.", "game", *(["steam"] if game.steam_app_id else [])))
        if re.search(r"horas|duracion|cuanto dura|tiempo|sesion", q):
            if game.median_review_hours is not None:
                text = f"La mediana importada de horas acumuladas por reseñadores es {game.median_review_hours:g} h. No mide la duración de la campaña ni cuánto vas a tardar vos."
            else:
                text = "No hay una duración fiable de campaña en estos datos. No puedo estimar cuántas horas vas a necesitar para completarlo."
            selected.append(point(text, "game"))
        if re.search(r"precio|cuesta|oferta|requisit|pc|fps|rendimiento", q):
            selected.append(point("No guardamos precios ni pruebas de rendimiento actuales. Consultá los requisitos y precios en la ficha oficial; no puedo asegurar cómo va a correr en tu PC.", *(["steam"] if game.steam_app_id else ["game"])))
        if not selected:
            selected.append(point("Puedo explicar tu afinidad, las posibles dudas, el cálculo y las referencias disponibles de este juego. Con los datos actuales no puedo responder esa pregunta con confianza. Probá: «¿Qué podría no gustarme?» o «Comparalo con mi historial».", "profile", "game"))
        answer = list({p.text: p for p in selected}.values())[:8]

    available = {ref for p in positives + cautions + answer for ref in p.reference_ids}
    references = [ref for ref in references if ref.id in available]
    return GameExplanation(game_id=game.id, game_name=game.name, score=score, summary=summary, positives=positives, cautions=cautions,
        answer=answer, references=references,
        suggested_questions=["¿Por qué podría gustarme?", "¿Qué podría no gustarme?", "Comparalo con mi historial", "¿Cómo se calcula mi puntaje?"],
        method="Análisis local del mismo perfil y señales del GameTrackScore. Las comparaciones usan el motor de contenido; las respuestas se construyen con evidencia del catálogo y tu historial. No es un chat generativo de propósito general.")
