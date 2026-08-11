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
# "¿Cuánto tiempo tenés?" pregunta por la sesión de hoy, pero la mediana de
# horas de los reseñadores (`Game.median_review_hours`) mide compromiso
# total. Para un juego finito son lo mismo a efectos del filtro: si la
# mediana de terminar The Witcher 3 es 53 h, no es para una tarde. Para un
# juego-servicio se invierte: CS2 acumula 164 h de mediana justamente porque
# cada sesión es corta y la gente vuelve — es EXACTAMENTE lo que jugás una
# tarde. La distinción, entonces, exime del filtro de duración a los juegos
# de sesión; su "duración" no existe como concepto.

# Etiquetas comunitarias que marcan un juego-servicio de sesión. Deliberada-
# mente NO incluyen "multiplayer" ni "co-op" a secas: It Takes Two es
# cooperativo y finito, Portal 2 tiene co-op y campaña. Sí incluyen
# free-to-play: el modelo de negocio de sesión infinita.
SERVICE_MARKERS: frozenset[str] = frozenset(
    {
        "massively-multiplayer",
        "mmorpg",
        "moba",
        "battle-royale",
        "e-sports",
        "free-to-play",
    }
)

# Categorías/etiquetas que indican que existe modo de un jugador.
_SINGLEPLAYER_MARKERS = frozenset({"un-jugador", "singleplayer"})


def is_session_based(tag_slugs: set[str]) -> bool:
    """¿Es un juego de sesión (servicio) en vez de una obra que se termina?

    Dos señales, cualquiera alcanza:

    1. **No tiene modo de un jugador.** Un juego sin campaña no tiene "tiempo
       de completarlo": sus horas son acumulación de sesiones. Cubre CS2,
       Dota, Apex y todo el multijugador puro, incluso ANTES de la ingesta
       de SteamSpy (la categoría "Un jugador" viene de la tienda).
    2. **La comunidad lo marcó como servicio** (MOBA, battle royale, MMO,
       e-sports, free-to-play). Cubre los híbridos con campaña testimonial.

    El costo de un falso positivo es leve (un juego largo se cuela en "una
    tarde" y el ranking decide); el de un falso negativo es el bug de CS2
    excluido de "una tarde". Por eso la regla es generosa.
    """
    if not tag_slugs & _SINGLEPLAYER_MARKERS:
        return True
    return bool(tag_slugs & SERVICE_MARKERS)


def passes_time_budget(
    median_hours: float | None, tag_slugs: set[str], max_hours: int | None
) -> bool:
    """¿Entra el juego en el presupuesto de tiempo del usuario?

    Sin presupuesto, o sin dato de duración, pasa (comportamiento decidido y
    fijado por golden test: la falta de dato no excluye). Con dato, el juego
    de sesión pasa siempre; el finito, si su compromiso entra en la franja.
    """
    if max_hours is None or median_hours is None:
        return True
    if is_session_based(tag_slugs):
        return True
    return median_hours <= max_hours
