"""Comparación local de dos juegos con el mismo perfil y fuentes verificables."""
from fastapi import HTTPException
from app.models import Game
from app.schemas.game import GameSummary
from app.schemas.gametrack_score import GameComparison, GameComparisonItem
from app.services.friendship_service import require_player
from app.services.game_explanation_service import explain_game
from app.services.gametrack_score_service import score_context


def compare_games(db, user, game_ids):
    require_player(user)
    if len(game_ids) != 2 or any(type(gid) is not int or gid <= 0 for gid in game_ids) or game_ids[0] == game_ids[1]:
        raise HTTPException(422, "Elegí dos juegos distintos del catálogo")
    games = [db.get(Game, gid) for gid in game_ids]
    if any(game is None for game in games):
        raise HTTPException(404, "Uno de los juegos no existe")
    context = score_context(db, user)
    items = [GameComparisonItem(game=GameSummary.model_validate(game),
                               explanation=explain_game(db, user, game.id, _context=context)) for game in games]
    a, b = [item.explanation.score for item in items]
    preferred = None
    if any(score.affinity is None or score.confidence in {"low", "none"} for score in (a, b)):
        conclusion = "Todavía falta evidencia personal comparable para elegir uno con confianza. Revisá los motivos de cada juego y guardá tu opinión sobre otros que conozcas."
    elif abs(a.affinity - b.affinity) <= 5:
        conclusion = "Los dos tienen una afinidad parecida con tu perfil. No hay una ventaja personal clara; compará las modalidades y los puntos a tener en cuenta."
    else:
        preferred = games[0].id if a.affinity > b.affinity else games[1].id
        name = games[0].name if preferred == games[0].id else games[1].name
        conclusion = f"{name} encaja mejor con tu perfil actual según la afinidad personal. Esto no garantiza que te guste ni reemplaza revisar los motivos y la ficha oficial."
    differences = [f"Afinidad personal: {game.name}, {score.affinity if score.affinity is not None else 'sin datos'}/100."
                   for game, score in zip(games, (a, b)) if score.affinity is not None]
    for game, score in zip(games, (a, b)):
        if score.metascore is not None:
            differences.append(f"{game.name}: Metascore {score.metascore}/100. Es recepción crítica, no afinidad personal.")
        if game.genres:
            differences.append(f"{game.name}: {', '.join(genre.name for genre in game.genres[:3])}.")
    return GameComparison(items=items, preferred_game_id=preferred, conclusion=conclusion, differences=differences)
