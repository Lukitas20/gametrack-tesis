"""Pruebas de humo del modelo de datos.

Usan una base SQLite en memoria, así que no tocan la base de desarrollo.
"""

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models import (
    Aspect,
    Game,
    GameList,
    GameListItem,
    Genre,
    ListType,
    Rating,
    Review,
    ReviewAspect,
    Sentiment,
    Tag,
    User,
    UserPreference,
    UserRole,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


@pytest.fixture
def game(db: Session) -> Game:
    rpg = Genre(slug="rpg", name="RPG")
    tag = Tag(slug="mundo-abierto", name="Mundo abierto")
    game = Game(slug="un-juego", name="Un juego", description="Una descripción.")
    game.genres = [rpg]
    game.tags = [tag]
    db.add(game)
    db.commit()
    return game


@pytest.fixture
def user(db: Session) -> User:
    user = User(username="tester", role=UserRole.PLAYER)
    db.add(user)
    db.commit()
    return user


def test_content_soup_pondera_generos_y_etiquetas(game: Game) -> None:
    soup = game.content_soup
    # Los géneros se repiten para pesar más que la descripción en el TF-IDF.
    assert soup.count("rpg") == 3
    assert soup.count("mundo-abierto") == 2
    assert "descripción" in soup


def test_rol_desarrollador(db: Session) -> None:
    dev = User(username="dev", role=UserRole.DEVELOPER, studio="Valve")
    db.add(dev)
    db.commit()
    assert dev.is_developer
    # El enum se persiste con su valor en español.
    stored = db.execute(select(User.role).where(User.id == dev.id)).scalar_one()
    assert stored is UserRole.DEVELOPER


def test_un_solo_rating_por_usuario_y_juego(db: Session, user: User, game: Game) -> None:
    db.add(Rating(user_id=user.id, game_id=game.id, score=4.0))
    db.commit()
    db.add(Rating(user_id=user.id, game_id=game.id, score=2.0))
    with pytest.raises(IntegrityError):
        db.commit()


def test_score_fuera_de_rango_es_rechazado(db: Session, user: User, game: Game) -> None:
    db.add(Rating(user_id=user.id, game_id=game.id, score=7.0))
    with pytest.raises(IntegrityError):
        db.commit()


def test_review_nace_sin_analizar(db: Session, user: User, game: Game) -> None:
    review = Review(user_id=user.id, game_id=game.id, content="Muy bueno.")
    db.add(review)
    db.commit()
    assert review.is_analyzed is False
    assert review.sentiment is None
    assert review.aspects == []


def test_aspectos_se_borran_con_la_review(db: Session, user: User, game: Game) -> None:
    review = Review(user_id=user.id, game_id=game.id, content="Corre mal.")
    review.aspects = [
        ReviewAspect(
            game_id=game.id,
            aspect=Aspect.PERFORMANCE,
            sentiment=Sentiment.NEGATIVE,
            score=-0.8,
        )
    ]
    db.add(review)
    db.commit()

    db.delete(review)
    db.commit()
    assert db.scalar(select(func.count(ReviewAspect.id))) == 0


def test_borrar_usuario_arrastra_sus_datos(db: Session, user: User, game: Game) -> None:
    genre = db.scalars(select(Genre)).first()
    game_list = GameList(user_id=user.id, name="Favoritos", list_type=ListType.FAVORITES)
    game_list.items = [GameListItem(game_id=game.id)]
    db.add_all(
        [
            UserPreference(user_id=user.id, genre_id=genre.id, weight=0.9),
            Rating(user_id=user.id, game_id=game.id, score=5.0),
            game_list,
        ]
    )
    db.commit()

    db.delete(user)
    db.commit()

    assert db.scalar(select(func.count(Rating.id))) == 0
    assert db.scalar(select(func.count(GameList.id))) == 0
    assert db.scalar(select(func.count(UserPreference.id))) == 0
    # El catálogo no se toca al borrar un usuario.
    assert db.scalar(select(func.count(Game.id))) == 1
