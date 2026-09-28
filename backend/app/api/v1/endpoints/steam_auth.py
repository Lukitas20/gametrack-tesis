"""Steam OpenID 2.0: verificación en servidor y entrega de sesión sin tokens en URL.

El endpoint y el origen de retorno son fijos. Los estados y las entregas son
aleatorios, ligados al navegador, expiran y se consumen atómicamente una vez.
"""
import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.v1.endpoints.auth import _token_for
from app.core.config import settings
from app.db.database import get_db
from app.models import SteamAuthFlow, SteamIdentity, User, UserRole
from app.services.steam_service import get_player_summary

router = APIRouter(prefix="/auth/steam", tags=["auth"])
ENDPOINT = "https://steamcommunity.com/openid/login"
NAMESPACE = "http://specs.openid.net/auth/2.0"
SELECT_ID = NAMESPACE + "/identifier_select"
STATE_COOKIE = "gametrack_steam_state"
SESSION_COOKIE = "gametrack_steam_exchange"


def base_url():
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in {"https", "http"} or not parts.netloc or parts.path or parts.query or parts.fragment:
        raise HTTPException(503, "Configurá PUBLIC_BASE_URL con el origen público de GameTrack")
    if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1", "[::1]", "::1"}:
        raise HTTPException(503, "El acceso público con Steam requiere HTTPS")
    return base


def cookie_path():
    return settings.API_V1_PREFIX + "/auth/steam"


def set_cookie(response, name, token, seconds):
    response.set_cookie(name, token, max_age=seconds, httponly=True,
                        secure=base_url().startswith("https://"), samesite="lax", path=cookie_path())
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def new_flow(db, purpose, user_id=None, seconds=600):
    token = secrets.token_urlsafe(32)
    db.execute(delete(SteamAuthFlow).where(SteamAuthFlow.expires_at < int(time.time())))
    db.add(SteamAuthFlow(token_hash=hashlib.sha256(token.encode()).hexdigest(), purpose=purpose,
                        user_id=user_id, expires_at=int(time.time()) + seconds))
    db.commit()
    return token


def consume(db, token, purpose):
    if not token or len(token) > 100:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute(delete(SteamAuthFlow).where(
        SteamAuthFlow.token_hash == digest, SteamAuthFlow.purpose == purpose,
        SteamAuthFlow.expires_at >= int(time.time())
    ).returning(SteamAuthFlow.user_id)).first()
    db.commit()
    return row  # A row with user_id=None is a valid anonymous login attempt.


def return_url(token):
    return base_url() + cookie_path() + "/callback?" + urlencode({"state": token})


def begin(db, user_id=None):
    token = new_flow(db, "state", user_id)
    url = ENDPOINT + "?" + urlencode({
        "openid.ns": NAMESPACE, "openid.mode": "checkid_setup",
        "openid.return_to": return_url(token), "openid.realm": base_url() + "/",
        "openid.identity": SELECT_ID, "openid.claimed_id": SELECT_ID,
    })
    return token, url


@router.get("/start")
def start(request: Request, db: Session = Depends(get_db)):
    # Llegar por 127.0.0.1 no debe dejar la cookie en un origen distinto al retorno.
    if str(request.base_url).rstrip("/") != base_url():
        return RedirectResponse(base_url() + cookie_path() + "/start", status_code=303)
    token, url = begin(db)
    response = RedirectResponse(url, status_code=303)
    set_cookie(response, STATE_COOKIE, token, 600)
    return response


@router.post("/link")
def link(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if str(request.base_url).rstrip("/") != base_url():
        raise HTTPException(400, f"Para vincular Steam, abrí GameTrack desde {base_url()}")
    token, url = begin(db, user.id)
    response = JSONResponse({"url": url})
    set_cookie(response, STATE_COOKIE, token, 600)
    return response


def failure(reason):
    response = RedirectResponse(base_url() + "/#/cuentas?steam_error=" + reason, status_code=303)
    response.delete_cookie(STATE_COOKIE, path=cookie_path())
    response.delete_cookie(SESSION_COOKIE, path=cookie_path())
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def verified_steam_id(params, expected_return):
    """No se confía en un SteamID hasta que Steam verifica la firma completa."""
    required = {"op_endpoint", "claimed_id", "identity", "return_to", "response_nonce", "assoc_handle"}
    if (params.get("openid.ns") != NAMESPACE or params.get("openid.mode") != "id_res"
        or params.get("openid.op_endpoint") != ENDPOINT
        or params.get("openid.return_to") != expected_return
        or not required.issubset(set(params.get("openid.signed", "").split(",")))
        or not params.get("openid.sig") or not params.get("openid.assoc_handle")):
        raise ValueError("invalid")
    claimed = params.get("openid.claimed_id", "")
    match = re.fullmatch(r"https?://steamcommunity\.com/openid/id/(\d{17})", claimed)
    if not match or params.get("openid.identity") != claimed:
        raise ValueError("invalid")
    try:
        issued = datetime.strptime(params["openid.response_nonce"][:20], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = time.time() - issued.timestamp()
        if not -60 <= age <= 600:
            raise ValueError("expired")
    except (KeyError, ValueError):
        raise ValueError("invalid") from None
    verification = {k: v for k, v in params.items() if k.startswith("openid.")}
    verification["openid.mode"] = "check_authentication"
    # Fixed HTTPS endpoint, no redirects: assertions cannot choose a server to contact.
    with httpx.Client(timeout=12.0, follow_redirects=False) as client:
        response = client.post(ENDPOINT, data=verification)
        response.raise_for_status()
    fields = dict(line.split(":", 1) for line in response.text.splitlines() if ":" in line)
    if fields.get("is_valid") != "true" or fields.get("ns") != NAMESPACE:
        raise ValueError("invalid")
    return match.group(1)


def resolve_user(db, steam_id, link_user_id):
    identity = db.get(SteamIdentity, steam_id)
    if identity:
        if link_user_id is not None and identity.user_id != link_user_id:
            raise ValueError("linked")
        user = db.get(User, identity.user_id)
    else:
        legacy = db.scalar(select(User).where(User.steam_id == steam_id))
        if link_user_id is None and legacy:
            # Un ID escrito manualmente nunca se convierte en una credencial.
            raise ValueError("conflict")
        if link_user_id is not None:
            user = db.get(User, link_user_id)
            if legacy and legacy.id != link_user_id:
                raise ValueError("linked")
            other = db.scalar(select(SteamIdentity).where(SteamIdentity.user_id == link_user_id))
            if other and other.steam_id != steam_id:
                raise ValueError("linked")
        else:
            username = f"steam_{steam_id}"
            if db.scalar(select(User.id).where(User.username == username)):
                username += "_" + secrets.token_hex(4)
            user = User(username=username, role=UserRole.PLAYER, steam_id=steam_id)
            db.add(user)
            db.flush()
        if not user or not user.is_active:
            # New users receive the column default at flush above.
            raise ValueError("inactive")
        user.steam_id = steam_id
        db.add(SteamIdentity(steam_id=steam_id, user_id=user.id))
    if not user or not user.is_active:
        raise ValueError("inactive")
    # La Web API mejora el perfil, pero no es requisito para autenticar.
    if settings.STEAM_API_KEY:
        try:
            profile = get_player_summary(steam_id)
            if profile:
                user.steam_username = str(profile.get("personaname", ""))[:100] or None
                user.steam_avatar_url = profile.get("avatarfull")
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    db.commit()
    db.refresh(user)
    return user


@router.get("/callback")
def callback(request: Request, db: Session = Depends(get_db)):
    params = dict(request.query_params)
    if len(params) != len(request.query_params.multi_items()):
        return failure("invalid")
    token = params.get("state", "")
    cookie = request.cookies.get(STATE_COOKIE, "")
    if not token or not cookie or not secrets.compare_digest(token, cookie):
        return failure("expired")
    flow = consume(db, token, "state")
    if flow is None:
        return failure("expired")
    if params.get("openid.mode") == "cancel":
        return failure("cancelled")
    try:
        steam_id = verified_steam_id(params, return_url(token))
        user = resolve_user(db, steam_id, flow.user_id)
        exchange = new_flow(db, "exchange", user.id, seconds=60)
    except ValueError as error:
        db.rollback()
        reason = str(error)
        return failure(reason if reason in {"invalid", "expired", "conflict", "linked", "inactive"} else "invalid")
    except httpx.HTTPError:
        db.rollback()
        return failure("unavailable")
    except IntegrityError:
        db.rollback()
        return failure("linked")
    response = RedirectResponse(base_url() + "/#/steam-complete", status_code=303)
    response.delete_cookie(STATE_COOKIE, path=cookie_path())
    set_cookie(response, SESSION_COOKIE, exchange, 60)
    return response


@router.post("/session")
def session(request: Request, response: Response, db: Session = Depends(get_db)):
    if request.headers.get("origin") not in {None, base_url()}:
        raise HTTPException(403, "Origen no permitido")
    flow = consume(db, request.cookies.get(SESSION_COOKIE), "exchange")
    if flow is None:
        raise HTTPException(401, "El acceso con Steam venció. Intentá de nuevo.")
    user = db.get(User, flow.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "Cuenta no disponible")
    response.delete_cookie(SESSION_COOKIE, path=cookie_path())
    response.headers["Cache-Control"] = "no-store"
    return _token_for(user)
