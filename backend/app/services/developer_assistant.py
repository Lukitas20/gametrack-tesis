"""Explicaciones de ABSA local: cifras y evidencia, sin servicios de IA externos."""
import unicodedata

LABELS = {'jugabilidad': 'jugabilidad', 'graficos': 'gráficos',
          'historia': 'historia', 'optimizacion': 'optimización'}
MIN_MENTIONS = 5


def explain_developer_report(report: dict, question: str, overview: dict | None = None) -> dict:
    query = ''.join(c for c in unicodedata.normalize('NFD', question.lower())
                    if unicodedata.category(c) != 'Mn')
    summary = report['resenas']
    count = summary['analizadas']
    aspects = [item for item in report.get('aspectos', []) if item['menciones'] > 0]
    reliable = [item for item in aspects if item['menciones'] >= MIN_MENTIONS]
    label = report.get('estudio') or report.get('juego', {}).get('nombre', 'este juego')
    actions, evidence = [], []
    title = 'Lectura del informe'
    answer = f'El informe de {label} reúne {count:,} reseñas analizadas y {summary.get("pendientes", 0):,} pendientes.'
    selected = None
    if not count:
        title = 'Todavía falta evidencia'
        answer += ' No hay una base analizada para identificar fortalezas o problemas.'
        actions.append('Procesá las reseñas guardadas pendientes antes de sacar conclusiones.')
    elif report.get('juegos') and any(word in query for word in ['que juego', 'cual juego', 'que titulo', 'cual titulo']):
        title = 'Qué título necesita más revisión'
        candidates = [game for game in report['juegos'] if game['resenas_analizadas'] >= MIN_MENTIONS
                      and game['sentimiento_neto'] < 0]
        if candidates:
            game = max(candidates, key=lambda item: item['distribucion']['negativo'] ** 2 / item['resenas_analizadas'])
            answer += (f' {game["nombre"]} concentra una señal negativa: '
                       f'{game["distribucion"]["negativo"]} reseñas negativas entre '
                       f'{game["resenas_analizadas"]} analizadas, con neto {game["sentimiento_neto"]:+.2f}.')
            actions.append('Abrí el informe de ese juego y revisá sus aspectos y fragmentos antes de definir cambios.')
        else:
            answer += ' Ningún título muestra un balance negativo con al menos cinco reseñas analizadas.'
    elif any(word in query for word in ['suficiente', 'confianza', 'muestra', 'exact', 'fiable']):
        title = 'Qué tan sólida es la muestra'
        answer += (f' Hay {len(reliable)} aspectos con al menos {MIN_MENTIONS} menciones. '
                   'Ese umbral orienta la lectura; no es una medida de confianza estadística. '
                   'La muestra guardada puede no representar todas las reseñas de Steam.')
        actions.append('Revisá los fragmentos originales y contrastá opiniones antes de decidir cambios.')
    elif any(word in query for word in ['compar', 'catalogo', 'promedio']):
        title = 'Comparación con el catálogo'
        if overview and overview['resenas_analizadas']:
            gap = summary['sentimiento_neto'] - overview['sentimiento_neto']
            answer += (f' Su sentimiento neto es {summary["sentimiento_neto"]:+.2f}, '
                       f'frente a {overview["sentimiento_neto"]:+.2f} del catálogo '
                       f'({gap:+.2f} de diferencia en la escala −1 a +1). '
                       'Las muestras pueden tener tamaños y tipos de juegos distintos.')
        else:
            answer += ' El catálogo todavía no tiene una referencia analizada disponible.'
    elif any(word in query for word in ['funciona', 'fuerte', 'mejor recibido', 'fortaleza', 'positivo']):
        title = 'Qué conviene conservar'
        favorable = [item for item in reliable if item['sentimiento_neto'] > 0]
        if favorable:
            selected = max(favorable, key=lambda item: item['sentimiento_neto'])
            answer += (f' {LABELS.get(selected["aspecto"], selected["aspecto"]).capitalize()} '
                       f'tiene la mejor recepción con evidencia suficiente: '
                       f'neto {selected["sentimiento_neto"]:+.2f} sobre {selected["menciones"]} menciones.')
            actions.append('Conservá esa fortaleza y verificá que los próximos cambios no la empeoren.')
        else:
            answer += ' Ningún aspecto tiene un balance positivo con al menos cinco menciones; no sería correcto señalar una fortaleza clara.'
    else:
        title = 'Qué revisar primero'
        requested = next((key for key, words in {
            'jugabilidad': ['jugabilidad', 'gameplay', 'combate'],
            'graficos': ['graficos', 'visual', 'arte'],
            'historia': ['historia', 'narrativa', 'guion'],
            'optimizacion': ['optimizacion', 'rendimiento', 'bugs', 'fps'],
        }.items() if any(word in query for word in words)), None)
        candidates = [item for item in reliable if not requested or item['aspecto'] == requested]
        negative = [item for item in candidates if item['sentimiento_neto'] < 0]
        if requested and not candidates:
            answer += f' No hay suficientes menciones de {LABELS[requested]} para una recomendación sólida.'
        elif negative:
            selected = max(negative, key=lambda item: item['distribucion']['negativo'] *
                           item['distribucion']['negativo'] / item['menciones'])
            negative_count = selected['distribucion']['negativo']
            share = round(100 * negative_count / selected['menciones'])
            aspect = LABELS.get(selected['aspecto'], selected['aspecto'])
            answer += (f' Conviene revisar {aspect}: {negative_count} de {selected["menciones"]} '
                       f'menciones son negativas ({share}%), con neto {selected["sentimiento_neto"]:+.2f}. '
                       'La prioridad combina cantidad y proporción de menciones negativas; no prueba una causa técnica.')
            actions.append({'optimizacion': 'Contrastá los reportes de rendimiento con pruebas de FPS, estabilidad y equipos afectados.',
                            'jugabilidad': 'Revisá controles, dificultad y ritmo con pruebas de juego.',
                            'historia': 'Revisá ritmo narrativo, diálogos y claridad de objetivos.',
                            'graficos': 'Revisá legibilidad, dirección visual y opciones gráficas.'}.get(selected['aspecto'], 'Revisá los reportes del aspecto señalado.'))
        else:
            answer += ' No aparece un aspecto con balance negativo y al menos cinco menciones. El aspecto menos valorado no necesariamente es un problema.'
            actions.append('Buscá problemas puntuales en los fragmentos, sin convertir un balance positivo en una alerta.')
        if not any(word in query for word in ['mejor', 'prior', 'revis', 'problema', 'atenc', 'debil', 'juego']) and not requested:
            answer += ' Puedo explicar prioridades, fortalezas, aspectos, comparación y tamaño de muestra; esta respuesta no es una conversación generativa.'
    if selected and selected['sentimiento_neto'] < 0:
        evidence = [{'aspect': selected['aspecto'], 'quote': quote}
                    for quote in report.get('citas_negativas', {}).get(selected['aspecto'], [])[:3]]
        if not evidence:
            actions.append('Este agregado no tiene fragmentos negativos disponibles en el informe; revisá las reseñas originales.')
    return {'title': title, 'answer': answer, 'actions': actions, 'evidence': evidence,
            'basis': f'{label} · {count:,} reseñas · análisis local de sentimiento y aspectos',
            'limitation': 'Las clasificaciones son automáticas y pueden fallar con ironía, contexto o lenguaje ambiguo. No equivalen a Metacritic ni a una auditoría técnica.'}
