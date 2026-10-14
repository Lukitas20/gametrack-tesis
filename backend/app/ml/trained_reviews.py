"""Modelos propios de texto, ajustados en CPU y exportados sin pickle.

Recepción: supervisión real (recomendación declarada por el autor en Steam).
Temas: supervisión débil por vocabulario curado; no son etiquetas humanas.
Ni una predicción de recepción ni un tema constituyen la opinión del usuario.
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile
import numpy as np
from scipy.special import expit
from sklearn.feature_extraction.text import TfidfVectorizer
from app.core.config import settings

VERSION = 1
TOPICS = ('jugabilidad', 'graficos', 'historia', 'optimizacion', 'monetizacion', 'dificultad')
LABELS = dict(zip(TOPICS, ('Jugabilidad', 'Gráficos', 'Historia', 'Rendimiento y errores', 'Precio y monetización', 'Dificultad')))

def artifact_path():
    return Path(settings.REVIEW_MODEL_PATH)

def vectorizer(vocabulary=None):
    return TfidfVectorizer(strip_accents='unicode', lowercase=True, ngram_range=(1, 2),
                           sublinear_tf=True, max_features=18000, min_df=3,
                           vocabulary=vocabulary, token_pattern=r'(?u)\b\w\w+\b')

class ReviewModel:
    def __init__(self, vector, coefficients, intercepts, metadata):
        self.vector, self.coefficients, self.intercepts, self.metadata = vector, coefficients, intercepts, metadata

    def evidence(self, text, topic):
        from app.ml.analytics import normalize_text
        row=self.vector.transform([text[:6000]])
        contributions=row.multiply(self.coefficients[TOPICS.index(topic)+1]).tocsr()
        vocabulary=self.vector.get_feature_names_out()
        normalized=normalize_text(text)
        ranked=sorted(zip(contributions.indices,contributions.data),key=lambda item:-item[1])
        for index,value in ranked:
            position=normalized.find(vocabulary[index])
            if value>0 and position>=0:
                start=max(0,position-100);end=min(len(text),position+220)
                return ('…' if start else '')+text[start:end]+('…' if end<len(text) else '')
        return text[:320]+('…' if len(text)>320 else '')

    def predict(self, texts):
        if not texts:
            return []
        matrix = self.vector.transform([text[:6000] for text in texts])
        scores = expit(np.asarray(matrix @ self.coefficients.T) + self.intercepts)
        covered = np.asarray(matrix.getnnz(axis=1)).ravel() > 0
        return [dict(reception=float(row[0]) if known and self.metadata['reception']['eligible'] else None,
                     topics=[dict(topic=topic, label=LABELS[topic], strength=round(float(row[i+1]), 4))
                             for i, topic in enumerate(TOPICS)
                             if known and self.metadata['topics'][topic]['eligible'] and
                             row[i+1] >= self.metadata['topics'][topic]['threshold']])
                for row, known in zip(scores, covered)]

    def save(self, path):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        vocabulary = {word: int(index) for word, index in self.vector.vocabulary_.items()}
        with path.with_suffix('.tmp').open('wb') as handle:
            np.savez_compressed(handle, vocabulary=json.dumps(vocabulary, ensure_ascii=False),
                                idf=self.vector.idf_, coefficients=self.coefficients,
                                intercepts=self.intercepts, metadata=json.dumps(self.metadata, ensure_ascii=False))
        path.with_suffix('.tmp').replace(path)
        path.with_suffix('.json').write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2), encoding='utf-8')

    @classmethod
    def load(cls, path):
        if Path(path).stat().st_size > 20_000_000:
            raise ValueError('Artefacto de reseñas demasiado grande')
        with np.load(path, allow_pickle=False) as data:
            vocabulary=json.loads(str(data['vocabulary'])); meta=json.loads(str(data['metadata']))
            idf, weights, biases = data['idf'], data['coefficients'], data['intercepts']
        n=len(vocabulary)
        validate_provenance(meta)
        if (meta.get('version') != VERSION or not 1 <= n <= 18000 or
                sorted(vocabulary.values()) != list(range(n)) or weights.shape != (7,n) or
                biases.shape != (7,) or idf.shape != (n,) or
                not all(np.isfinite(value).all() for value in (idf,weights,biases)) or np.any(idf<=0)):
            raise ValueError('Artefacto de reseñas inválido')
        if set(meta['topics']) != set(TOPICS):
            raise ValueError('Temas incompatibles')
        for topic in meta['topics'].values():
            if not isinstance(topic['eligible'], bool) or not .5 <= topic['threshold'] <= .95:
                raise ValueError('Umbrales incompatibles')
        if not isinstance(meta['reception']['eligible'],bool):
            raise ValueError('Activación incompatible')
        vector=vectorizer(vocabulary); vector.idf_=idf
        return cls(vector, weights, biases, meta)


def validate_provenance(meta):
    """Exige el contrato de reseñas observadas; no admite modelos demo."""
    if (meta.get('source') != 'real_steam_reviews' or meta.get('language') != 'es'
            or meta.get('algorithm') != 'tfidf_logistic_regression_cpu'
            or meta.get('reception', {}).get('supervision') != 'real_steam_recommendation_flag'
            or meta.get('reception', {}).get('target') != 'steam_author_recommendation_not_personal_taste_or_text_sentiment'
            or meta.get('split') != 'disjoint_game_hash_80_10_10_and_normalized_text_deduplication'):
        raise ValueError('Procedencia de reseñas no admitida')
    fingerprint = meta.get('dataset_fingerprint', '')
    if len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise ValueError('Falta la huella del conjunto real de reseñas')
    parts = meta.get('partitions', {})
    if set(parts) != {'train', 'validation', 'test'}:
        raise ValueError('Particiones incompatibles')
    if any(type(part.get('examples')) is not int or part['examples'] < 100
           or type(part.get('games')) is not int or part['games'] < 1 for part in parts.values()):
        raise ValueError('Particiones insuficientes')
    if sum(part['examples'] for part in parts.values()) != meta.get('examples'):
        raise ValueError('Conteo de reseñas incompatible')
    if sum(part['games'] for part in parts.values()) != meta.get('games'):
        raise ValueError('Conteo de juegos incompatible')
    if any(item.get('supervision') != 'weak_curated_vocabulary' for item in meta.get('topics', {}).values()):
        raise ValueError('Supervisión de temas desconocida')

@lru_cache(maxsize=2)
def _load(path, modified, size):
    return ReviewModel.load(Path(path))

def load_model():
    path=artifact_path()
    if not path.is_file():
        return None, dict(status='missing', message='Todavía no se entrenó el modelo de reseñas.')
    try:
        stat=path.stat(); model=_load(str(path), stat.st_mtime_ns, stat.st_size)
        return model, dict(status='ready', **model.metadata)
    except (ValueError, KeyError, OSError, TypeError, EOFError, BadZipFile, AttributeError):
        return None, dict(status='invalid', message='El artefacto no es válido; sigue disponible el análisis por reglas.')
