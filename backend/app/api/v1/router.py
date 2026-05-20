from fastapi import APIRouter
from app.api.v1.endpoints import auth, health, games, reviews, steam, recommendations

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(games.router)
api_router.include_router(reviews.router)
api_router.include_router(steam.router)
api_router.include_router(recommendations.router)