from fastapi import APIRouter

from app.api.v1.endpoints import (
    analytics,
    auth,
    games,
    health,
    home,
    interactions,
    lists,
    recommendations,
    steam,
)
from app.core.config import settings

api_router = APIRouter(prefix=settings.API_V1_PREFIX)
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(games.router)
api_router.include_router(home.router)
api_router.include_router(interactions.router)
api_router.include_router(lists.router)
api_router.include_router(recommendations.router)
api_router.include_router(analytics.router)
api_router.include_router(steam.router)
