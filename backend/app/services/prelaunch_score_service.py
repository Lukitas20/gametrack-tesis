"""Afinidad provisional para juegos anunciados. Sin notas críticas ni IA externa.

Se proyectan rasgos públicos en el TF-IDF local, conservando las penalizaciones
de historial. Las etiquetas de un juego anunciado no tienen votos comparables:
se usan por presencia y el índice se limita a 75 (55 con géneros solos).
Estos límites son heurísticos, no intervalos de confianza ni probabilidades.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import threading
import time

from fastapi import HTTPException
import httpx
import numpy as np
from scipy.sparse import lil_matrix
from sklearn.preprocessing import normalize

from app.schemas.gametrack_score import GameExplanation, GameTrackScore
from app.services import home_release_service as releases
from app.services.gametrack_score_service import score_context
from app.services.personal_history_service import combine_history_affinities
from app.services.steam_service import GENRE_TRANSLATIONS, slugify

_cache = {}
_lock = threading.Lock()
TTL = 3600


class StoreTags(HTMLParser):
    """Sólo etiquetas públicas app_tag; ignora la descripción comercial."""
    def __init__(self):
        super().__init__()
        self.tags, self.capture, self.text = [], False, []

    def handle_starttag(self, tag, attrs):
        if tag == 'a' and 'app_tag' in dict(attrs).get('class', '').split():
            self.capture, self.text = True, []

    def handle_data(self, text):
        if self.capture:
            self.text.append(text)

    def handle_endtag(self, tag):
        if tag == 'a' and self.capture:
            name = ' '.join(''.join(self.text).split())[:80]
            if name and name not in self.tags and len(self.tags) < 20:
                self.tags.append(name)
            self.capture = False


def announced_game(appid):
    """Caché acotada de datos públicos; nunca guarda perfiles ni puntajes."""
    now = time.time()
    with _lock:
        cached = _cache.get(appid)
        if cached and now - cached['cached_at'] < TTL:
            return cached
    try:
        response = releases._http_client().get('https://store.steampowered.com/api/appdetails',
            params={'appids': appid, 'l': 'english', 'cc': 'ar'}, timeout=5)
        response.raise_for_status()
        envelope = response.json().get(str(appid), {})
        data = envelope.get('data')
        if envelope.get('success') is not True or not isinstance(data, dict) or data.get('type') != 'game':
            raise HTTPException(404, 'Steam no tiene una ficha de juego verificable para ese enlace.')
        if (data.get('release_date') or {}).get('coming_soon') is not True:
            raise HTTPException(409, 'Este juego ya está disponible. Buscalo en el catálogo para consultar su GameTrackScore habitual.')
        name = data.get('name')
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(502, 'Steam devolvió una ficha incompleta. Intentá nuevamente.')
        genres = [GENRE_TRANSLATIONS.get(item['description'].lower(), item['description'])
                  for item in (data.get('genres') or []) if isinstance(item, dict) and isinstance(item.get('description'), str)]
        tags, tags_available = [], False
        # Las etiquetas de Steam usan slugs ingleses en el modelo existente.
        try:
            page = releases._http_client().get(f'https://store.steampowered.com/app/{appid}/',
                params={'l': 'english', 'cc': 'ar'}, timeout=5)
            page.raise_for_status()
            parser = StoreTags(); parser.feed(page.text[:1500000])
            tags, tags_available = parser.tags, bool(parser.tags)
        except (httpx.HTTPError, ValueError):
            pass  # Con géneros solos, la respuesta declara menor respaldo.
        label = (data.get('release_date') or {}).get('date')
        result = {'appid': appid, 'name': name[:200], 'genres': genres[:15], 'tags': tags,
                  'tags_available': tags_available, 'release_label': label[:100] if isinstance(label, str) and label.strip() else 'Fecha por anunciar',
                  'image': releases._image(data.get('header_image')), 'cached_at': now}
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as error:
        raise HTTPException(503, 'No pudimos verificar el anuncio en Steam. Volvé a intentar en unos minutos.') from error
    with _lock:
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[appid] = result
    return result


def preview_similarities(engine, genres, tags, history_ids):
    if not engine._term_index or not history_ids:
        return {}, set()
    specific = getattr(engine, 'personal_specific_terms', set())
    query = lil_matrix((1, len(engine._term_index)))
    known = set()
    for term in genres | tags:
        column = engine._term_index.get(term)
        if column is not None:
            known.add(term)
            query[0, column] = engine.config.genre_tf if term in genres else 1.
    if not known or not query.nnz:
        return {}, known
    weights = np.array([1. if term in specific else .15 for term in engine._term_index])
    candidate = normalize(engine._transformer.transform(query.tocsr()).multiply(weights).tocsr())
    result = {}
    for gid in history_ids:
        index = engine._game_index.get(gid)
        if index is None:
            continue
        previous = normalize(engine._tfidf[index].multiply(weights).tocsr())
        similarity = float((candidate @ previous.T).toarray()[0, 0])
        shared = known & engine._community_terms_by_game.get(gid, set()) & specific
        result[gid] = np.array([min(similarity, 1. if shared else .45)])
    return result, known


def build_preview(announced, context, question=None):
    genres = {slugify(name) for name in announced['genres']}
    tags = {slugify(name) for name in announced['tags']}
    similarities, known = preview_similarities(context['engine'], genres, tags, context['signals'])
    specific = known & getattr(context['engine'], 'personal_specific_terms', set())
    positive, cautions, reasons, references = [], [], [], []
    def ref(rid, title, url, detail):
        references.append({'id':rid, 'title':title, 'url':url, 'detail':detail})
    def point(text, *ids):
        return {'text':text, 'reference_ids':list(ids)}
    store_url = f"https://store.steampowered.com/app/{announced['appid']}/"
    checked = datetime.fromtimestamp(announced['cached_at'], timezone.utc).strftime('%d/%m/%Y %H:%M UTC')
    ref('announcement', 'Anuncio en Steam', store_url, f"Fecha anunciada: {announced['release_label']} · consulta {checked}. Géneros y etiquetas públicos; pueden cambiar.")
    ref('profile', 'Mi perfil', '#/perfil', 'Tus valoraciones y tu historial sincronizado. Las horas indican interés, no satisfacción.')
    comparisons = []
    for gid, similarity in similarities.items():
        previous, signal = context['history_games'].get(gid), context['signals'][gid]
        if previous is None or similarity[0] < .12 or not signal['weight']:
            continue
        shared = (genres & {genre.slug for genre in previous.genres}) | (tags & context['engine']._community_terms_by_game.get(gid, set()))
        if not shared:
            continue
        comparisons.append((float(similarity[0]) * abs(signal['weight']), gid, signal, previous, sorted(shared)))
    comparisons.sort(key=lambda item:(-item[0], item[1]))
    value = None
    if comparisons:
        values, _, _ = combine_history_affinities(similarities, context['signals'], 1)
        if values is not None:
            value = min(75 if specific else 55, round(float(values[0])*100))
            reasons.append('El puntaje está limitado a 75/100 con rasgos específicos, o a 55/100 con rasgos amplios: todavía no existe una experiencia final para validar la afinidad.')
    elif context['genres'] and genres:
        overlap = genres & context['genres']
        value = round(25 + 30 * len(overlap) / len(genres))
        reasons.append('Sólo podemos comparar géneros amplios: el puntaje no supera 55/100.')
        if overlap:
            positive.append(point('Coincide con los géneros de tu perfil: ' + ', '.join(sorted(overlap)) + '. Es una coincidencia amplia, no una experiencia confirmada.', 'announcement','profile'))
        else:
            cautions.append(point('Sus géneros anunciados no coinciden con los que elegiste; eso no demuestra que vaya a disgustarte.', 'announcement','profile'))
    elif context['personal']:
        reasons.append('Faltan características comparables con tu historial. No asignamos un número por popularidad ni por el nombre del estudio.')
    else:
        reasons.append('Completá tus gustos o valorá juegos para obtener una estimación personal.')
    selected = [item for item in comparisons if item[2]['weight'] > 0][:2] + [item for item in comparisons if item[2]['weight'] < 0][:2]
    names = {slugify(name): name for name in announced['genres'] + announced['tags']}
    for _, gid, signal, previous, shared in selected:
        rid = f'history-{gid}'
        ref(rid, previous.name, f'#/juego/{gid}', 'Juego de tu historial usado en la comparación de rasgos.')
        if signal['source'] == 'rating':
            opinion = f"valoraste {signal['rating']:g}/5 en GameTrack"
        elif signal['source'] == 'gametrack_review':
            opinion = 'recomendaste en tu reseña de GameTrack' if signal['recommended'] else 'no recomendaste en tu reseña de GameTrack'
        elif signal['source'] == 'steam_review':
            opinion = 'recomendaste en Steam' if signal['recommended'] else 'no recomendaste en Steam'
        elif signal['source'] == 'play_feedback':
            opinion = 'marcaste como una experiencia que te gustó' if signal['weight']>0 else 'marcaste como una experiencia que no te gustó'
        else:
            opinion = f"jugaste {signal['minutes']/60:.1f} h en Steam; eso indica interés, no que te haya gustado"
        labels = []
        for term in sorted(shared, key=lambda term:(term not in specific, term)):
            name = names.get(term, term)
            label = GENRE_TRANSLATIONS.get(name.lower(), name)
            if label.casefold() not in {item.casefold() for item in labels}:
                labels.append(label)
        text = f"Anuncia {', '.join(labels[:3])}, también presente en {previous.name}, que {opinion}."
        (positive if signal['weight'] > 0 else cautions).append(point(text,'announcement',rid,'profile'))
        reasons.append(text)
    if not specific:
        cautions.append(point('La comparación se apoya en géneros o etiquetas amplias. Faltan rasgos específicos para una estimación más detallada.','announcement'))
    cautions.append(point('Todavía no podemos confirmar calidad, rendimiento, duración, monetización ni recepción del juego final. No usamos notas críticas o reseñas de otros títulos para llenar esos datos.','announcement'))
    cautions.append(point('Antes de comprar, contrastá gameplay real, una demo si existe y reseñas posteriores al lanzamiento. Este índice no garantiza satisfacción.','announcement'))
    reasons.append('Estimación previa al lanzamiento; sólo afinidad personal. No representa un porcentaje de probabilidad ni una nota de calidad.')
    score = GameTrackScore(value=value, affinity=value, evidence='prelanzamiento' if value is not None else 'sin_datos',
        preliminary=True, version='prelaunch-1.0', reasons=reasons,
        components={'affinity':value/100} if value is not None else {}, weights={'affinity':1.} if value is not None else {},
        explanation='GameTrackScore provisional de 0 a 100. Compara rasgos anunciados con tus opiniones e interés; tus rechazos reducen el índice. Límite heurístico de 75, o 55 con rasgos amplios. No usa Metascore ni recepción pública para predecir una experiencia aún no lanzada.')
    fit = 'prometedora' if value is not None and value >= 60 else 'moderada' if value is not None and value >= 40 else 'baja'
    summary = f"Afinidad provisional {fit} con los rasgos anunciados. La experiencia final todavía no está confirmada." if value is not None else 'Todavía no hay datos comparables suficientes para estimar tu afinidad. No inventamos un puntaje.'
    answer = []
    if question:
        query = slugify(question)
        if any(word in query for word in ('compr', 'reserv', 'confirm', 'falt', 'riesg', 'segur', 'rendimiento')):
            answer = cautions[-2:]
        elif any(word in query for word in ('no-', 'disgust', 'negativ', 'rechaz')):
            answer = cautions
        elif any(word in query for word in ('parec', 'historial', 'compar')):
            answer = positive + cautions[:1] if selected else [point('No hay suficientes juegos comparables en tu historial para detallar semejanzas.','profile','announcement')]
        elif any(word in query for word in ('puntaje', 'score', 'numero', 'calcul')):
            answer = [point(score.explanation,'profile','announcement')]
        else:
            answer = [point(summary,'profile','announcement')] + positive[:2] + cautions[-2:-1]
    return GameExplanation(game_id=announced['appid'],game_name=announced['name'],score=score,summary=summary,
        positives=positive,cautions=cautions,answer=answer,references=references,
        suggested_questions=['¿En qué se parece a mi historial?','¿Por qué podría no gustarme?','¿Qué falta confirmar antes de comprar?','¿Cómo se calcula el puntaje?'],
        method='Modelo local de contenido TF-IDF y reglas con fuentes visibles. Las etiquetas anunciadas se usan por presencia, sin inventar votos. Se conserva la penalización por opiniones negativas. No es una conversación generativa ni una predicción de calidad; no se consulta IA externa.')


def explain_upcoming(db, user, appid, question=None):
    announced = announced_game(appid)
    return build_preview(announced, score_context(db,user), question)
