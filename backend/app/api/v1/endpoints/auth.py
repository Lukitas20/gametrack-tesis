"""Registro, login y perfil del usuario autenticado."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import create_access_token
from app.db.database import get_db
from app.models import User
from app.schemas.user import (
    LoginRequest,
    PreferencesUpdate,
    ProfileUpdate,
    Token,
    UserCreate,
    UserResponse,
)
from app.services.user_service import (
    authenticate_user,
    create_user,
    get_user_by_email,
    get_user_by_username,
    set_preferences,
    update_profile,
    ensure_steam_preferences,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_for(user: User) -> Token:
    access_token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return Token(access_token=access_token, user=UserResponse.model_validate(user))


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(data: UserCreate, db: Session = Depends(get_db)) -> Token:
    if get_user_by_username(db, data.username):
        raise HTTPException(status_code=400, detail="El nombre de usuario ya existe")
    if data.email and get_user_by_email(db, data.email):
        raise HTTPException(status_code=400, detail="El email ya está registrado")
    return _token_for(create_user(db, data))


@router.post("/login", response_model=Token)
def login(data: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = authenticate_user(db, data.username, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    ensure_steam_preferences(db, user)
    return _token_for(user)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    ensure_steam_preferences(db, user)
    return user


@router.put("/me", response_model=UserResponse)
def update_me(
    data: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    try:
        return update_profile(db, user, data.full_name, data.email, data.avatar_url)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/me/preferences", response_model=UserResponse)
def update_preferences(
    data: PreferencesUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    return set_preferences(db, user, data.genre_ids)


@router.post("/me/preferences/steam", response_model=UserResponse)
def suggest_steam_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    from copy import deepcopy
    from app.services import steam_profile_service as profiles, steam_preferences_service as tastes
    cache = profiles.sync_profile(db, user)
    with profiles._user_lock(user.id):
        db.refresh(cache)
        library = deepcopy(cache.library)
        if not tastes.assign_preferences(db, user, library, force=True):
            cache.library = library
            db.commit()
            raise HTTPException(409, "Todavía no tenemos géneros de tus juegos jugados. Revisá que tu biblioteca sea pública y volvé a sincronizar Steam.")
        cache.library = library
        db.commit()
        db.refresh(user)
    return user
