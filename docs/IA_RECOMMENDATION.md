# IA de afinidad entrenada con observaciones reales

La IA de afinidad es un modelo distinto del [clasificador de reseñas del
desarrollador](IA_REAL.md). Aprende la relación entre el historial de una persona,
los atributos de un juego y el voto real que esa persona emitió en Steam. Su
salida aporta al motor de recomendaciones y al GameTrackScore; no sustituye el
historial personal ni convierte la recepción pública en gusto individual.

El clasificador del desarrollador analiza textos de reseñas. Este segundo modelo
no genera texto ni necesita un servicio de IA externo: ejecuta pesos numéricos en
el mismo servidor de GameTrack, en CPU. La política es [datos reales
exclusivamente](REAL_DATA.md).

## Observaciones y privacidad

El recolector `backend/scripts/collect_steam_training_data.py` utiliza el endpoint
público de reseñas de Steam. Conserva el voto positivo o negativo, AppID, fecha de
la reseña y horas observadas disponibles. Cada autor se vincula entre juegos con
un HMAC privado; la respuesta cruda, el nombre y el SteamID no se almacenan en el
corpus normalizado. El HMAC es seudonimización, no anonimización irreversible.

Los archivos `observations.jsonl`, `checkpoint.json`, `manifest.json` y la clave
`.author_hmac.key` pertenecen al directorio privado del recolector. No se cargan
como cuentas o reseñas de GameTrack y no forman parte del contenedor o Git. La
clave no se publica ni se incluye en el artefacto. La clave original se conserva
en privado para poder reanudar una recolección sin cambiar las identidades.

Cada reseña cuenta una sola vez por autor y juego. Una actualización reemplaza
el estado anterior; no se suma como una nueva persona. El manifiesto registra
la huella de las observaciones, los parámetros de recolección y los comprobantes
de las páginas de Steam. La huella identifica un archivo; por sí sola no prueba
que cada observación haya sido validada manualmente.

## Qué aprende y qué no se inventa

La etiqueta es el voto real de Steam: recomienda o no recomienda ese juego.
Las características del perfil se calculan a partir de las interacciones
anteriores del autor y los atributos del catálogo. No se utilizan los textos de
la reseña objetivo ni su voto para construir ese perfil.

- Un juego sin reseña no se transforma en un ejemplo negativo.
- Las horas no se convierten en estrellas ni sustituyen el voto del autor.
- Las horas actuales no se presentan como si se conocieran al escribir una
  reseña histórica.
- No se generan autores, reseñas, votos ni interacciones para ampliar el corpus.
- Las horas reales del usuario de GameTrack pueden orientar el motor personal,
  con su significado de interés y uso, sin afirmar que siempre implican gusto.

El GameTrackScore sigue siendo un índice de compatibilidad. No debe presentarse
como un porcentaje de probabilidad de compra satisfactoria ni como la exactitud
del modelo. Los datos de popularidad y recepción de la crítica conservan su
significado separado.

## Evaluación y activación

El entrenamiento reserva autores para validación y prueba. Las reseñas de un
mismo autor no pueden aparecer en el ajuste de pesos y en la prueba final. Para
predecir un voto se utiliza sólo el historial anterior del autor y se informa la
cantidad de ejemplos con historia suficiente. Las fechas ausentes o ambiguas
no se sustituyen por una fecha inventada.

La referencia incluye predicción por popularidad/recepción del juego. Se
comparan métricas de clasificación y pérdida, teniendo presente el desequilibrio
entre votos positivos y negativos. No basta obtener una exactitud alta en una
muestra donde casi todos recomiendan. La prueba reservada no debe utilizarse
para elegir repetidamente hiperparámetros hasta encontrar una mejora.

La carga exige `source=steam_public_review_api`, `data_label=observed` y
`data_policy=observed-only-v1`, un esquema compatible y evaluación elegible. Un
modelo ausente, incompatible o no aprobado no bloquea las recomendaciones: el
motor existente sigue funcionando y no se inventa una predicción de IA.

## Artefactos y publicación

- `backend/app/ml/steam_affinity_model.py`: extracción de atributos, perfiles,
  carga de pesos e inferencia; comparte el mismo esquema con el entrenador.
- `backend/scripts/train_steam_affinity.py`: entrenamiento y evaluación offline,
  sin escribir en la base de GameTrack.
- `backend/models/steam_affinity.json`: coeficientes, interceptos, normalización,
  vocabulario agregado de atributos, procedencia, huellas, evaluación y límites.
  No contiene pickle, identidades o historial por autor. Incluye variantes
  evaluadas por separado para preferencias explícitas y para historial con horas.
- `backend/scripts/verify_steam_affinity_model.py`: verifica el artefacto aprobado,
  hash exacto, procedencia, dimensiones y pesos finitos antes del
  build. Cambiar una etiqueta no aprueba otro entrenamiento.

`STEAM_AFFINITY_MODEL_PATH` apunta por defecto a `backend/models/steam_affinity.json`;
en el contenedor utiliza `/app/backend/models/steam_affinity.json`.
`REVIEW_MODEL_PATH` sigue apuntando al modelo de reseñas del desarrollador.
`LOCAL_MODEL_PATH` pertenece al recomendador anterior y no se reutiliza para
activar sus artefactos demo. Cada modelo conserva su estado y procedencia.

Desde `backend` se verifica el empaquetado sin red ni base de datos:

```bash
python scripts/verify_review_model.py
python scripts/verify_steam_affinity_model.py
```

Para reproducir el entrenamiento se requieren los archivos privados originales;
el catálogo real se conserva como `backend/data/steam_affinity_catalog.json`.
No se necesita ni se solicita la clave HMAC para ajustar los pesos:

```bash
python scripts/train_steam_affinity.py \
  --observations /ruta/privada/observations.jsonl \
  --manifest /ruta/privada/manifest.json \
  --catalog data/steam_affinity_catalog.json \
  --output data/generated/steam_affinity.json
```

El resultado se revisa antes de sustituir el archivo de `backend/models`; un
entrenamiento que no supera su evaluación no se habilita sólo por existir.

Docker ejecuta ambos verificadores durante la construcción. El entrenamiento
ocurre fuera del servicio web y no se ejecuta al arrancar ni con cada consulta.
El corpus privado y sus claves no se necesitan para inferencia. Los cambios de
modelo requieren revisar la evaluación y actualizar los hashes aprobados.

Antes de enviar fuentes a Cloud Build, auditar también el listado efectivo de
subida; `.gcloudignore` incluye las exclusiones de Git y Docker:

```bash
gcloud meta list-files-for-upload > /tmp/gametrack-upload.txt
python deploy/google-cloud/verify_source_upload.py --files-list /tmp/gametrack-upload.txt
```

Este comando se ejecuta desde la raíz del repositorio. Rechaza corpus, claves,
entornos, bases, estado de Terraform y artefactos no aprobados; también detecta
si alguno de los modelos o verificadores falta en la subida.

## Artefacto aprobado el 14 de octubre de 2026

El corpus contiene 110.499 votos reales de Steam, de 107.305 autores seudónimos
y 50 juegos: 88.844 positivos y 21.655 negativos. Incluye páginas recientes de
todas las opiniones y páginas adicionales filtradas por votos negativos reales.
Los cursores son independientes y los duplicados se eliminan. Esta selección
busca más rechazos observados; su distribución no representa la prevalencia de
gustos de todos los usuarios de Steam. No se crearon negativos artificiales.

Sólo 3.140 objetivos tienen un historial anterior comparable: 2.466 se usan para
entrenar, 340 para validación y 334 para prueba. Los restantes registros del
corpus no se presentan como pares de entrenamiento personal. La partición por
autor se mantiene fija; la prueba no intervino en elegir pesos ni activación.

| Canal | Activación | AUC validación | AUC prueba |
| --- | --- | --- | --- |
| Opiniones explícitas | Aprobado | 0,762386 | 0,757248 |
| Sólo horas | Desactivado: no supera los criterios | 0,519706 | 0,571389 |

El canal explícito supera en validación las referencias de prior, similitud
calibrada y recepción por juego, con mejora de pérdida de al menos 0,005 y sin
empeorar Brier. La AUC mide discriminación en la muestra reservada; no significa
que se acierte ese porcentaje de recomendaciones individuales. El modelo usa
24 comparaciones derivadas del historial y ajusta sus coeficientes en CPU.

Cuando sólo hay horas, continúa el motor de interés personal; no se afirma que
el modelo aprobado haya aprendido a deducir disfrute a partir de ellas. Las
opiniones reales de Steam, GameTrack y devoluciones explícitas sí pueden activar
el canal aprobado. El aporte tiene un máximo del 25% del componente de afinidad,
depende del respaldo disponible y nunca sustituye una opinión sobre el propio
juego. La ficha informa el porcentaje efectivo del índice base.

La muestra no representa a todos los jugadores ni cubre todo el catálogo; las
personas que escriben reseñas son una selección de quienes juegan. Las opiniones
múltiples y negativas son valiosas para comprobar afinidad, pero no se fabrican
para equilibrar la muestra.

El historial reciente puede haber sido modificado después de una reseña antigua;
las horas al momento de la consulta no solucionan esa limitación. Los géneros y
las etiquetas del catálogo también describen el catálogo recolectado, no una
fotografía histórica perfecta. La evaluación debe informar esas restricciones.
La calidad se debe reevaluar con más observaciones reales y devoluciones de
usuarios de GameTrack; no se promete precisión individual a partir del tamaño
del corpus.
