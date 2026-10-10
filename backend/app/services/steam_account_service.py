"""Nombres de cuenta legibles a partir del perfil Steam verificado."""
import re
import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import User


def _generated_username(user):
    if user.hashed_password:
        return False
    legacy = rf"steam_{re.escape(user.steam_id or '')}(?:_[0-9a-f]{{8}})?"
    return bool(re.fullmatch(legacy, user.username or "") or
                re.fullmatch(r"jugador_steam(?:_\d{1,2}|_[0-9a-f]{6})?", user.username or ""))


def assign_steam_username(db, user, name=None):
    """Reserva un nombre único sin exponer el SteamID ni cambiar el ID de cuenta.

    El savepoint permite resolver también un nombre ocupado entre el SELECT y
    el INSERT/UPDATE, sin perder la transacción del acceso OpenID.
    """
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        # El modo legacy de sqlite3 no inicia BEGIN para SELECT/SAVEPOINT.
        # Debe existir una transacción exterior para que rollback abarque
        # también esta reserva de nombre y no quede una cuenta incompleta.
        connection.exec_driver_sql("BEGIN")
    base = (name or "jugador_steam").strip()[:50]
    if len(base) < 3:
        base += "_steam"
    for attempt in range(32):
        suffix = "" if attempt == 0 else f"_{attempt + 1}" if attempt < 10 else "_" + secrets.token_hex(3)
        candidate = base[:50 - len(suffix)].rstrip() + suffix
        owner = db.scalar(select(User.id).where(User.username == candidate))
        if owner is not None and owner != user.id:
            continue
        try:
            with db.begin_nested():
                user.username = candidate
                db.add(user)
                db.flush()
            return
        except IntegrityError:
            if not db.is_active:
                raise
            # Otros conflictos (por ejemplo SteamID duplicado) siguen siendo
            # responsabilidad del flujo de autenticación.
            if db.scalar(select(User.id).where(User.username == candidate)) is None:
                raise
    raise ValueError("No pudimos asignar un nombre de usuario disponible")


def apply_steam_profile(db, user, profile):
    if not isinstance(profile, dict) or profile.get("steamid", user.steam_id) != user.steam_id:
        return
    raw_name = profile.get("personaname")
    name = raw_name.strip()[:100] if isinstance(raw_name, str) else ""
    if name:
        if _generated_username(user):
            assign_steam_username(db, user, name)
        user.steam_username = name
        if not user.full_name:
            user.full_name = name
    if isinstance(profile.get("avatarfull"), str) and profile["avatarfull"]:
        user.steam_avatar_url = profile["avatarfull"]


def ensure_steam_account_name(db, user):
    """Actualiza cuentas anteriores al restaurar la sesión, sin pedir otro login."""
    if not user.steam_verified:
        return
    name = (user.steam_username or "").strip()
    if name:
        if not _generated_username(user) and user.full_name:
            return
        apply_steam_profile(db, user, {"personaname": name})
    elif settings.STEAM_API_KEY:
        from app.services.steam_service import get_player_summary
        try:
            profile = get_player_summary(user.steam_id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return
        if not profile:
            return
        apply_steam_profile(db, user, profile)
    else:
        return
    db.commit()
    db.refresh(user)
