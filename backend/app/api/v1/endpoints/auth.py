"""Registro, login y perfil del usuario autenticado."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_player
from app.core.security import create_access_token
from app.db.database import get_db
from app.models import Game, User, UserRole
from app.services.steam_account_service import ensure_steam_account_name
from app.schemas.user import (
    LoginRequest,
    PreferencesUpdate,
    ProfileUpdate,
    Token,
    UserCreate,
    UserResponse,
    ExperienceUpdate,
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
    from app.db.real_data import is_synthetic_profile
    if is_synthetic_profile(user):
        raise HTTPException(403, "Este perfil sintético fue retirado. Iniciá sesión con tu cuenta personal o Steam.")
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
    if data.role is not None and user.role is not data.role:
        access = "jugadores" if user.role is UserRole.PLAYER else "desarrolladores / estudios"
        raise HTTPException(403, f"Esta cuenta pertenece al acceso de {access}. Ingresá desde ese acceso.")
    ensure_steam_preferences(db, user)
    return _token_for(user)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    ensure_steam_preferences(db, user)
    ensure_steam_account_name(db, user)
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
    user: User = Depends(require_player),
    db: Session = Depends(get_db),
) -> User:
    return set_preferences(db, user, data.genre_ids)


@router.put("/me/experience", response_model=Token)
def update_experience(
    data: ExperienceUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Token:
    db.refresh(user, attribute_names=["role", "onboarding_step", "studio"])
    if user.onboarding_step != "choose" and data.role is not user.role:
        raise HTTPException(403, "El tipo de cuenta se define al registrarse y no se puede cambiar")
    studio = data.studio if data.studio is not None else user.studio
    if data.role is UserRole.DEVELOPER and not (studio and studio.strip()):
        raise HTTPException(422, "Indicá el nombre del estudio o del desarrollador independiente")
    if data.role is UserRole.PLAYER and {"studio", "analysis_game_id"} & data.model_fields_set:
        raise HTTPException(422, "Configurá los juegos desde el espacio de estudio")
    if data.analysis_game_id is not None and not db.get(Game, data.analysis_game_id):
        raise HTTPException(404, "El juego seleccionado no está en el catálogo")
    values = {"role": data.role, "developer_enabled": data.role is UserRole.DEVELOPER}
    if data.role is UserRole.DEVELOPER:
        values["studio"] = studio.strip()
        if "analysis_game_id" in data.model_fields_set:
            values["analysis_game_id"] = data.analysis_game_id
    if data.complete_onboarding:
        values["onboarding_step"] = None
    elif user.onboarding_step:
        values["onboarding_step"] = "studio" if data.role is UserRole.DEVELOPER else "player"
    # La condición se evalúa en la escritura: dos solicitudes concurrentes no
    # pueden consumir la elección inicial de Steam para tipos distintos.
    result = db.execute(update(User).where(User.id == user.id,
        or_(User.role == data.role, User.onboarding_step == "choose"))
        .values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(403, "El tipo de cuenta ya fue definido y no se puede cambiar")
    db.commit()
    db.refresh(user)
    ensure_steam_preferences(db, user)
    return _token_for(user)


@router.post("/me/preferences/steam", response_model=UserResponse)
def suggest_steam_preferences(user: User = Depends(require_player), db: Session = Depends(get_db)) -> User:
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
