"""Gestión de las listas de juegos de un usuario."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GameList, GameListItem, ListType, User

# Listas que todo usuario tiene desde el registro.
SYSTEM_LISTS: tuple[tuple[ListType, str, str], ...] = (
    (ListType.FAVORITES, "Favoritos", "Mis juegos preferidos."),
    (ListType.PLAYING, "Jugando", "Lo que estoy jugando ahora."),
    (ListType.BACKLOG, "Pendientes", "Juegos que quiero jugar."),
)


def ensure_system_lists(db: Session, user: User) -> None:
    """Crea las listas del sistema que le falten al usuario.

    Se llama al consultar las listas para que las cuentas creadas por la API
    (que no pasan por el seed) también las tengan.
    """
    existing = {
        game_list.list_type
        for game_list in db.scalars(select(GameList).where(GameList.user_id == user.id))
    }
    missing = [
        GameList(user_id=user.id, name=name, description=description, list_type=list_type)
        for list_type, name, description in SYSTEM_LISTS
        if list_type not in existing
    ]
    if missing:
        db.add_all(missing)
        db.commit()


def list_user_lists(db: Session, user: User) -> list[GameList]:
    ensure_system_lists(db, user)
    return list(
        db.scalars(
            select(GameList)
            .where(GameList.user_id == user.id)
            .order_by(GameList.list_type, GameList.name)
        )
    )


def get_user_list(db: Session, user: User, list_id: int) -> GameList | None:
    return db.scalar(
        select(GameList).where(GameList.id == list_id, GameList.user_id == user.id)
    )


def create_list(db: Session, user: User, name: str, description: str | None, is_public: bool) -> GameList:
    game_list = GameList(
        user_id=user.id,
        name=name,
        description=description,
        list_type=ListType.CUSTOM,
        is_public=is_public,
    )
    db.add(game_list)
    db.commit()
    db.refresh(game_list)
    return game_list


def add_game(db: Session, game_list: GameList, game_id: int, note: str | None = None) -> GameList:
    """Agrega un juego al final de la lista. Si ya está, no hace nada."""
    already_there = db.scalar(
        select(GameListItem).where(
            GameListItem.list_id == game_list.id, GameListItem.game_id == game_id
        )
    )
    if already_there is None:
        next_position = (
            db.scalar(
                select(func.max(GameListItem.position)).where(
                    GameListItem.list_id == game_list.id
                )
            )
            or 0
        ) + 1
        db.add(
            GameListItem(
                list_id=game_list.id,
                game_id=game_id,
                position=next_position,
                note=note,
            )
        )
        db.commit()
    db.refresh(game_list)
    return game_list


def remove_game(db: Session, game_list: GameList, game_id: int) -> GameList:
    item = db.scalar(
        select(GameListItem).where(
            GameListItem.list_id == game_list.id, GameListItem.game_id == game_id
        )
    )
    if item is not None:
        db.delete(item)
        db.commit()
    db.refresh(game_list)
    return game_list


def lists_containing(db: Session, user: User, game_id: int) -> list[int]:
    """Ids de las listas del usuario que contienen un juego dado."""
    return list(
        db.scalars(
            select(GameList.id)
            .join(GameListItem, GameListItem.list_id == GameList.id)
            .where(GameList.user_id == user.id, GameListItem.game_id == game_id)
        )
    )
