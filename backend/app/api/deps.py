"""Dependencias compartidas por los endpoints."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.database import get_db
from app.models import User, UserRole
from app.services.user_service import get_user

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    payload = decode_access_token(credentials.credentials)
    if not payload or not payload.get("sub"):
        raise unauthorized

    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise unauthorized
    user = get_user(db, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def get_optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Admite visitas anónimas; un token enviado igual debe ser válido."""
    if credentials is None:
        return None
    return get_current_user(credentials, db)


def require_developer(user: User = Depends(get_current_user)) -> User:
    """Restringe un endpoint al rol desarrollador."""
    if user.role is not UserRole.DEVELOPER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta sección es exclusiva del rol desarrollador",
        )
    return user
