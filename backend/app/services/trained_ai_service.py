"""Inferencia local sobre reseñas reales dentro del informe de desarrollador.

Los temas describen de qué habla un texto, no su polaridad. El voto del autor
se conserva separado: una reseña no recomendada puede elogiar un tema.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db.real_data import real_review_filter
from app.ml.trained_reviews import LABELS, TOPICS, load_model
from app.models import Game, Review

logger = logging.getLogger(__name__)
MAX_REVIEWS = 1500
INFERENCE_BATCH = 100
LIMITATIONS = [
    "La muestra contiene reseñas reales guardadas en español; no representa todas las opiniones de Steam.",
    "Un tema detectado no es una crítica ni demuestra la causa de una recomendación negativa.",
    "Los temas se entrenaron con etiquetas de vocabulario sobre textos reales, no con anotaciones humanas.",
    "La comparación de recepción con votos guardados no es una evaluación independiente del modelo.",
]


def review_model_report(db, *, studio=None, game_id=None, limit=MAX_REVIEWS):
    """Ejecuta el modelo entrenado sin modificar reseñas, votos ni GameTrackScore."""
    model, status = load_model()
    scope = {"type": "game", "game_id": game_id} if game_id is not None else {
        "type": "studio", "studio": (studio or "").strip()
    }
    result = dict(
        model=status, scope=scope, language="es", sample_order="latest_saved",
        sampled=0, total=0, topics=[], reception=dict(compared=0, disagreements=0),
        limitations=list(LIMITATIONS),
    )
    if game_id is None and not scope["studio"]:
        result["scope"] = {"type": "unspecified"}
        return result

    criteria = [real_review_filter(), func.lower(Review.language) == "es",
                func.length(func.trim(Review.content)) > 0]
    if game_id is not None:
        criteria.append(Review.game_id == game_id)
    else:
        criteria.append(func.lower(func.trim(Game.developer)) == scope["studio"].lower())
    result["total"] = db.scalar(
        select(func.count(Review.id)).join(Game).where(*criteria)
    ) or 0
    if model is None:
        return result

    bounded_limit = max(1, min(int(limit), MAX_REVIEWS))
    rows = db.execute(
        select(Review.id, Review.game_id, Review.content, Review.is_recommended,
               Review.source, Game.name)
        .join(Game).where(*criteria).order_by(Review.id.desc()).limit(bounded_limit)
    ).all()
    if not rows:
        return result

    try:
        predictions = []
        for start in range(0, len(rows), INFERENCE_BATCH):
            predictions.extend(model.predict([row.content for row in rows[start:start + INFERENCE_BATCH]]))
        if len(predictions) != len(rows):
            raise ValueError("Cantidad de predicciones incompatible")
    except (ValueError, TypeError, KeyError, IndexError, ArithmeticError):
        logger.exception("No se pudo ejecutar el modelo local de reseñas")
        result["model"] = dict(status="invalid", message="El modelo no pudo analizar esta muestra; sigue disponible el informe por reglas.")
        return result

    result["sampled"] = len(rows)
    topics = {
        topic: dict(topic=topic, label=LABELS[topic], mentions=0, not_recommended=0,
                    recommended=0, without_recommendation=0, examples=[])
        for topic in TOPICS if status.get("topics", {}).get(topic, {}).get("eligible")
    }
    candidates = {topic: [] for topic in topics}
    for row, prediction in zip(rows, predictions):
        reception = prediction.get("reception")
        if reception is not None and row.is_recommended is not None:
            result["reception"]["compared"] += 1
            result["reception"]["disagreements"] += int((reception >= .5) != bool(row.is_recommended))
        for item in prediction.get("topics", []):
            topic = item.get("topic")
            if topic not in topics:
                continue
            counts = topics[topic]
            counts["mentions"] += 1
            vote_field = "without_recommendation" if row.is_recommended is None else (
                "recommended" if row.is_recommended else "not_recommended")
            counts[vote_field] += 1
            # Elegir evidencia de la misma muestra y priorizar votos negativos,
            # sin atribuir una polaridad al fragmento ni inventar una cita.
            current = candidates[topic]
            if len(current) < 3 or (row.is_recommended is False and
                                    any(example["recommended"] is not False for example in current)):
                try:
                    quote = model.evidence(row.content, topic)
                except (ValueError, TypeError, KeyError, IndexError, ArithmeticError):
                    # La predicción ya fue calculada. Si falla la selección de
                    # fragmento, mostrar texto original, nunca generar una cita.
                    logger.warning("No se pudo seleccionar un fragmento del modelo de reseñas")
                    quote = row.content[:320]
                if quote and quote.strip():
                    candidates[topic].append(dict(
                        review_id=row.id, game_id=row.game_id, game=row.name,
                        quote=quote, recommended=row.is_recommended, source=row.source,
                    ))
                    candidates[topic].sort(key=lambda example: example["recommended"] is not False)
                    del candidates[topic][3:]
    for topic, counts in topics.items():
        counts["examples"] = candidates[topic]
    result["topics"] = sorted(
        topics.values(), key=lambda item: (-item["not_recommended"], -item["mentions"], item["topic"])
    )
    return result


def explain_trained_report(report, question):
    """Redacción acotada y trazable de predicciones, sin un chatbot externo."""
    from app.ml.analytics import normalize_text
    query = normalize_text(question)
    requested = next((topic for topic, words in {
        "jugabilidad": ("jugabilidad", "gameplay", "combate"),
        "graficos": ("grafico", "visual", "arte"),
        "historia": ("historia", "narrativa", "guion"),
        "optimizacion": ("rendimiento", "optimizacion", "bugs", "fps"),
        "monetizacion": ("monetiza", "precio", "dlc"),
        "dificultad": ("dificultad", "dificil"),
    }.items() if any(word in query for word in words)), None)
    candidates = [topic for topic in report["topics"] if not requested or topic["topic"] == requested]
    evidence, actions = [], []
    used = report["model"]["status"] == "ready" and report["sampled"] > 0
    if report["model"]["status"] != "ready":
        answer = report["model"].get("message", "El modelo entrenado no está disponible.")
    elif not report["sampled"]:
        answer = "No hay reseñas reales guardadas en español para aplicar el modelo en este informe."
    elif any(word in query for word in ("confianza", "muestra", "fiable", "exact", "suficiente")):
        answer = (f"El modelo analizó {report['sampled']} de {report['total']} reseñas guardadas en español. "
                  "Su validación de entrenamiento y la cobertura de esta muestra son cosas distintas; "
                  "no corresponde presentar coincidencias con estos votos como exactitud independiente.")
        actions = ["Contrastá las predicciones con reseñas originales y con una muestra anotada antes de medir precisión."]
    elif requested is None and any(word in query for word in ("tema", "detecta")):
        detected = sorted((topic for topic in candidates if topic["mentions"]),
                          key=lambda topic: (-topic["mentions"], topic["topic"]))[:3]
        if detected:
            description = "; ".join(f"{topic['label'].lower()}: {topic['mentions']} textos"
                                    for topic in detected)
            answer = (f"En {report['sampled']} reseñas reales el modelo detectó {description}. "
                      "Los temas pueden coincidir en una misma reseña. Estos conteos no indican "
                      "si el autor elogia o critica cada tema.")
            evidence = [dict(aspect=topic["topic"], **topic["examples"][0])
                        for topic in detected if topic["examples"]]
            actions = ["Leé los fragmentos originales para interpretar cada tema en su contexto."]
        else:
            answer = (f"Se analizaron {report['sampled']} reseñas reales, pero ningún tema superó "
                      "su umbral de detección. No corresponde inventar temas para completar el informe.")
    else:
        favorable = any(word in query for word in ("fortaleza", "positivo", "funciona", "conservar", "mejor recibido"))
        field = "recommended" if favorable else "not_recommended"
        enough = [topic for topic in candidates if topic[field] >= 5]
        if not enough:
            vote = "recomendadas" if favorable else "no recomendadas"
            requested_label = LABELS.get(requested, "ese tema") if requested else "un mismo tema"
            answer = (f"El modelo revisó {report['sampled']} reseñas reales, pero no hay al menos cinco "
                      f"reseñas {vote} con {requested_label.lower()} detectado. "
                      "No hay evidencia suficiente para señalar una prioridad basada en el modelo.")
        else:
            selected = max(enough, key=lambda topic: (topic[field], topic["mentions"]))
            vote = "recomendaron" if favorable else "no recomendaron"
            answer = (f"El modelo entrenado detectó {selected['label'].lower()} en {selected['mentions']} "
                      f"de las {report['sampled']} reseñas reales de la muestra. "
                      f"{selected[field]} pertenecen a autores que {vote} el juego. "
                      "Detectar el tema no indica si lo elogian o critican ni demuestra por qué votaron así.")
            evidence = [dict(aspect=selected["topic"], **example) for example in selected["examples"]
                        if example["recommended"] is favorable]
            actions = ["Abrí los textos originales y comprobá qué dicen de este tema antes de decidir cambios."]
    return dict(
        title="Lectura con el modelo entrenado", answer=answer, actions=actions, evidence=evidence,
        basis=f"Modelo local entrenado · {report['sampled']} de {report['total']} reseñas reales en español",
        limitation="Los temas usan supervisión por vocabulario; no son etiquetas humanas. La respuesta se redacta con plantillas locales y no es un chatbot generativo.",
        trained_ai_used=used, model_status=report["model"]["status"],
    )
