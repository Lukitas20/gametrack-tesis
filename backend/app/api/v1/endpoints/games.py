"""Catálogo de juegos: listado, detalle, similares y reseñas."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.ml.recommender import get_engine
from app.models import Game
from app.schemas.game import GameDetail, GamePage, GameSummary, GenreOut, TagOut
from app.schemas.interaction import ReviewOut
from app.schemas.recommendation import RecommendationOut
from app.services import steam_service
from app.services.game_service import (
    get_game,
    list_games,
    list_genres,
    list_reviews,
    list_tags,
)

router = APIRouter(tags=["catalogo"])


@router.get("/genres", response_model=list[GenreOut])
def get_genres(db: Session = Depends(get_db)) -> list:
    """Géneros disponibles. Alimenta el onboarding de preferencias."""
    return list_genres(db)


@router.get("/tags", response_model=list[TagOut])
def get_tags(
    min_games: int = Query(default=2, ge=1, description="Descarta etiquetas poco usadas"),
    db: Session = Depends(get_db),
) -> list:
    """Etiquetas del catálogo, de las más usadas a las menos."""
    return list_tags(db, min_games=min_games)


@router.get("/games", response_model=GamePage)
def get_games(
    search: str | None = Query(default=None, description="Busca por nombre o desarrollador"),
    genre: str | None = Query(default=None, description="Slug de género"),
    tag: str | None = Query(default=None, description="Slug de etiqueta"),
    min_rating: float | None = Query(default=None, ge=0, le=5),
    max_playtime: int | None = Query(
        default=None, ge=1, description="Duración máxima en horas"
    ),
    sort: str = Query(default="popularidad", pattern="^(rating|popularidad|metacritic|nombre|lanzamiento)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> GamePage:
    total, games = list_games(
        db, search, genre, tag, min_rating, max_playtime, sort, limit, offset
    )
    return GamePage(
        total=total,
        limit=limit,
        offset=offset,
        items=[GameSummary.model_validate(game) for game in games],
    )


def _require_game(db: Session, game_id: int) -> Game:
    game = get_game(db, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="El juego no existe")
    # Si viene de Steam y hace rato que no se sincroniza, se refresca acá:
    # es el punto por el que pasan la ficha, los similares y las reseñas.
    steam_service.maybe_refresh(db, game)
    return game


@router.get("/games/{game_id}", response_model=GameDetail)
def get_game_detail(game_id: int, db: Session = Depends(get_db)) -> Game:
    return _require_game(db, game_id)


@router.get("/games/{game_id}/similar", response_model=list[RecommendationOut])
def get_similar_games(
    game_id: int,
    limit: int = Query(default=8, ge=1, le=30),
    db: Session = Depends(get_db),
) -> list[RecommendationOut]:
    """Juegos parecidos según el modelo basado en contenido (TF-IDF + coseno)."""
    game = _require_game(db, game_id)
    engine = get_engine(db)
    recommendations = engine.similar_games(game.id, limit=limit)

    games = {
        item.id: item
        for item in db.scalars(
            select(Game).where(Game.id.in_([r.game_id for r in recommendations]))
        )
    }
    return [
        RecommendationOut(
            game=GameSummary.model_validate(games[r.game_id]),
            score=r.score,
            source=r.source,
            reason=r.reason,
            components=r.components,
        )
        for r in recommendations
        if r.game_id in games
    ]


@router.get("/games/{game_id}/reviews", response_model=list[ReviewOut])
def get_game_reviews(
    game_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list:
    _require_game(db, game_id)
    return list_reviews(db, game_id, limit=limit, offset=offset)
