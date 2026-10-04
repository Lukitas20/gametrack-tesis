"""Afinidad personal: mismo género, opiniones distintas, explicaciones verificables."""
from datetime import datetime, timezone
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.v1.endpoints.recommendations import router
from app.db.database import Base, get_db
from app.ml.recommender import invalidate_engine
from app.models import Game, Genre, Rating, SteamIdentity, SteamProfileCache, Tag, User, UserPreference, game_tags
from app.services import gametrack_score_service as scores, steam_review_service
from app.services.game_explanation_service import explain_game


@pytest.fixture
def history():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        action, strategy = Genre(slug="accion", name="Acción"), Genre(slug="estrategia", name="Estrategia")
        shooter, puzzle = Tag(slug="tactical-shooter", name="Disparos tácticos", kind="community"), Tag(slug="puzzle", name="Puzles", kind="community")
        user, other = User(username="steam", steam_id="76561198000000001"), User(username="otro", steam_id="76561198000000002")
        db.add_all([action, strategy, shooter, puzzle, user, other]); db.flush()
        db.add_all([SteamIdentity(user_id=user.id, steam_id=user.steam_id), SteamIdentity(user_id=other.id, steam_id=other.steam_id)])
        games = [Game(name=f"Juego {i}", slug=f"history-{i}", steam_app_id=100+i,
            genres=[strategy if i == 4 else action], tags=[shooter if i in (0, 2, 4) else puzzle] if i != 5 else [],
            steam_synced_at=datetime.now(timezone.utc), description="Descripción",
            background_image="https://example.test/cover.jpg", metacritic=96,
            steam_total_reviews=20000, steam_positive_reviews=19000) for i in range(6)]
        db.add_all(games); db.flush()
        for account in (user, other):
            db.add(UserPreference(user_id=account.id, genre_id=action.id))
        db.add(SteamProfileCache(user_id=user.id, steam_id=user.steam_id, checked_at=int(time.time()), library={
            "status": "ok", "items": [{"appid": 100, "minutes": 12000}, {"appid": 101, "minutes": 500000}],
            "user_reviews": {"1": {"status": "ok", "checked_at": int(time.time()), "items": [
                {"appid": 100, "is_recommended": True}, {"appid": 101, "is_recommended": False}]}}}, friends={}))
        db.commit(); invalidate_engine()
        yield db, user, other, games, action, strategy
    invalidate_engine(); engine.dispose()


def set_library(db, user, library):
    cache = db.get(SteamProfileCache, user.id)
    cache.library = library
    db.commit()


def test_steam_opiniones_distinguen_juegos_del_mismo_genero(history):
    db, user, _, games, *_ = history
    liked, disliked = [scores.game_score(db, user, games[i].id) for i in (2, 3)]
    assert liked.affinity > disliked.affinity + 50
    assert liked.evidence == "steam" and liked.version == "2.2"
    assert liked.weights == {"affinity": .85, "metacritic": .10, "community": .05}
    assert "reach" not in liked.components
    assert "recomendaste en Steam" in " ".join(liked.reasons)
    assert "no recomendaste en Steam" in " ".join(disliked.reasons)
    assert "Perfil inicial" not in liked.model_dump_json()
    assert "Coincide con tus géneros" not in liked.model_dump_json()
    assert db.scalar(select(func.count()).select_from(Rating)) == 0


def test_mismas_preferencias_no_implican_mismo_puntaje(history):
    db, user, other, games, *_ = history
    library = db.get(SteamProfileCache, user.id).library
    reversed_reviews = {**library, "user_reviews": {"1": {"status": "ok", "items": [
        {"appid": 100, "is_recommended": False}, {"appid": 101, "is_recommended": True}]}}}
    db.add(SteamProfileCache(user_id=other.id, steam_id=other.steam_id, library=reversed_reviews, friends={})); db.commit()
    assert scores.game_score(db, user, games[2].id).affinity > scores.game_score(db, other, games[2].id).affinity + 50


def test_resena_negativa_prevalece_sobre_muchas_horas(history):
    db, user, _, games, *_ = history
    before = scores.game_score(db, user, games[3].id).affinity
    library = db.get(SteamProfileCache, user.id).library
    set_library(db, user, {**library, "items": [{"appid": 101, "minutes": 9000000}]})
    assert scores.game_score(db, user, games[3].id).affinity == before
    assert scores.game_score(db, user, games[1].id).affinity < 20


def test_horas_son_interes_y_no_estrellas(history):
    db, user, _, games, *_ = history
    set_library(db, user, {"status": "ok", "items": [{"appid": 100, "minutes": 6000}]})
    score = scores.game_score(db, user, games[2].id)
    assert score.evidence == "steam" and score.affinity >= 45
    assert "0 reseñas tuyas" in " ".join(score.reasons)
    assert "interés" in " ".join(score.reasons)
    assert "aún no tenemos reseñas" in " ".join(score.reasons)
    assert db.scalar(select(func.count()).select_from(Rating)) == 0
    set_library(db, user, {"status": "ok", "items": [{"appid": 100, "minutes": 0}]})
    assert scores.game_score(db, user, games[2].id).evidence == "inicial"


def test_buena_critica_no_salva_desencaje_y_genero_solo_no_domina(history):
    db, user, _, games, *_ = history
    assert scores.game_score(db, user, games[5].id).affinity < 45
    game = games[3]
    before = scores.game_score(db, user, game.id)
    game.metacritic = 100; game.steam_total_reviews = 900000; game.steam_positive_reviews = 900000; db.commit()
    after = scores.game_score(db, user, game.id)
    assert after.affinity == before.affinity and after.value < 30
    assert scores.game_score(db, user, games[2].id).value > after.value


def test_historial_permite_afinidad_fuera_del_genero_y_excluye_poseidos(history):
    db, user, _, games, *_ = history
    result = scores.discovery(db, user)
    ids = {item.game.id for item in result.items}
    assert games[4].id in ids  # Estrategia, comparte disparos tácticos
    assert not {games[i].id for i in (0, 1, 3, 5)} & ids
    assert result.personal_data and result.history_size == 2
    assert all(item.gametrack_score.model_dump() == scores.game_score(db, user, item.game.id).model_dump() for item in result.items)


@pytest.mark.parametrize("state", ["private", "unavailable", "unverified", "wrong_identity"])
def test_no_usa_historial_privado_ajeno_o_no_verificado(history, state):
    db, user, _, games, *_ = history
    cache = db.get(SteamProfileCache, user.id)
    if state in {"private", "unavailable"}:
        library = cache.library
        cache.library = {**library, "status": state, "user_reviews": {"1": {"status": state, "items": library["user_reviews"]["1"]["items"]}}}
    elif state == "unverified":
        db.delete(user.steam_identity)
    else:
        cache.steam_id = "76561198000000099"
    db.commit(); db.expire(user)
    assert scores.game_score(db, user, games[2].id).evidence == "inicial"
    assert scores.score_context(db, user)["steam_count"] == 0


def test_reseñas_publicas_funcionan_con_biblioteca_privada(history):
    db, user, _, games, *_ = history
    library = db.get(SteamProfileCache, user.id).library
    set_library(db, user, {**library, "status": "private"})
    assert scores.game_score(db, user, games[2].id).evidence == "steam"
    assert scores.score_context(db, user)["steam_played_count"] == 0


def test_nota_manual_prevalece_y_no_duplica_opiniones(history):
    db, user, _, games, *_ = history
    db.add(Rating(user_id=user.id, game_id=games[0].id, score=1)); db.commit(); invalidate_engine()
    score = scores.game_score(db, user, games[2].id)
    assert score.affinity < 20
    assert scores.game_score(db, user, games[0].id).value == 0
    assert scores.score_context(db, user)["steam_review_count"] == 1
    assert scores.score_context(db, user)["history_size"] == 2


def test_explicacion_cita_mismas_opiniones_steam_y_no_genero_como_evidencia(history):
    db, user, _, games, *_ = history
    result = explain_game(db, user, games[3].id, "Comparalo con mi historial")
    assert result.score.model_dump() == scores.game_score(db, user, games[3].id).model_dump()
    assert "no recomendaste en Steam" in " ".join(p.text for p in result.cautions)
    assert any(ref.url == f"https://steamcommunity.com/profiles/{user.steam_id}/recommended/101/" for ref in result.references)
    assert "géneros que elegiste" not in result.model_dump_json()
    assert "con pocas valoraciones" not in result.model_dump_json()
    assert "no recomendaste en Steam" in " ".join(p.text for p in result.answer)


def test_api_importa_resenas_sin_abrir_valoraciones_y_reutiliza_cache(history, monkeypatch):
    db, user, _, games, *_ = history
    library = db.get(SteamProfileCache, user.id).library
    set_library(db, user, {**library, "user_reviews": {}})
    fetched = []
    def fetch(sid, page):
        assert sid == user.steam_id
        fetched.append(page)
        return {"status": "ok", "items": [{"appid": 99 + page, "is_recommended": page == 1}],
                "total": 2, "next_page": 2 if page == 1 else None}
    monkeypatch.setattr(steam_review_service, "fetch_reviews", fetch)
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get(f"/recommendations/game/{games[2].id}/score")
            assert response.status_code == 200 and response.json()["evidence"] == "steam"
            assert response.headers["cache-control"] == "no-store"
            assert "2 reseñas tuyas" in " ".join(response.json()["reasons"])
    assert fetched == [1, 2]


def test_solo_rechazos_no_se_transforman_en_recomendaciones_positivas(history):
    db, user, _, games, *_ = history
    user.preferences.clear()
    set_library(db, user, {"status": "ok", "items": [], "user_reviews": {"1": {"status": "ok", "items": [
        {"appid": 100, "is_recommended": False}]}}})
    assert scores.discovery(db, user).items == []
    assert scores.game_score(db, user, games[2].id).affinity < 20


def test_votos_recuperan_rasgos_que_el_seed_marco_como_plataforma(history):
    db, user, _, games, *_ = history
    tag = games[0].tags[0]
    tag.kind = "platform"
    db.execute(update(game_tags).where(game_tags.c.tag_id == tag.id).values(votes=100))
    db.commit(); invalidate_engine()
    result = scores.game_score(db, user, games[2].id)
    assert result.affinity > 70
    assert "Disparos tácticos" in " ".join(result.reasons)
    assert tag.kind == "platform"  # no reclasifica categorías de la tienda


def test_software_no_es_interes_en_juegos(history):
    db, user, _, games, *_ = history
    software = Genre(slug="audio-production", name="Audio Production")
    games[0].genres = [software]
    set_library(db, user, {"status": "ok", "items": [{"appid": 100, "minutes": 600000}]})
    context = scores.score_context(db, user)
    assert context["steam_played_count"] == 0


def test_gustos_inferidos_no_duplican_historial_y_manuales_son_secundarios(history):
    db, user, _, games, action, strategy = history
    user.preferences_source = "steam"; db.commit()
    before = scores.game_score(db, user, games[2].id).affinity
    db.add(UserPreference(user_id=user.id, genre_id=strategy.id)); db.commit(); db.expire(user, ["preferences"])
    assert scores.game_score(db, user, games[2].id).affinity == before
    user.preferences_source = "manual"; db.commit()
    assert abs(scores.game_score(db, user, games[2].id).affinity - before) <= 5


def test_genero_amplio_solo_no_genera_afinidad_alta(history):
    db, user, _, games, *_ = history
    set_library(db, user, {"status": "ok", "items": []})
    result = scores.game_score(db, user, games[2].id)
    assert result.evidence == "inicial" and result.affinity <= 55
    assert result.value < 65  # Metascore 96 no transforma la señal débil en fuerte.


def test_1500_horas_reconocen_interes_en_el_propio_juego_y_en_shooters(history):
    db, user, _, games, *_ = history
    set_library(db, user, {"status": "ok", "items": [{"appid": 100, "minutes": 90000}]})
    own, similar, unrelated = [scores.game_score(db, user, games[i].id) for i in (0, 2, 3)]
    assert own.affinity >= 85 and own.value >= 85
    assert similar.affinity >= 65 and similar.affinity > unrelated.affinity + 40
    assert "1500.0 h a este juego" in " ".join(own.reasons)
    assert "steam_playtime" == scores.score_context(db, user)["signals"][games[0].id]["source"]
    assert db.scalar(select(func.count()).select_from(Rating)) == 0
    explanation = explain_game(db, user, games[0].id, "¿Por qué con tantas horas tengo este score?")
    assert explanation.score.model_dump() == own.model_dump()
    assert "1500.0 h" in " ".join(p.text for p in explanation.answer)
    assert any(p.text == own.reasons[0] for p in explanation.positives)


def test_una_resena_de_otro_genero_no_apaga_cientos_de_horas_en_shooters(history):
    db, user, _, games, *_ = history
    library = {"status": "ok", "items": [{"appid": 100, "minutes": 90000}]}
    set_library(db, user, library)
    before = scores.game_score(db, user, games[2].id).affinity
    set_library(db, user, {**library, "user_reviews": {"1": {"status": "ok", "items": [
        {"appid": 101, "is_recommended": True}]}}})
    after = scores.game_score(db, user, games[2].id).affinity
    assert after >= 65 and abs(after - before) <= 5
    # Conservar varios gustos es útil: la reseña positiva de puzles sigue contando.
    assert scores.game_score(db, user, games[3].id).affinity >= 65


def test_horas_crecen_gradualmente_sin_convertir_dos_horas_en_amor(history):
    db, user, _, games, *_ = history
    own, similar = [], []
    for hours in (2, 100, 500, 1500, 15000):
        set_library(db, user, {"status": "ok", "items": [{"appid": 100, "minutes": hours * 60}]})
        own.append(scores.game_score(db, user, games[0].id).affinity)
        similar.append(scores.game_score(db, user, games[2].id).affinity)
    assert own[0] < 35 and similar[0] < 35
    assert own[0] < own[1] < own[2] < own[3] == own[4] < 100
    assert similar[0] < similar[1] < similar[2] < similar[3] == similar[4] < 100
    assert own[3] - own[2] < own[2] - own[1]  # rendimiento decreciente


def test_opiniones_negativas_y_estrellas_prevalecen_sobre_1500_horas(history):
    db, user, _, games, *_ = history
    library = {"status": "ok", "items": [{"appid": 100, "minutes": 90000}],
               "user_reviews": {"1": {"status": "ok", "items": [{"appid": 100, "is_recommended": False}]}}}
    set_library(db, user, library)
    assert scores.game_score(db, user, games[0].id).affinity == 0
    assert scores.game_score(db, user, games[2].id).affinity < 20
    db.add(Rating(user_id=user.id, game_id=games[0].id, score=3)); db.commit(); invalidate_engine()
    assert scores.game_score(db, user, games[0].id).value == 50
    assert scores.game_score(db, user, games[0].id).evidence == "valoracion_propia"


def test_horas_propias_no_requieren_etiquetas_pero_no_inventan_similitudes(history):
    db, user, _, games, *_ = history
    set_library(db, user, {"status": "ok", "items": [{"appid": 105, "minutes": 90000}]})
    assert scores.game_score(db, user, games[5].id).affinity >= 85
    assert scores.game_score(db, user, games[2].id).affinity < 45


def test_biblioteca_grande_no_pierde_horas_propias_fuera_de_las_40_comparaciones(history):
    db, user, _, games, *_ = history
    extra = [Game(name=f"Otro {i}", slug=f"extra-{i}", steam_app_id=200+i,
                  genres=games[1].genres, tags=games[1].tags) for i in range(40)]
    db.add_all(extra); db.commit(); invalidate_engine()
    set_library(db, user, {"status": "ok", "items": [{"appid": 100, "minutes": 90000}]
        + [{"appid": 200+i, "minutes": 100000+i} for i in range(40)]})
    context = scores.score_context(db, user)
    assert context["steam_played_count"] == 40
    assert scores.score_game(games[0], context).affinity >= 85
    assert "1500.0 h a este juego" in " ".join(scores.score_game(games[0], context).reasons)
