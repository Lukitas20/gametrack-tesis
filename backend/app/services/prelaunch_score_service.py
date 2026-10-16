"""Afinidad provisional para juegos anunciados. Sin notas críticas ni IA externa.

Se proyectan rasgos públicos en el TF-IDF local, conservando las penalizaciones
de historial. Las etiquetas de un juego anunciado no tienen votos comparables:
se usan por presencia y el índice se limita a 75. Los géneros solos no alcanzan
para asignar afinidad baja ni para seleccionar ejemplos del historial.
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
from sqlalchemy import select

from app.ml.recommender import get_engine
from app.models import Game, Rating
from app.schemas.gametrack_score import GameExplanation, GameTrackScore
from app.services import home_release_service as releases
from app.services.personal_history_service import combine_history_affinities, own_history_affinity, personal_history
from app.services.steam_service import GENRE_TRANSLATIONS, slugify

_cache = {}
_lock = threading.Lock()
TTL = 3600
INCOMPLETE_TTL = 120
# Rasgos de presentación, clasificación por edad o distribución no bastan
# para llamar parecidos a juegos de jugabilidad distinta.
PRESENTATION_TERMS = {"3d", "2d", "controller", "violent", "violence", "gore", "nudity",
                      "sexual-content", "early-access", "full-controller-support"}


class StoreTags(HTMLParser):
    """Etiquetas app_tag públicas: enlaces de la tienda y divs de su ficha breve."""
    def __init__(self):
        super().__init__()
        self.tags, self.capture, self.text, self.depth = [], None, [], 0

    def handle_starttag(self, tag, attrs):
        if self.capture:
            if tag == self.capture:
                self.depth += 1
        elif tag in {'a', 'div', 'span'} and 'app_tag' in dict(attrs).get('class', '').split():
            self.capture, self.text, self.depth = tag, [], 1

    def handle_data(self, text):
        if self.capture:
            self.text.append(text)

    def handle_endtag(self, tag):
        if tag == self.capture:
            self.depth -= 1
            if self.depth:
                return
            name = ' '.join(''.join(self.text).split())[:80]
            if any(char.isalnum() for char in name) and name not in self.tags and len(self.tags) < 20:
                self.tags.append(name)
            self.capture = None


def public_tags(appid):
    """Dos vistas públicas del mismo anuncio; sin simular una fecha de nacimiento."""
    for url in (f'https://store.steampowered.com/app/{appid}/',
                f'https://store.steampowered.com/apphoverpublic/{appid}/'):
        try:
            page = releases._http_client().get(url, params={'l': 'english', 'cc': 'ar'}, timeout=5)
            page.raise_for_status()
            parser = StoreTags(); parser.feed(page.text[:1500000])
            if parser.tags:
                return parser.tags, url
        except (httpx.HTTPError, ValueError):
            continue
    return [], None


def announced_game(appid):
    """Caché acotada de datos públicos; nunca guarda perfiles ni puntajes."""
    now = time.time()
    with _lock:
        cached = _cache.get(appid)
        if cached and now - cached['cached_at'] < (TTL if cached['tags_available'] else INCOMPLETE_TTL):
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
        # Las etiquetas de Steam usan slugs ingleses en el modelo existente.
        tags, tag_source = public_tags(appid)
        label = (data.get('release_date') or {}).get('date')
        result = {'appid': appid, 'name': name[:200], 'genres': genres[:15], 'tags': tags,
                  'tags_available': bool(tags), 'tag_source': tag_source,
                  'release_label': label[:100] if isinstance(label, str) and label.strip() else 'Fecha por anunciar',
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
    specific = engine.personal_specific_terms - PRESENTATION_TERMS
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
    # Un anuncio x historial completo. No usa el recorte global por horas ni
    # construye la matriz catálogo x historial que necesita el feed general.
    ids = [gid for gid in history_ids if gid in engine._game_index]
    if not ids:
        return {}, known
    previous = normalize(engine._tfidf[[engine._game_index[gid] for gid in ids]].multiply(weights).tocsr())
    values = (previous @ candidate.T).toarray().ravel()
    result = {gid: np.array([float(value)]) for gid, value in zip(ids, values)}
    return result, known


def preview_context(db, user):
    """Señales propias completas, sin inferencias ni recortes del feed del catálogo."""
    ratings = dict(db.execute(select(Rating.game_id, Rating.score).where(Rating.user_id == user.id)).all())
    signals, _, _ = personal_history(db, user, ratings)
    games = {}
    ids = list(signals)
    for start in range(0, len(ids), 500):
        games.update({game.id: game for game in db.scalars(select(Game).where(Game.id.in_(ids[start:start + 500]))).all()})
    return {'engine': get_engine(db), 'signals': signals, 'history_games': games,
            'genres': {pref.genre.slug for pref in user.preferences}, 'personal': bool(signals or user.preferences)}


def build_preview(announced, context, question=None):
    genres = {slugify(name) for name in announced['genres']}
    tags = {slugify(name) for name in announced['tags']}
    similarities, known = preview_similarities(context['engine'], genres, tags, context['signals'])
    specific = known & (context['engine'].personal_specific_terms - PRESENTATION_TERMS)
    positive, cautions, reasons, references = [], [], [], []
    def ref(rid, title, url, detail):
        references.append({'id':rid, 'title':title, 'url':url, 'detail':detail})
    def point(text, *ids):
        return {'text':text, 'reference_ids':list(ids)}
    store_url = f"https://store.steampowered.com/app/{announced['appid']}/"
    checked = datetime.fromtimestamp(announced['cached_at'], timezone.utc).strftime('%d/%m/%Y %H:%M UTC')
    ref('announcement', 'Anuncio en Steam', store_url, f"Fecha anunciada: {announced['release_label']} · consulta {checked}. Géneros y etiquetas públicos; pueden cambiar.")
    ref('profile', 'Mi perfil', '#/perfil', 'Tus valoraciones y tu historial sincronizado. Las horas indican interés, no satisfacción.')
    if announced.get('tag_source'):
        ref('tags', 'Etiquetas públicas de Steam', announced['tag_source'], 'Rasgos observados en Steam para este anuncio; sin inferirlos del nombre ni de otros juegos.')
    tag_refs = ['announcement'] + (['tags'] if announced.get('tag_source') else [])
    comparisons = []
    for gid, similarity in similarities.items():
        previous, signal = context['history_games'].get(gid), context['signals'][gid]
        if previous is None or similarity[0] < .12 or not signal['weight']:
            continue
        shared = specific & context['engine']._community_terms_by_game.get(gid, set())
        if not shared:
            continue
        strength = ((own_history_affinity(signal) - .15) / .85
                    if signal['weight'] > 0 and signal['source'] != 'steam_playtime' else abs(signal['weight']))
        comparisons.append((float(similarity[0]) * strength, gid, signal, previous, sorted(shared)))
    comparisons.sort(key=lambda item:(-item[0], item[1]))
    value = None
    if comparisons:
        comparable = {item[1]: context['signals'][item[1]] for item in comparisons}
        values, _, _ = combine_history_affinities(similarities, comparable, 1)
        if values is not None:
            value = min(75, round(float(values[0])*100))
            reasons.append('Comparamos los rasgos anunciados con los juegos de tu historial que comparten características específicas. El puntaje está limitado a 75/100 porque la experiencia final todavía no está confirmada.')
    else:
        overlap = genres & context['genres']
        if overlap:
            positive.append(point('Coincide con los géneros de tu perfil: ' + ', '.join(sorted(overlap)) + '. Es una coincidencia amplia, no una experiencia confirmada.', 'announcement','profile'))
        reasons.append('Faltan rasgos específicos compartidos con tu historial. La falta de información no significa afinidad baja: no asignamos un número ni elegimos ejemplos sólo por compartir un género.')
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
        (positive if signal['weight'] > 0 else cautions).append(point(text,*tag_refs,rid,'profile'))
        reasons.append(text)
    if not specific:
        cautions.append(point('Faltan rasgos específicos del anuncio. Compartir sólo «Acción» u otro género amplio no alcanza para elegir juegos parecidos ni calcular tu afinidad.','announcement'))
    history_count = len(similarities)
    coverage = f"Revisamos {history_count} juegos de tu historial con datos de contenido disponibles; {len(comparisons)} comparten rasgos específicos con este anuncio."
    reasons.append(coverage)
    cautions.append(point(coverage + ' Los títulos todavía sin rasgos en el catálogo no permiten una comparación fiable.', 'profile', *tag_refs))
    cautions.append(point('Todavía no podemos confirmar calidad, rendimiento, duración, monetización ni recepción del juego final. No usamos notas críticas o reseñas de otros títulos para llenar esos datos.','announcement'))
    cautions.append(point('Antes de comprar, contrastá gameplay real, una demo si existe y reseñas posteriores al lanzamiento. Este índice no garantiza satisfacción.','announcement'))
    reasons.append('Estimación previa al lanzamiento; sólo afinidad personal. No representa un porcentaje de probabilidad ni una nota de calidad.')
    score = GameTrackScore(value=value, affinity=value, evidence='prelanzamiento' if value is not None else 'sin_datos',
        preliminary=True, version='prelaunch-2.0', reasons=reasons,
        confidence='medium' if value is not None and any(item[2]['source'] != 'steam_playtime' for item in comparisons) else 'low' if value is not None else 'none',
        confidence_message=coverage + (' Las horas indican interés; no garantizan que te haya gustado.' if value is not None else ' Sin suficiente evidencia no calculamos un puntaje.'),
        components={'affinity':value/100} if value is not None else {}, weights={'affinity':1.} if value is not None else {},
        explanation='GameTrackScore provisional de 0 a 100. Revisa todo el historial con datos disponibles y usa sólo juegos con rasgos específicos compartidos; tus rechazos comparables reducen el índice. Límite heurístico de 75. Sin rasgos comparables no asigna puntaje. No usa Metascore ni recepción pública para predecir una experiencia aún no lanzada.')
    fit = 'prometedora' if value is not None and value >= 60 else 'moderada' if value is not None and value >= 40 else 'baja'
    summary = f"Afinidad provisional {fit} con los rasgos anunciados. La experiencia final todavía no está confirmada." if value is not None else 'Faltan rasgos comparables para estimar tu afinidad con este anuncio. Eso no significa que no te vaya a gustar; todavía no calculamos un puntaje.'
    answer = []
    if question:
        query = slugify(question)
        if any(word in query for word in ('compr', 'reserv', 'confirm', 'falt', 'riesg', 'segur', 'rendimiento')):
            answer = cautions[-2:]
        elif any(word in query for word in ('no-', 'disgust', 'negativ', 'rechaz')):
            answer = cautions
        elif any(word in query for word in ('parec', 'historial', 'compar')):
            answer = ([p for p in positive + cautions if any(rid.startswith('history-') for rid in p['reference_ids'])]
                      if selected else [point('No hay suficientes juegos comparables en tu historial para detallar semejanzas.','profile','announcement')])
        elif any(word in query for word in ('puntaje', 'score', 'numero', 'calcul')):
            answer = [point(score.explanation,'profile','announcement')]
        else:
            answer = [point(summary,'profile','announcement')] + positive[:2] + cautions[-2:-1]
    return GameExplanation(game_id=announced['appid'],game_name=announced['name'],score=score,summary=summary,
        positives=positive,cautions=cautions,answer=answer,references=references,
        suggested_questions=['¿En qué se parece a mi historial?','¿Por qué podría no gustarme?','¿Qué falta confirmar antes de comprar?','¿Cómo se calcula el puntaje?'],
        method='Compara el anuncio con todo tu historial que tiene rasgos disponibles en el catálogo, sin recortarlo a los juegos con más horas. Sólo los juegos con rasgos específicos compartidos intervienen en el puntaje y sus ejemplos. Modelo local de contenido TF-IDF; las etiquetas públicas del anuncio se usan por presencia, sin inventar votos. Se conserva la penalización por opiniones negativas comparables. No es una predicción de calidad ni se consulta IA externa.')


def explain_upcoming(db, user, appid, question=None):
    announced = announced_game(appid)
    return build_preview(announced, preview_context(db,user), question)
