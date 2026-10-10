"""Validación compartida de imágenes oficiales de Steam almacenadas o remotas."""

from urllib.parse import urlsplit


def sanitize_screenshots(values) -> list[str]:
    """Mantiene sólo HTTPS de los CDN usados por la tienda, sin duplicados."""
    if not isinstance(values, (list, tuple)):
        return []
    images = []
    for value in values:
        if not isinstance(value, str) or len(value) > 2048:
            continue
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            continue
        try:
            parts = urlsplit(value)
            host = (parts.hostname or "").lower()
            port = parts.port
        except ValueError:
            continue
        official = any(host == root or host.endswith("." + root)
                       for root in ("steamstatic.com", "steamusercontent.com", "akamaihd.net"))
        if (parts.scheme != "https" or not official or parts.username or parts.password
                or port not in (None, 443) or not parts.path or parts.fragment):
            continue
        if value not in images:
            images.append(value)
            if len(images) == 16:
                break
    return images


def extract_screenshots(data: dict) -> list[str]:
    """Extrae capturas proporcionadas por Steam; nunca deriva URLs del AppID."""
    values = data.get("screenshots") or []
    if not isinstance(values, list):
        return []
    return sanitize_screenshots([
        shot.get("path_full") for shot in values if isinstance(shot, dict)
    ])
