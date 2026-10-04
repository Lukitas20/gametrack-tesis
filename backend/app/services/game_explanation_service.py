"""Explicaciones locales del recomendador: evidencia, límites y preguntas.

No es un LLM ni llama a una IA externa. Interpreta el mismo perfil del
GameTrackScore, usa similitudes del motor local y responde por intención con
hechos verificables. No modifica las preferencias ni la puntuación.
"""
import re
import unicodedata

from fastapi import HTTPException
from app.models import Game
from app.schemas.gametrack_score import GameExplanation, ExplanationPoint, ExplanationReference
from app.services.friendship_service import require_player
from app.services.gametrack_score_service import public_evidence, score_context, score_game
from app.services.personal_history_service import history_reasons


def _plain(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def explain_game(db, user, game_id, question=None, _context=None):
    require_player(user)
    if question is not None and not question.strip():
        raise HTTPException(422, "Escribí una pregunta sobre el juego")
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404, "El juego no existe")
    context = _context if _context is not None else score_context(db, user)
    score = score_game(game, context)
    public = public_evidence(game)
    references = [ExplanationReference(id="game", title="Ficha de GameTrack", url=f"#/juego/{game.id}",
        detail="Géneros, etiquetas y datos importados del catálogo; no es una reseña crítica completa."),
        ExplanationReference(id="profile", title="Tu historial y gustos", url="#/perfil",
        detail=f"Señales comparables: {context['rating_count']} notas y {context.get('gametrack_review_count',0)} reseñas de GameTrack, {context['steam_review_count']} reseñas propias de Steam y {context['steam_played_count']} juegos con horas. Sólo se consulta tu propia cuenta.")]
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
    if common and not context["has_history"]:
        positives.append(point("Coincidencia amplia con tus géneros: " + ", ".join(common) + ". Es una señal provisional; dos juegos del mismo género pueden sentirse distintos.", "profile", "game"))
    elif context["genres"] and not context["has_history"]:
        cautions.append(point("No coincide directamente con los géneros que elegiste. Si hay afinidad, viene de otros rasgos y de tus valoraciones.", "profile", "game"))

    # Usa exactamente las comparaciones y fuentes que alimentan el puntaje.
    for _, gid, negative, text in history_reasons(game, context):
        previous, signal = context["history_games"][gid], context["signals"][gid]
        ref = f"history-{gid}"
        url = f"#/juego/{gid}"
        if signal["source"] == "steam_review":
            url = f"https://steamcommunity.com/profiles/{user.steam_id}/recommended/{signal['appid']}/"
        references.append(ExplanationReference(id=ref, title=f"Tu historial de {previous.name}", url=url,
            detail=text + " La similitud de contenido no garantiza la misma experiencia."))
        (cautions if negative else positives).append(point(text, ref, "game"))
    if context["steam_count"]:
        positives.append(point(f"Usamos {context['steam_review_count']} reseñas tuyas de Steam y {context['steam_played_count']} juegos con horas comparables. Las opiniones negativas también reducen la afinidad.", "profile"))
        if context["steam_played_count"]:
            cautions.append(point("Las horas muestran interés, pero no prueban que un juego te haya gustado. Las reseñas propias tienen más peso; comprar o poseer un juego no cuenta como una opinión positiva.", "profile"))

    own_rating = context["ratings"].get(game.id)
    own_signal = context.get("own_signals", context["signals"]).get(game.id)
    if own_rating is None and own_signal and own_signal["source"] == "steam_playtime":
        positives.insert(0, point(score.reasons[0], "profile"))
    elif own_rating is None and own_signal and own_signal["source"] == "play_feedback":
        (positives if own_signal["weight"] > 0 else cautions).insert(0, point(score.reasons[0], "profile"))
    if own_rating is None and own_signal and own_signal["source"] == "gametrack_review":
        (positives if own_signal["recommended"] else cautions).insert(0, point(score.reasons[0], "profile", "game"))
    if own_rating is None and own_signal and own_signal["source"] == "steam_review":
        recommended = own_signal["recommended"]
        (positives if recommended else cautions).insert(0, point(
            ("Ya lo recomendaste en Steam." if recommended else "Ya lo marcaste como no recomendado en Steam.")
            + " Usamos esa opinión como señal explícita; no la convertimos en estrellas de GameTrack.", "profile"))
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
        cautions.append(point("La explicación es provisional: hay pocas opiniones o juegos con rasgos comparables. Los géneros amplios solos no describen con precisión tus gustos.", "profile"))
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
        confidence_question = bool(re.search(r"confianza|confiab|segur|certeza|falt.*dato|evidencia",q))
        if confidence_question:
            selected.append(point(score.confidence_message, "profile", "game"))
        # Consultas acotadas al dominio. El texto de la pregunta no se ejecuta,
        # no cambia el perfil ni puede introducir nuevas fuentes o instrucciones.
        if re.search(r"no.*gust|contra|riesgo|duda|negativ|rechaz|desventaj", q):
            selected.extend(cautions)
        elif not confidence_question and re.search(r"gust|afinidad|recomend|encaj|favor", q):
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
            selected.extend(comparisons or [point("No hay juegos de tu historial con suficientes rasgos específicos compartidos para una comparación fiable. No tomo las valoraciones de otras cuentas como si fueran tuyas.", "profile", "game")])
        if re.search(r"genero|modalidad|multijugador|cooperativ|amigo|solo|mecanica|juega", q):
            features = [g.name for g in game.genres] + [t.name for t in game.tags[:12]]
            selected.append(point("La ficha registra: " + (", ".join(features) if features else "todavía no hay géneros o modalidades suficientes") + ". Las etiquetas describen rasgos; no garantizan que puedas compartir partida con cualquier plataforma o amigo.", "game", *(["steam"] if game.steam_app_id else [])))
        if re.search(r"horas|duracion|cuanto dura|tiempo|sesion", q):
            if own_signal and "minutes" in own_signal:
                selected.append(point(f"Tu biblioteca sincronizada registra {own_signal['minutes'] / 60:.1f} h en este juego. El interés por horas crece de forma logarítmica hasta 1500 h; una opinión explícita propia prevalece, aunque tengas muchas horas.", "profile"))
            selected.append(point("En juegos nuevos comparamos rasgos específicos con los juegos que más jugás: cientos de horas en un shooter competitivo pueden influir aunque también hayas recomendado un juego de otro género. El interés por horas no crea valoraciones ni anula tus rechazos.", "profile", "game"))
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
        suggested_questions=["¿Por qué podría gustarme?", "¿Qué podría no gustarme?", "Comparalo con mi historial", "¿Cómo se calcula mi puntaje?", "¿Qué tan confiable es esta recomendación?"],
        method="Análisis local del mismo perfil y señales del GameTrackScore. Las comparaciones usan el motor de contenido; las respuestas se construyen con evidencia del catálogo y tu historial. No es un chat generativo de propósito general.")
