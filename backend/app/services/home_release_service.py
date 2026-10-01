"""Agenda pública de Steam, separada del feed personalizado y con caché acotada."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import re
import threading
import time
from urllib.parse import urlsplit

import httpx
from app.services.steam_service import _http_client

TTL = 3600
RETRY_DELAY = 120
_lock = threading.Lock()
_cached = None
_retry_at = 0


def _image(value):
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    return value if parsed.scheme == "https" and (host.endswith(".steamstatic.com") or host == "steamcdn-a.akamaihd.net") else None


def _details(appid):
    try:
        response = _http_client().get("https://store.steampowered.com/api/appdetails",
            params={"appids": appid, "l": "spanish", "cc": "ar"}, timeout=5)
        response.raise_for_status()
        envelope = response.json().get(str(appid), {})
        data = envelope.get("data")
        if envelope.get("success") is not True or not isinstance(data, dict):
            return None
        release = data.get("release_date")
        if data.get("type") != "game" or not isinstance(release, dict) or release.get("coming_soon") is not True:
            return None
        name, image = data.get("name"), _image(data.get("header_image"))
        if not isinstance(name, str) or not name.strip() or not image:
            return None
        # No convertir "2027" o "próximamente" en una fecha exacta inventada.
        label = release.get("date")
        return {"steam_app_id": appid, "name": name[:200], "background_image": image,
            "release_label": label[:100] if isinstance(label, str) and label.strip() else "Fecha por anunciar",
            "store_url": f"https://store.steampowered.com/app/{appid}/"}
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return None


def _fetch():
    response = _http_client().get("https://store.steampowered.com/search/results/",
        params={"filter": "popularcomingsoon", "category1": 998, "count": 8, "start": 0,
                "json": 1, "l": "spanish", "cc": "ar"}, timeout=5)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Formato de agenda inválido")
    candidates = []
    if isinstance(payload.get("items"), list):
        for item in payload["items"][:40]:
            if not isinstance(item, dict):
                continue
            logo = _image(item.get("logo"))
            match = re.search(r"/apps/(\d+)/", logo or "")
            if match:
                candidates.append(int(match[1]))
    elif isinstance(payload.get("results_html"), str):
        candidates = [int(value) for value in re.findall(r'data-ds-appid="(\d+)"', payload["results_html"][:500000])]
    else:
        raise ValueError("Formato de agenda inválido")
    candidates = list(dict.fromkeys(appid for appid in candidates if 0 < appid <= 4294967295))[:8]
    if not candidates:
        raise ValueError("Agenda sin títulos verificables")
    with ThreadPoolExecutor(max_workers=4) as pool:
        items = [item for item in pool.map(_details, candidates) if item is not None][:6]
    if not items:
        raise ValueError("Steam no devolvió próximos juegos verificables")
    return items


def upcoming_releases():
    global _cached, _retry_at
    now = int(time.time())
    with _lock:
        if _cached and now - _cached["updated_at"] < TTL:
            return {**deepcopy(_cached), "status": "ok"}
        if now >= _retry_at:
            try:
                _cached = {"items": _fetch(), "updated_at": int(time.time()), "source": "steam"}
                _retry_at = 0
                return {**deepcopy(_cached), "status": "ok"}
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                _retry_at = now + RETRY_DELAY
        if _cached and now - _cached["updated_at"] < 86400:
            return {**deepcopy(_cached), "status": "stale"}
        return {"items": [], "status": "unavailable", "updated_at": None, "source": "steam"}
