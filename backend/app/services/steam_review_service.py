"""Reseñas públicas del perfil verificado, paginadas y con caché por usuario.

La API de biblioteca no incluye reseñas; el listado por autor está en Steam
Community. Se conserva su recomendación binaria sin convertirla en estrellas.
"""
from copy import deepcopy
from html.parser import HTMLParser
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import select

from app.models import Game, SteamProfileCache
from app.services import steam_profile_service as profiles

TTL = 600
RETRY_DELAY = 30
MAX_CACHED_PAGES = 32
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _Element:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has_class(self, name):
        return name in (self.attrs.get("class") or "").split()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Element):
                yield from child.walk()

    def text(self):
        if self.tag in {"script", "style"}:
            return ""
        if self.tag == "br":
            return "\n"
        return "".join(child.text() if isinstance(child, _Element) else child for child in self.children)


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Element(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def parse_reviews(html, steam_id, page):
    parser = _PageParser()
    parser.feed(html)
    nodes = list(parser.root.walk())
    if any(node.has_class("profile_private_info") for node in nodes):
        return {"status": "private"}
    if not any(node.has_class("review_list") for node in nodes):
        return {"status": "unavailable"}
    numbers = [node.text().strip().replace(",", "") for node in nodes if node.has_class("giantNumber")]
    total = int(numbers[0]) if numbers and numbers[0].isdigit() else None
    items = {}
    for box in (node for node in nodes if node.has_class("review_box")):
        descendants = list(box.walk())
        appid = None
        recommended = None
        review_id = None
        fields = {}
        for node in descendants:
            if node.tag == "a":
                url = urlsplit(node.attrs.get("href") or "")
                match = re.search(r"/(?:app|recommended)/(\d+)(?:/|$)", url.path)
                if url.scheme == "https" and url.hostname == "steamcommunity.com" and match:
                    appid = int(match[1])
            if node.tag == "img":
                filename = urlsplit(node.attrs.get("src") or "").path.rsplit("/", 1)[-1]
                if filename in {"icon_thumbsUp.png", "icon_thumbsDown.png"}:
                    recommended = filename == "icon_thumbsUp.png"
            identifier = re.fullmatch(r"RecommendationVoteUpBtn(\d+)", node.attrs.get("id") or "")
            if identifier:
                review_id = identifier[1]
            for field in ("content", "hours", "posted"):
                if node.has_class(field):
                    fields[field] = node.text().strip()
        if not appid or appid > 4294967295 or recommended is None or "content" not in fields:
            return {"status": "unavailable"}
        hours = re.search(r"([\d,]+(?:\.\d+)?)\s+hrs on record", fields.get("hours", ""))
        minutes = round(float(hours[1].replace(",", "")) * 60) if hours else None
        items[appid] = {
            "appid": appid, "review_id": review_id, "is_recommended": recommended,
            "content": fields["content"][:20000], "minutes": minutes,
            "posted": re.sub(r"\s+", " ", fields.get("posted", ""))[:250],
            "url": f"https://steamcommunity.com/profiles/{steam_id}/recommended/{appid}/",
        }
    if total is None or (total > 0 and not items):
        return {"status": "unavailable"}
    has_more = False
    for node in nodes:
        if node.tag == "a" and (node.has_class("pagelink") or node.has_class("pagebtn")):
            values = parse_qs(urlsplit(node.attrs.get("href") or "").query).get("p", [])
            has_more = has_more or any(value.isdigit() and int(value) > page for value in values)
    return {"status": "ok", "items": list(items.values()), "total": total,
            "next_page": page + 1 if has_more else None, "updated_at": int(time.time())}


def fetch_reviews(steam_id, page):
    try:
        response = profiles.steam_service._http_client().get(
            f"https://steamcommunity.com/profiles/{steam_id}/recommended/",
            params={"p": page, "l": "english"}, timeout=12.0, follow_redirects=True,
        )
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            return {"status": "unavailable"}
        return parse_reviews(response.text, steam_id, page)
    except (httpx.HTTPError, ValueError, RecursionError):
        return {"status": "unavailable"}


def reviews(db, user, page=1, force=False):
    cache = profiles.sync_profile(db, user)  # exige identidad verificada
    with profiles._user_lock(user.id):
        cache = db.get(SteamProfileCache, user.id, populate_existing=True)
        library = deepcopy(cache.library)
        pages = library.get("user_reviews", {})
        previous = pages.get(str(page))
        now = int(time.time())
        if previous and now - previous["checked_at"] < (RETRY_DELAY if force else TTL):
            value = previous
        else:
            value = fetch_reviews(cache.steam_id, page)
            if value["status"] == "unavailable" and previous:
                value = {**deepcopy(previous), "status": "unavailable"}
            if page == 1 and value["status"] != "unavailable":
                pages = {}  # las páginas pueden cambiar al publicar o borrar reseñas
            value["checked_at"] = now
            if value["status"] == "private":
                pages = {}  # no retener reseñas que dejaron de ser públicas
            pages[str(page)] = value
            library["user_reviews"] = dict(sorted(pages.items(), key=lambda item: -item[1]["checked_at"])[:MAX_CACHED_PAGES])
            cache.library = library
            db.commit()
        payload = {"page": page, "items": [], "total": None, "next_page": None, **deepcopy(value)}
        payload["next_refresh_at"] = value["checked_at"] + RETRY_DELAY
        appids = [item["appid"] for item in payload["items"]]
        games = {game.steam_app_id: game for game in db.scalars(select(Game).where(Game.steam_app_id.in_(appids)))}
        owned = {game["appid"]: game for game in library.get("items", [])}
        for item in payload["items"]:
            game, steam = games.get(item["appid"]), owned.get(item["appid"], {})
            item["name"] = game.name if game else steam.get("name", f"Juego de Steam {item['appid']}")
            item["game_id"] = game.id if game else None
            item["cover"] = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{item['appid']}/header.jpg"
        return payload


def prepare_personal_history(db, user):
    """Importa opiniones para GTS aunque nunca se abra la pestaña Valoraciones.

    Tres páginas como máximo por consulta; la caché aplica el mismo TTL y
    límite de reintento que el perfil. El puntaje sólo usa páginas importadas,
    sin afirmar que conoce todas las reseñas de una cuenta extensa.
    """
    page = 1
    for _ in range(3):
        data = reviews(db, user, page)
        if data["status"] != "ok" or not data.get("next_page"):
            break
        page = data["next_page"]
