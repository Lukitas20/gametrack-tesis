"""Vocabulario del asistente "¿Qué jugamos hoy?".

Todo el mapeo entre lo que el usuario responde y lo que el catálogo entiende
vive acá, en código y no en el frontend, por tres razones: es testeable (los
golden tests lo ejercitan), es versionable (cada ajuste queda en el historial
con su motivo), y mantiene una sola fuente de verdad — el frontend manda la
CLAVE de la respuesta ("competir", "amigos") y el backend decide qué significa.

Dos mecanismos distintos a propósito (la distinción central del rediseño):

- **Filtros duros** (``MOOD_REQUIREMENTS``, ``COMPANY_FILTERS``): restricciones
  que un juego cumple o no. Un juego sin cooperativo no aparece con puntaje
  bajo cuando pediste jugar con amigos: no aparece. Operan sobre etiquetas
  binarias — las categorías de plataforma de la tienda (curadas por el
  developer, en español) en unión con las comunitarias equivalentes de
  SteamSpy (en inglés), porque la cobertura de las categorías es incompleta:
  el caso testigo es Counter-Strike 2, que en la tienda dice apenas
  "Multijugador" pero la comunidad etiquetó "PvP" con 34 mil votos.

- **Perfiles blandos** (``MOOD_PROFILES``): preferencias graduales. "Quiero
  relajarme" no excluye nada; acerca lo que la comunidad marcó como Relaxing
  o Cozy. Son vectores de consulta ponderados que el motor compara por
  coseno contra la matriz TF-IDF de etiquetas votadas. Incluyen géneros con
  peso menor como respaldo: le dan señal a los juegos que todavía no pasaron
  por la ingesta de SteamSpy.

Los slugs comunitarios están en inglés (así los publica SteamSpy y así los
guarda la ingesta, ver ``steamspy_service``); los de plataforma en español
(así los traduce la tienda de Steam). Conviven en el mismo espacio de
términos sin colisionar.
"""

from __future__ import annotations

# Peso de un género dentro de un perfil o consulta. Menor que el de las
# etiquetas específicas del ánimo: el género es la señal de respaldo (18
# baldes para miles de juegos), no la protagonista.
GENRE_WEIGHT = 0.4

# term -> peso de consulta, por ánimo. Mezclan etiquetas comunitarias
# (inglés) y géneros (español); el motor resuelve ambos en el mismo espacio.
MOOD_PROFILES: dict[str, dict[str, float]] = {
    "historia": {
        "story-rich": 1.0,
        "narrative": 0.9,
        "choices-matter": 0.7,
        "atmospheric": 0.5,
        "singleplayer": 0.4,
        "rol": GENRE_WEIGHT,
        "aventura": GENRE_WEIGHT,
    },
    "desafio": {
        "difficult": 1.0,
        "souls-like": 0.9,
        "roguelike": 0.7,
        "precision-platformer": 0.6,
        "accion": GENRE_WEIGHT,
        "estrategia": GENRE_WEIGHT,
    },
    "relajarme": {
        "relaxing": 1.0,
        "cozy": 0.9,
        "casual": 0.7,
        "farming-sim": 0.6,
        "sandbox": 0.5,
        "simuladores": GENRE_WEIGHT,
    },
    "competir": {
        "competitive": 1.0,
        "pvp": 1.0,
        "e-sports": 0.8,
        "team-based": 0.5,
        "fps": 0.4,
        "accion": GENRE_WEIGHT,
        "deportes": GENRE_WEIGHT,
        "carreras": GENRE_WEIGHT,
    },
}

# Requisito DURO que el ánimo impone, además del perfil blando. Sólo
# "competir" lo tiene: sin JcJ no hay contra quién competir. Los otros tres
# ánimos son graduales por naturaleza (un juego puede ser "bastante
# relajante") y forzarlos como filtro repetiría el bug original del quiz.
MOOD_REQUIREMENTS: dict[str, frozenset[str]] = {
    "historia": frozenset(),
    "desafio": frozenset(),
    "relajarme": frozenset(),
    "competir": frozenset({"jcj", "jcj-en-linea", "pvp"}),
}

# Filtro duro de compañía: unión de categorías de plataforma (español) y
# etiquetas comunitarias equivalentes (inglés). Basta con tener UNA.
COMPANY_FILTERS: dict[str, frozenset[str]] = {
    "solo": frozenset({"un-jugador", "singleplayer"}),
    "amigos": frozenset(
        {
            # Plataforma
            "cooperativo",
            "cooperativo-en-linea",
            "pantalla-partida-compartida",
            "coop-a-pantalla-com-partida",
            # Comunidad
            "co-op",
            "online-co-op",
            "local-co-op",
            "split-screen",
        }
    ),
    "en-linea": frozenset(
        {
            # Plataforma
            "jcj-en-linea",
            "cooperativo-en-linea",
            "multijugador",
            "multijugador-multiplataforma",
            # Comunidad
            "multiplayer",
            "pvp",
            "online-co-op",
            "massively-multiplayer",
        }
    ),
}

# --- Duración: compromiso vs. sesión ---------------------------------------
#
# La mediana de horas registradas al reseñar NO mide duración de campaña ni
# de sesión. Se usa sólo como aproximación al compromiso, con una nota de
# incertidumbre en cada resultado. Los juegos con señales de partidas o
# servicio quedan exentos porque acumulan horas entre muchas sesiones.

# Etiquetas comunitarias que marcan un juego-servicio de sesión. Deliberada-
# mente NO incluyen "multiplayer" ni "co-op" a secas: It Takes Two es
# cooperativo y finito, Portal 2 tiene co-op y campaña. Tampoco free-to-play:
# un precio gratuito no prueba que el juego sea un servicio.
SERVICE_MARKERS: frozenset[str] = frozenset(
    {
        "massively-multiplayer",
        "mmorpg",
        "moba",
        "battle-royale",
        "e-sports",
    }
)

# Categorías/etiquetas que indican que existe modo de un jugador.
_SINGLEPLAYER_MARKERS = frozenset({"un-jugador", "singleplayer"})


def is_session_based(tag_slugs: set[str]) -> bool:
    """¿Es un juego de sesión (servicio) en vez de una obra que se termina?

    Exige una señal positiva: etiquetas de servicio, o JcJ sin modo de un
    jugador. La ausencia de metadatos, el cooperativo y el precio gratuito
    no bastan para afirmar que un juego se organiza en partidas.
    """
    return bool(tag_slugs & SERVICE_MARKERS) or (
        not tag_slugs & _SINGLEPLAYER_MARKERS
        and bool(tag_slugs & MOOD_REQUIREMENTS["competir"])
    )


def passes_time_budget(
    median_hours: float | None, tag_slugs: set[str], max_hours: int | None
) -> bool:
    """¿Entra el juego en el presupuesto de tiempo del usuario?

    Sin presupuesto, o sin dato de duración, pasa este filtro numérico. El
    asistente declara por separado la incertidumbre y ofrece esos juegos
    como alternativas; su modo estricto exige el dato. Con dato, el juego
    de sesión pasa siempre; el resto, si la mediana entra en la franja.
    Esta regla es una heurística de compromiso, no una estimación de duración.
    """
    if max_hours is None or median_hours is None:
        return True
    if is_session_based(tag_slugs):
        return True
    return median_hours <= max_hours
