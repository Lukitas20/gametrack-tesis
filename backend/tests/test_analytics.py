"""Pruebas del módulo NLP: sentimiento y ABSA."""

import pytest

from app.ml.analytics import analyze_text, detect_aspects, score_polarity, tokenize
from app.models import Aspect, Sentiment


def opinion_for(text: str, aspect: Aspect):
    return next((o for o in analyze_text(text).aspects if o.aspect is aspect), None)


# --- Sentimiento global ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Una obra maestra, la historia es magnífica.", Sentiment.POSITIVE),
        ("El rendimiento es un desastre y crashea todo el tiempo.", Sentiment.NEGATIVE),
        ("La jugabilidad es correcta, cumple sin destacar.", Sentiment.NEUTRAL),
    ],
)
def test_sentimiento_global(text: str, expected: Sentiment) -> None:
    assert analyze_text(text).sentiment is expected


def test_texto_vacio_no_rompe() -> None:
    analysis = analyze_text("   ")
    assert analysis.sentiment is Sentiment.NEUTRAL
    assert analysis.aspects == []


# --- Negación e intensificadores -------------------------------------------


def test_negacion_invierte_la_polaridad() -> None:
    positivo, _ = score_polarity(tokenize("el juego es bueno"))
    negado, _ = score_polarity(tokenize("el juego no es bueno"))
    assert positivo > 0 > negado


def test_no_encontre_un_solo_bug_es_positivo() -> None:
    # "bug" es negativo, pero la negación previa lo invierte.
    score, _ = score_polarity(tokenize("no encontre un solo bug en toda la campana"))
    assert score > 0


def test_sin_embargo_no_cuenta_como_negacion() -> None:
    # "sin" negaría; "sin embargo" es un conector y debe ignorarse.
    score, _ = score_polarity(tokenize("sin embargo el combate es excelente"))
    assert score > 0


def test_intensificador_posterior_amplifica() -> None:
    # En español el adjetivo va detrás: "decepción enorme" debe ser más
    # negativo que "decepción" a secas.
    simple, _ = score_polarity(tokenize("fue una decepcion"))
    intensificado, _ = score_polarity(tokenize("fue una decepcion enorme"))
    assert intensificado < simple


# --- Detección de aspectos -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("el combate es preciso", Aspect.GAMEPLAY),
        ("la direccion artistica es deslumbrante", Aspect.GRAPHICS),
        ("el guion es magnifico", Aspect.STORY),
        ("el rendimiento es pesimo", Aspect.PERFORMANCE),
    ],
)
def test_deteccion_de_aspectos(text: str, expected: Aspect) -> None:
    assert detect_aspects(tokenize(text)) == [expected]


def test_termino_debil_cede_ante_uno_fuerte() -> None:
    # "funcionan" apunta a optimización pero "mecánicas" es un término fuerte
    # de jugabilidad: la cláusula habla de jugabilidad.
    assert detect_aspects(tokenize("las mecanicas funcionan bien")) == [Aspect.GAMEPLAY]


def test_termino_debil_se_usa_si_no_hay_ninguno_fuerte() -> None:
    assert detect_aspects(tokenize("corre bien en mi maquina")) == [Aspect.PERFORMANCE]


def test_misiones_secundarias_es_historia_no_jugabilidad() -> None:
    # La coincidencia más larga desambigua contra "diseño de misiones".
    assert detect_aspects(tokenize("las misiones secundarias son geniales")) == [Aspect.STORY]
    assert detect_aspects(tokenize("el diseno de misiones es perezoso")) == [Aspect.GAMEPLAY]


def test_diseno_de_personajes_es_graficos() -> None:
    assert detect_aspects(tokenize("el diseno de personajes es hermoso")) == [Aspect.GRAPHICS]


# --- ABSA completo ---------------------------------------------------------


def test_separa_opiniones_opuestas_en_el_mismo_texto() -> None:
    text = (
        "La historia es magnífica y los personajes están muy bien escritos, "
        "pero el rendimiento es un desastre: crashea cada media hora."
    )
    analysis = analyze_text(text)

    story = opinion_for(text, Aspect.STORY)
    performance = opinion_for(text, Aspect.PERFORMANCE)
    assert story is not None and story.sentiment is Sentiment.POSITIVE
    assert performance is not None and performance.sentiment is Sentiment.NEGATIVE
    assert analysis.aspects


def test_conector_adversativo_hereda_el_aspecto_anterior() -> None:
    # La segunda cláusula no nombra ningún aspecto: la opinión sigue siendo
    # sobre el gameplay.
    text = "El gameplay es correcto aunque le falta profundidad."
    opinion = opinion_for(text, Aspect.GAMEPLAY)
    assert opinion is not None
    assert opinion.sentiment is Sentiment.NEUTRAL


def test_mencionar_un_aspecto_sin_opinar_no_genera_resultado() -> None:
    # Menciona los gráficos pero no dice nada valorativo sobre ellos.
    assert opinion_for("El juego tiene graficos en 3D.", Aspect.GRAPHICS) is None


def test_guarda_la_evidencia_textual() -> None:
    text = "Corre impecable. Los graficos son pobres."
    opinion = opinion_for(text, Aspect.GRAPHICS)
    assert opinion is not None
    assert "pobres" in opinion.evidence.lower()


def test_resumen_global_no_materializa_catalogo_como_lista_de_parametros():
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db.base import Base
    from app.models import Game
    from app.ml.analytics import platform_overview
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Game(slug=f'juego-{index}', name=f'Juego {index}') for index in range(80)])
        db.commit()
        raw = db.connection().connection.driver_connection
        previous = raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 50)
        try:
            assert platform_overview(db)['aspectos'] == []
        finally:
            raw.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous)


def test_asistente_no_convierte_aspecto_positivo_en_alerta():
    from app.services.developer_assistant import explain_developer_report
    report = {'estudio':'Test','resenas':{'analizadas':20,'pendientes':0},
              'aspectos':[{'aspecto':'jugabilidad','menciones':10,'sentimiento_neto':0.2,
                           'distribucion':{'positivo':6,'neutro':0,'negativo':4}}]}
    response = explain_developer_report(report, 'Qué conviene mejorar')
    assert 'No aparece un aspecto con balance negativo' in response['answer']
    assert response['evidence'] == []


def test_asistente_prioriza_evidencia_real_y_admite_muestra_insuficiente():
    from app.services.developer_assistant import explain_developer_report
    report = {'estudio':'Test','resenas':{'analizadas':20,'pendientes':2},
              'aspectos':[{'aspecto':'optimizacion','menciones':10,'sentimiento_neto':-0.6,
                           'distribucion':{'positivo':2,'neutro':0,'negativo':8}}],
              'citas_negativas':{'optimizacion':['El rendimiento es pésimo.']}}
    response = explain_developer_report(report, 'Qué conviene mejorar')
    assert '8 de 10' in response['answer'] and '80%' in response['answer']
    assert response['evidence'] == [{'aspect':'optimizacion','quote':'El rendimiento es pésimo.'}]
    report['aspectos'][0]['menciones'] = 2
    response = explain_developer_report(report, 'Qué revisar de la optimización')
    assert 'No hay suficientes menciones' in response['answer']
    assert response['evidence'] == []


def test_asistente_muestra_vacia_no_inventa_conclusiones():
    from app.services.developer_assistant import explain_developer_report
    report = {'estudio':'Test','resenas':{'analizadas':0,'pendientes':3},'aspectos':[]}
    response = explain_developer_report(report, 'Qué funciona mejor')
    assert response['title'] == 'Todavía falta evidencia'
    assert response['evidence'] == []
