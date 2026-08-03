"""Modelos ORM de GameTrack.

Importar este paquete registra todas las tablas en ``Base.metadata``, que es
lo que necesita ``create_all`` para crear el esquema completo.
"""

from app.models.enums import (
    Aspect,
    GameStatus,
    ListType,
    RecommendationSource,
    Sentiment,
    UserRole,
)
from app.models.game import Game, Genre, Tag, game_genres, game_tags
from app.models.game_list import GameList, GameListItem
from app.models.interaction import Rating, Review, ReviewAspect
from app.models.user import User, UserPreference

__all__ = [
    "Aspect",
    "Game",
    "GameList",
    "GameListItem",
    "GameStatus",
    "Genre",
    "ListType",
    "Rating",
    "RecommendationSource",
    "Review",
    "ReviewAspect",
    "Sentiment",
    "Tag",
    "User",
    "UserPreference",
    "UserRole",
    "game_genres",
    "game_tags",
]
