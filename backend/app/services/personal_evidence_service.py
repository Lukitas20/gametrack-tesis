"""Respaldo cualitativo de cada estimación; no simula probabilidades."""


def confidence_for(game, context):
    def result(level, message):
        return {"confidence": level, "confidence_message": message}

    if game.id in context["ratings"]:
        return result("known", "Tu opinión guardada: refleja tu nota de este juego, no una predicción nueva.")
    own = context.get("own_signals", context.get("signals", {})).get(game.id)
    if own and own["source"] != "steam_playtime" and own["weight"] != 0:
        return result("known", "Tu opinión guardada: la afinidad usa tu recomendación o rechazo explícito. El índice también puede incluir recepción pública.")
    if own and own["source"] == "steam_playtime":
        return result("medium" if own["minutes"] >= 6000 else "low",
                      "Se apoya en tus horas verificadas en este juego. Indican interés, pero no confirman que te haya gustado; una opinión tuya permitiría precisarlo.")
    if not context.get("scores"):
        return result("none", "Sin evidencia personal suficiente. Elegí tus gustos o guardá opiniones sobre juegos que conocés.")
    engine = context["engine"]
    index = engine._game_index.get(game.id)
    terms = engine._community_terms_by_game.get(game.id, set()) & engine.personal_specific_terms
    if not game.is_enriched or index is None or not terms:
        return result("low", "Estimación provisional: faltan datos o rasgos específicos de esta ficha para compararla con tus gustos.")
    explicit, implicit = 0, 0
    for gid, signal in context.get("signals", {}).items():
        if gid == game.id or abs(signal["weight"]) < .35:
            continue
        similar = context["similarities"].get(gid)
        if similar is None or float(similar[index]) < .25:
            continue
        if not terms & engine._community_terms_by_game.get(gid, set()):
            continue
        if signal["source"] == "steam_playtime":
            implicit += 1
        else:
            explicit += 1
    if explicit:
        return result("high" if explicit >= 3 else "medium",
                      f"Respaldo de {explicit} opiniones tuyas sobre juegos con rasgos específicos compartidos. Es evidencia de afinidad, no una probabilidad de acertar.")
    if implicit:
        return result("medium", f"Respaldo de {implicit} juegos con horas y rasgos específicos compartidos. Es interés observado; faltan opiniones explícitas para confirmar tus gustos.")
    return result("low", "Estimación provisional: faltan opiniones sobre juegos con rasgos específicos compartidos. Los géneros y la buena crítica solos no confirman afinidad.")
