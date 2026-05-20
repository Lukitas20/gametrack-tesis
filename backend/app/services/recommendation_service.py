import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from app.models.game import Game
from app.models.review import Review, UserGameInteraction
from typing import List


def build_content_features(game: Game) -> str:
    parts = []
    if game.genres:
        parts.extend(game.genres * 3)
    if game.tags:
        parts.extend(game.tags * 2)
    if game.developer:
        parts.append(game.developer)
    if game.short_description:
        parts.append(game.short_description[:200])
    return " ".join(parts).lower()


def get_content_based_recommendations(db: Session, game_id: int, n: int = 10) -> List[dict]:
    games = db.query(Game).all()
    if len(games) < 2:
        return []

    game_ids = [g.id for g in games]
    features = [build_content_features(g) for g in games]

    vectorizer = TfidfVectorizer(stop_words="english", max_features=500)
    tfidf_matrix = vectorizer.fit_transform(features)

    try:
        idx = game_ids.index(game_id)
    except ValueError:
        return []

    cosine_sim = cosine_similarity(tfidf_matrix[idx], tfidf_matrix).flatten()
    similar_indices = cosine_sim.argsort()[::-1][1:n+1]

    results = []
    for i in similar_indices:
        if cosine_sim[i] > 0:
            results.append({
                "game_id": game_ids[i],
                "title": games[i].title,
                "score": round(float(cosine_sim[i]), 4),
                "reason": "content"
            })
    return results


def get_collaborative_recommendations(db: Session, user_id: int, n: int = 10) -> List[dict]:
    reviews = db.query(Review).all()
    if not reviews:
        return []

    user_ids = list(set(r.user_id for r in reviews))
    game_ids = list(set(r.game_id for r in reviews))

    if len(user_ids) < 2 or len(game_ids) < 2:
        return []

    user_idx = {u: i for i, u in enumerate(user_ids)}
    game_idx = {g: i for i, g in enumerate(game_ids)}

    matrix = np.zeros((len(user_ids), len(game_ids)))
    for r in reviews:
        matrix[user_idx[r.user_id]][game_idx[r.game_id]] = r.rating

    if user_id not in user_idx:
        return []

    u_idx = user_idx[user_id]
    user_vector = matrix[u_idx]
    similarities = cosine_similarity([user_vector], matrix)[0]
    similar_users = similarities.argsort()[::-1][1:6]

    scores = np.zeros(len(game_ids))
    for su in similar_users:
        sim = similarities[su]
        if sim > 0:
            scores += sim * matrix[su]

    rated_games = set(game_idx[r.game_id] for r in reviews if r.user_id == user_id)
    scores[list(rated_games)] = 0

    top_indices = scores.argsort()[::-1][:n]
    results = []
    for i in top_indices:
        if scores[i] > 0:
            game = db.query(Game).filter(Game.id == game_ids[i]).first()
            if game:
                results.append({
                    "game_id": game_ids[i],
                    "title": game.title,
                    "score": round(float(scores[i]), 4),
                    "reason": "collaborative"
                })
    return results


def get_hybrid_recommendations(db: Session, user_id: int, n: int = 10) -> List[dict]:
    user_reviews = db.query(Review).filter(Review.user_id == user_id).order_by(Review.rating.desc()).limit(3).all()

    content_recs = []
    for review in user_reviews:
        recs = get_content_based_recommendations(db, review.game_id, n=5)
        content_recs.extend(recs)

    collab_recs = get_collaborative_recommendations(db, user_id, n=n)

    seen = set(r.game_id for r in user_reviews)
    merged = {}

    for rec in content_recs:
        gid = rec["game_id"]
        if gid not in seen:
            merged[gid] = merged.get(gid, 0) + rec["score"] * 0.4

    for rec in collab_recs:
        gid = rec["game_id"]
        if gid not in seen:
            merged[gid] = merged.get(gid, 0) + rec["score"] * 0.6

    if not merged:
        top_games = db.query(Game).filter(
            Game.id.notin_(seen)
        ).order_by(Game.internal_rating.desc().nullslast()).limit(n).all()
        return [{"game_id": g.id, "title": g.title, "score": g.internal_rating or 0, "reason": "popular"} for g in top_games]

    sorted_recs = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:n]
    results = []
    for gid, score in sorted_recs:
        game = db.query(Game).filter(Game.id == gid).first()
        if game:
            results.append({
                "game_id": gid,
                "title": game.title,
                "score": round(score, 4),
                "reason": "hybrid"
            })
    return results