"""Enumeraciones del dominio.

Los valores se guardan en la base en español porque son los mismos que
consume el frontend y los que se muestran en los dashboards de la tesis.
"""

from enum import Enum


class UserRole(str, Enum):
    """Los dos roles del sistema definidos en el alcance de la tesis."""

    PLAYER = "jugador"
    DEVELOPER = "desarrollador"


class GameStatus(str, Enum):
    """Estado de un juego dentro de la biblioteca de un usuario."""

    WISHLIST = "pendiente"
    PLAYING = "jugando"
    COMPLETED = "completado"
    ABANDONED = "abandonado"


class Sentiment(str, Enum):
    """Clasificación de sentimiento del módulo NLP."""

    POSITIVE = "positivo"
    NEUTRAL = "neutro"
    NEGATIVE = "negativo"


class Aspect(str, Enum):
    """Aspectos evaluados por el módulo ABSA."""

    GAMEPLAY = "jugabilidad"
    GRAPHICS = "graficos"
    STORY = "historia"
    PERFORMANCE = "optimizacion"


class ListType(str, Enum):
    """Tipo de lista. Las tres primeras se crean automáticamente por usuario."""

    FAVORITES = "favoritos"
    BACKLOG = "pendientes"
    PLAYING = "jugando"
    CUSTOM = "personalizada"


class RecommendationSource(str, Enum):
    """Estrategia que originó una recomendación (para explicabilidad)."""

    CONTENT = "contenido"
    COLLABORATIVE = "colaborativo"
    HYBRID = "hibrido"
    POPULARITY = "popularidad"
