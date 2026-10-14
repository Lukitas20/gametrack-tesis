"""Offline, real-only Steam review observations; never writes GameTrack's DB.

Raw responses exist only in memory. Authors are linked across games with a
private HMAC key, while vote and playtime observations retain their meanings.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
from typing import Any, Callable

import httpx

ENDPOINT = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
DOCUMENTATION = "https://partner.steamgames.com/doc/webapi/IUserReviewsService"
DATA_POLICY = "observed-only-v1"
SCHEMA_VERSION = 1
SOURCE = "steam_public_review_api"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def nonnegative_int(value: Any) -> int | None:
    # bool is an int in Python, but is not a valid observation of minutes.
    return value if type(value) is int and value >= 0 else None


def observed_epoch(value: Any) -> int | None:
    """Missing/zero/future/overflow epochs are unknown, never collection time."""
    return value if type(value) is int and 0 < value <= int(time.time()) + 300 else None


def positive_appid(value: Any) -> int | None:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if type(value) is int and 0 < value < 2**32 else None


def decimal_identifier(value: Any) -> str | None:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        return None
    return value if 0 < int(value) < 2**64 else None


def normalize_review(
    raw: dict[str, Any], appid: int, key: bytes, retrieved_at: str,
) -> dict[str, Any] | None:
    """Whitelist fields, reject absent votes/identity; unknown playtime is null."""
    author = raw.get("author")
    if not isinstance(author, dict) or type(raw.get("voted_up")) is not bool:
        return None
    steamid = decimal_identifier(author.get("steamid"))
    review_id = decimal_identifier(raw.get("recommendationid"))
    if steamid is None or review_id is None or positive_appid(appid) is None:
        return None
    language = raw.get("language")
    if not isinstance(language, str) or not language.isascii() or not language.replace("-", "").replace("_", "").isalpha():
        language = None
    return {
        "user_key": hmac.new(key, steamid.encode("ascii"), hashlib.sha256).hexdigest(),
        "steam_appid": appid,
        "review_id": review_id,
        "voted_up": raw["voted_up"],
        "created_at": observed_epoch(raw.get("timestamp_created")),
        "updated_at": observed_epoch(raw.get("timestamp_updated")),
        "language": language,
        "minutes_at_review": nonnegative_int(author.get("playtime_at_review")),
        "minutes_forever": nonnegative_int(author.get("playtime_forever")),
        "minutes_recent": nonnegative_int(author.get("playtime_last_two_weeks")),
        "source": SOURCE,
        "retrieved_at": retrieved_at,
    }


def load_appids(path: Path) -> list[int]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        payload = payload.get("games")
    if not isinstance(payload, list):
        raise ValueError("El catálogo debe ser una lista o un objeto con games: [].")
    values = []
    for entry in payload:
        if isinstance(entry, dict):
            entry = entry.get("steam_appid", entry.get("steam_app_id", entry.get("appid")))
        appid = positive_appid(entry)
        if appid is not None:
            values.append(appid)
    return list(dict.fromkeys(values))


def atomic_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


class ObservationStore:
    """One current review per author/game, with crash-safe page checkpoints."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        # Custom output directories must also remain private/unversioned.
        (directory / ".gitignore").write_text("*\n", encoding="utf-8")
        self.data_path = directory / "observations.jsonl"
        key_path = directory / ".author_hmac.key"
        if not key_path.exists():
            if self.data_path.exists() and self.data_path.stat().st_size:
                raise ValueError("Falta la clave original: no se puede cambiar la identidad de las observaciones existentes.")
            with key_path.open("xb") as handle:
                handle.write(secrets.token_bytes(32))
            try:
                key_path.chmod(0o600)
            except OSError:
                pass  # Windows privacy is additionally controlled by directory ACLs.
        self.key = key_path.read_bytes()
        if len(self.key) != 32:
            raise ValueError("La clave HMAC privada debe contener exactamente 32 bytes.")
        self.key_id = hashlib.sha256(self.key).hexdigest()[:16]
        self.rows: dict[tuple[str, int], dict[str, Any]] = {}
        self.review_ids: dict[str, tuple[str, int]] = {}
        self.checkpoint_path = directory / "checkpoint.json"
        self.checkpoint: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION, "data_policy": DATA_POLICY,
            "key_id": self.key_id, "created_at": utc_now(), "games": {},
            "totals": {}, "page_receipts": [],
        }
        if self.checkpoint_path.exists():
            self.checkpoint = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if self.checkpoint.get("data_policy") != DATA_POLICY or self.checkpoint.get("key_id") != self.key_id:
                raise ValueError("El checkpoint tiene otra procedencia o clave de autores.")
        if self.data_path.exists():
            for line in self.data_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    if row.get("source") != SOURCE or type(row.get("voted_up")) is not bool:
                        raise ValueError("El archivo contiene observaciones sin procedencia Steam válida.")
                    pair = (row["user_key"], row["steam_appid"])
                    if pair in self.rows or row["review_id"] in self.review_ids:
                        raise ValueError("El archivo contiene duplicados; no se suman como interacciones nuevas.")
                    self.rows[pair] = row
                    self.review_ids[row["review_id"]] = pair

    def merge(self, row: dict[str, Any]) -> str:
        pair = (row["user_key"], row["steam_appid"])
        previous_pair = self.review_ids.get(row["review_id"])
        if previous_pair is not None and previous_pair != pair:
            return "conflicting_ids"
        previous = self.rows.get(pair)
        if previous is not None:
            # Review votes are states, not repeat independent interactions.
            previous_time = previous["updated_at"] if previous["updated_at"] is not None else previous["created_at"]
            incoming_time = row["updated_at"] if row["updated_at"] is not None else row["created_at"]
            if previous_time is not None and (incoming_time is None or incoming_time < previous_time):
                return "older_duplicates"
            compared = {key: value for key, value in row.items() if key != "retrieved_at"}
            old_compared = {key: value for key, value in previous.items() if key != "retrieved_at"}
            if compared == old_compared:
                return "duplicates"
            self.review_ids.pop(previous["review_id"], None)
            result = "updated"
        else:
            result = "inserted"
        self.rows[pair] = row
        self.review_ids[row["review_id"]] = pair
        return result

    def persist(self) -> None:
        temporary = self.data_path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for pair in sorted(self.rows):
                handle.write(json.dumps(self.rows[pair], ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary.replace(self.data_path)
        self.checkpoint["updated_at"] = utc_now()
        atomic_json(self.checkpoint_path, self.checkpoint)

    def summary(self) -> dict[str, Any]:
        rows = list(self.rows.values())
        users: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            users[row["user_key"]].append(row)
        created = [row["created_at"] for row in rows if row["created_at"] is not None]
        days = {datetime.fromtimestamp(value, timezone.utc).date().isoformat() for value in created}
        at_review = sum(row["minutes_at_review"] is not None for row in rows)
        return {
            "observations": len(rows), "users": len(users),
            "games": len({row["steam_appid"] for row in rows}),
            "votes_positive": sum(row["voted_up"] for row in rows),
            "votes_negative": sum(not row["voted_up"] for row in rows),
            "users_with_at_least_2_games": sum(len(value) >= 2 for value in users.values()),
            "users_with_at_least_3_games": sum(len(value) >= 3 for value in users.values()),
            "users_with_at_least_5_games": sum(len(value) >= 5 for value in users.values()),
            "users_with_both_votes": sum(len({row["voted_up"] for row in value}) == 2 for value in users.values()),
            "known_review_dates": len(created), "distinct_review_days": len(days),
            "first_review_epoch": min(created) if created else None,
            "last_review_epoch": max(created) if created else None,
            "minutes_at_review_observations": at_review,
            "minutes_at_review_coverage": at_review / len(rows) if rows else None,
            "training_readiness": "not_evaluated",
        }


class CollectionError(RuntimeError):
    pass


class SteamReviewClient:
    """Serial client: explicit budgets, >=1 second spacing, bounded retries."""

    def __init__(
        self, client: httpx.Client, *, delay: float = 1.0, max_requests: int = 300,
        retries: int = 2, sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if delay < 1.0 or max_requests < 1 or retries < 0 or retries > 3:
            raise ValueError("Pausa mínima 1s; solicitudes positivas y entre 0 y 3 reintentos.")
        self.client, self.delay, self.max_requests, self.retries = client, delay, max_requests, retries
        self.sleep, self.monotonic = sleep, monotonic
        self.requests = 0
        self._last_started: float | None = None

    def _pause(self, seconds: float) -> None:
        # Keep individual sleeps bounded (also permits responsive cancellation).
        while seconds > 0:
            part = min(30.0, seconds)
            self.sleep(part)
            seconds -= part

    def _retry_after(self, response: httpx.Response) -> float:
        value = response.headers.get("Retry-After", "")
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return 2.0
        return max(0.0, seconds)

    def page(self, appid: int, cursor: str, *, page_size: int = 100) -> tuple[list[dict[str, Any]], str, str]:
        params = {
            "appid": appid, "filter": 1, "languages": ["all"], "review_type": 0,
            "purchase_type": 1, "num_per_page": page_size, "cursor": cursor,
            "filter_offtopic_activity": True,
        }
        for attempt in range(self.retries + 1):
            if self.requests >= self.max_requests:
                raise CollectionError("Se alcanzó el presupuesto de solicitudes HTTP.")
            if self._last_started is not None:
                self._pause(max(0.0, self.delay - (self.monotonic() - self._last_started)))
            self._last_started = self.monotonic()
            self.requests += 1
            try:
                response = self.client.get(ENDPOINT, params={"input_json": json.dumps(params, separators=(",", ":"))})
            except httpx.TransportError:
                if attempt == self.retries:
                    raise CollectionError("Steam no respondió después de los reintentos acotados.") from None
                self._pause(2.0 ** (attempt + 1))
                continue
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt == self.retries:
                    raise CollectionError(f"Steam devolvió HTTP {response.status_code}; checkpoint conservado.")
                wait = max(self._retry_after(response), 2.0 ** (attempt + 1))
                # Respect long server delays by stopping instead of retrying early.
                if wait > 120:
                    raise CollectionError("Steam solicita una espera superior a 120s; reanudá más tarde.")
                self._pause(wait)
                continue
            if response.status_code != 200:
                raise CollectionError(f"Steam devolvió HTTP {response.status_code}; sin respuesta cruda registrada.")
            try:
                payload = response.json()
                body = payload.get("response", payload)
                reviews, next_cursor = body["reviews"], body.get("cursor", "")
                if not isinstance(reviews, list) or not all(isinstance(row, dict) for row in reviews) or not isinstance(next_cursor, str):
                    raise ValueError
                # The new API deliberately has no `success` member.
                if body.get("success") not in (None, 1, True):
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError):
                raise CollectionError("La respuesta de Steam no tiene el esquema documentado.") from None
            return reviews, next_cursor, hashlib.sha256(response.content).hexdigest()
        raise AssertionError("unreachable")


def collect(
    appids: list[int], output_dir: Path, client: SteamReviewClient, *,
    max_games: int = 50, pages_per_game: int = 2, max_pages: int = 100,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    if min(max_games, pages_per_game, max_pages) < 1:
        raise ValueError("Los presupuestos de juegos y páginas deben ser positivos.")
    appids = list(dict.fromkeys(appid for value in appids if (appid := positive_appid(value)) is not None))[:max_games]
    if not appids:
        raise ValueError("No hay AppIDs válidos para consultar.")
    store = ObservationStore(output_dir)
    started_at = utc_now()
    totals = Counter(store.checkpoint.get("totals", {}))
    run_counts: Counter[str] = Counter()
    error: str | None = None
    pages = 0
    try:
        for game_index, appid in enumerate(appids, start=1):
            state = store.checkpoint["games"].setdefault(str(appid), {"cursor": "*", "pages": 0, "exhausted": False})
            if state["exhausted"]:
                continue
            for _ in range(pages_per_game):
                if pages >= max_pages:
                    break
                raw_reviews, cursor, response_sha = client.page(appid, state["cursor"])
                retrieved_at = utc_now()
                page_counts: Counter[str] = Counter()
                for raw in raw_reviews:
                    row = normalize_review(raw, appid, store.key, retrieved_at)
                    page_counts["invalid" if row is None else store.merge(row)] += 1
                page_counts["received"] += len(raw_reviews)
                page_counts["pages"] += 1
                totals.update(page_counts)
                run_counts.update(page_counts)
                pages += 1
                state["exhausted"] = not raw_reviews or not cursor or cursor == state["cursor"]
                state["cursor"] = cursor or state["cursor"]
                state["pages"] += 1
                store.checkpoint["totals"] = dict(totals)
                store.checkpoint["page_receipts"].append({
                    "steam_appid": appid, "page": state["pages"], "retrieved_at": retrieved_at,
                    "response_sha256": response_sha, "received": len(raw_reviews),
                })
                store.persist()
                progress(f"[{game_index}/{len(appids)}] AppID {appid}: página {state['pages']}, {len(raw_reviews)} observaciones recibidas.")
                if state["exhausted"]:
                    break
            if pages >= max_pages:
                break
    except (CollectionError, KeyboardInterrupt) as exc:
        error = str(exc) if isinstance(exc, CollectionError) else "Interrumpido por el usuario; se conserva el checkpoint."
    store.persist()
    manifest = {
        "schema_version": SCHEMA_VERSION, "data_policy": DATA_POLICY,
        "source": SOURCE, "endpoint": ENDPOINT, "documentation": DOCUMENTATION,
        "started_at": started_at, "finished_at": utc_now(), "key_id": store.key_id,
        "parameters": {"appids": appids, "max_games": max_games, "pages_per_game": pages_per_game,
                       "max_pages": max_pages, "max_requests": client.max_requests, "delay_seconds": client.delay,
                       "retries": client.retries, "filter": 1, "languages": ["all"], "review_type": 0,
                       "purchase_type": 1, "num_per_page": 100, "filter_offtopic_activity": True},
        "run_counts": dict(run_counts), "cumulative_counts": dict(totals),
        "requests_this_run": client.requests, "summary": store.summary(),
        "observations_sha256": hashlib.sha256(store.data_path.read_bytes()).hexdigest(),
        "status": "partial" if error else "completed_budget", "error": error,
        "limitations": ["Recent reviews from selected games are not a representative player sample.",
                        "Current playtime cannot be used as if known at review time.",
                        "No unreviewed game is a negative label; no training has been performed.",
                        "HMAC aliases are pseudonymous, not irreversible anonymization."],
    }
    atomic_json(output_dir / "manifest.json", manifest)
    return manifest
