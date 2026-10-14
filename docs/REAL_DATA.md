# Datos reales exclusivamente

Desde el 14 de octubre de 2026 se retiró el recomendador entrenado con datos sintéticos.
La interfaz y la API pública ya no ofrecen la estrategia manual `ia_local`.

El servidor excluye reseñas `demo`, `seed`, `synthetic` y cualquier origen distinto de
Steam o una reseña de usuario registrado. También excluye valoraciones, preferencias,
experiencias y aspectos derivados de los perfiles generados por el seed histórico.
Las filas antiguas se conservan; quedan fuera de consultas operativas, recomendaciones,
estadísticas y entrenamiento. Al arrancar se corrigen los contadores derivados de los
juegos afectados, usando sólo las fuentes reales disponibles.

El entrenador y Cloud Run sólo aceptan `observed`. Esta etiqueta no es por sí sola una
garantía: la selección usa la política de procedencia de la base, y los artefactos deben
registrar `data_policy=observed-only-v1`. Un artefacto anterior, desconocido o demo se
bloquea incluso si tiene activación automática o se solicita desde una URL antigua.
Si no hay suficientes observaciones reales, no se fabrica ni publica un reemplazo.

El script `seed_data.py` fue retirado. Para importar catálogo y reseñas reales se usa
`scripts/seed_from_steam.py`; los usuarios se registran por el acceso normal o Steam.
También se retiraron el corpus generador y la evaluación contra etiquetas sintéticas
del seed; `analyze_reviews.py --evaluate` rechaza ese flujo antes de abrir la base.

El modelo de reseñas reales entrenado el 5 de octubre se integra por separado en
el análisis de reseñas, con pesos auditados incluidos en la imagen. No es un LLM
ni reemplaza al recomendador demo con su misma función. La procedencia, sus
límites y el procedimiento de empaquetado se documentan en [IA_REAL.md](IA_REAL.md).

Los fixtures de pruebas unitarias viven en bases temporales y nunca se incorporan al
servicio, al corpus de entrenamiento operativo ni a una evaluación de datos reales.
